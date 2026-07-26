import asyncio
import csv
import hashlib
import io
import json
import re
import zipfile
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Any, Literal, cast
from urllib.parse import urlsplit
from uuid import UUID

from psycopg.errors import UniqueViolation
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.core.config import Settings
from app.services.export_storage import ExportStorage
from app.services.platform_operations import (
    complete_idempotency,
    require_platform_admin,
    reserve_idempotency,
    write_platform_event,
)

EXPORT_SCHEMA_VERSION = "2026-07-26.v1"
EXPORT_FORMAT = "zip_json_csv"
EXPORT_CONTENT_TYPE = "application/zip"
SENSITIVE_KEY_PATTERN = re.compile(
    r"(authorization|ciphertext|database_url|password|queue_token|secret|service_role|token)",
    re.IGNORECASE,
)


class ExportSubjectNotFoundError(Exception):
    """The requested business/shop subject does not exist."""


class ExportStateConflictError(Exception):
    """The export is not in the required lifecycle state."""


class ExportExpiredError(Exception):
    """The export delivery window has expired."""


class OffboardingStateConflictError(Exception):
    """The offboarding transition is not permitted."""


class ExportStorageUnavailableError(Exception):
    """Private export storage is unavailable."""


class ExportSubjectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    business_id: UUID
    shop_id: UUID | None = None
    scope: Literal["business", "shop"]

    @model_validator(mode="after")
    def validate_scope(self) -> "ExportSubjectRequest":
        if self.scope == "business" and self.shop_id is not None:
            raise ValueError("business export cannot include shop_id")
        if self.scope == "shop" and self.shop_id is None:
            raise ValueError("shop export requires shop_id")
        return self


class OffboardingRequest(ExportSubjectRequest):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    reason: str = Field(min_length=1, max_length=500)


class TenantExportResponse(BaseModel):
    export_id: UUID
    business_id: UUID
    shop_id: UUID | None
    scope: Literal["business", "shop"]
    status: str
    schema_version: str


class OffboardingResponse(BaseModel):
    case_id: UUID
    export_id: UUID
    business_id: UUID
    shop_id: UUID | None
    scope: Literal["business", "shop"]
    state: str


class ExportDownloadResponse(BaseModel):
    export_id: UUID
    download_url: str = Field(repr=False)
    expires_at: datetime


class ExportDeliveryResponse(BaseModel):
    export_id: UUID
    status: str


class OffboardingArchiveResponse(BaseModel):
    case_id: UUID
    business_id: UUID
    shop_id: UUID | None
    state: str


class ExportActionRequest(BaseModel):
    export_id: UUID


class OffboardingActionRequest(BaseModel):
    case_id: UUID


@dataclass(frozen=True)
class ExportDataset:
    name: str
    columns: tuple[str, ...]
    rows: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class ClaimedExport:
    export_id: UUID
    business_id: UUID
    shop_id: UUID | None
    scope: Literal["business", "shop"]
    attempt_count: int


def _json_default(value: object) -> str:
    if isinstance(value, (datetime, Decimal, UUID)):
        return str(value)
    if isinstance(value, (date, time)):
        return value.isoformat()
    return str(value)


def _sanitize(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): ("[REDACTED]" if SENSITIVE_KEY_PATTERN.search(str(key)) else _sanitize(item))
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_sanitize(item) for item in value]
    return value


def _json_bytes(value: object) -> bytes:
    return (
        json.dumps(
            value,
            default=_json_default,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode()


def _csv_cell(value: Any) -> Any:
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(
            _sanitize(value),
            default=_json_default,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    return value


def build_export_archive(
    *,
    subject: ExportSubjectRequest,
    datasets: list[ExportDataset],
    generated_at: datetime,
) -> bytes:
    manifest = {
        "schema_version": EXPORT_SCHEMA_VERSION,
        "format": EXPORT_FORMAT,
        "generated_at": generated_at.astimezone(UTC).isoformat(),
        "subject": {
            "scope": subject.scope,
            "business_id": str(subject.business_id),
            "shop_id": str(subject.shop_id) if subject.shop_id is not None else None,
        },
        "datasets": [
            {
                "name": dataset.name,
                "columns": list(dataset.columns),
                "row_count": len(dataset.rows),
            }
            for dataset in datasets
        ],
        "redactions": [
            "bot token ciphertext",
            "webhook secret hashes",
            "public queue token hashes",
            "internal idempotency and outbox rows",
            "credential-like keys in audit payloads",
        ],
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", _json_bytes(manifest))
        for dataset in datasets:
            rows = [_sanitize(row) for row in dataset.rows]
            archive.writestr(
                f"{dataset.name}.json",
                _json_bytes(
                    {
                        "schema_version": EXPORT_SCHEMA_VERSION,
                        "dataset": dataset.name,
                        "rows": rows,
                    }
                ),
            )
            csv_buffer = io.StringIO(newline="")
            writer = csv.DictWriter(csv_buffer, fieldnames=dataset.columns, extrasaction="ignore")
            writer.writeheader()
            for row in rows:
                writer.writerow({column: _csv_cell(row.get(column)) for column in dataset.columns})
            archive.writestr(f"{dataset.name}.csv", csv_buffer.getvalue().encode())
    return buffer.getvalue()


async def _fetch_dataset(
    connection: Any,
    *,
    name: str,
    query: str,
    params: tuple[Any, ...],
) -> ExportDataset:
    cursor = await connection.execute(query, params)
    description = cursor.description or ()
    columns = tuple(str(column.name) for column in description)
    rows = tuple(dict(zip(columns, row, strict=True)) for row in await cursor.fetchall())
    return ExportDataset(name=name, columns=columns, rows=rows)


async def _load_export_datasets(
    connection: Any,
    *,
    business_id: UUID,
    shop_id: UUID | None,
    scope: Literal["business", "shop"],
) -> list[ExportDataset]:
    shop_filter = "s.business_id = %s" if scope == "business" else "s.id = %s"
    shop_param = business_id if scope == "business" else shop_id
    membership_filter = "sm.business_id = %s" if scope == "business" else "sm.shop_id = %s"
    bot_filter = "b.business_id = %s" if scope == "business" else "b.shop_id = %s"
    subscription_filter = (
        "s.business_id = %s"
        if scope == "business"
        else "s.business_id = %s and (s.shop_id = %s or s.scope = 'business')"
    )
    subscription_params: tuple[Any, ...] = (
        (business_id,) if scope == "business" else (business_id, shop_id)
    )
    receipt_filter = "r.business_id = %s" if scope == "business" else "r.shop_id = %s"
    audit_filter = "a.business_id = %s" if scope == "business" else "a.shop_id = %s"

    specs = [
        (
            "business",
            """
            select id, legal_name, display_name, trade_license_number,
                   trade_license_expiry, vat_registered, trn, invoice_address,
                   contact_name, contact_phone, contact_email, billing_mode::text,
                   timezone, currency, status::text, created_at, updated_at, archived_at
            from public.businesses
            where id = %s
            order by id
            """,
            (business_id,),
        ),
        (
            "business_owners",
            """
            select bo.business_id, bo.auth_user_id, bo.telegram_user_id,
                   bo.is_primary, bo.active, bo.created_at,
                   up.display_name, up.phone
            from public.business_owners bo
            join public.user_profiles up on up.auth_user_id = bo.auth_user_id
            where bo.business_id = %s
            order by bo.auth_user_id
            """,
            (business_id,),
        ),
        (
            "shops",
            f"""
            select s.id, s.business_id, s.name, s.internal_code, s.timezone,
                   s.currency, s.status::text, s.open_time, s.close_time,
                   s.default_service_minutes, s.eod_time, s.settings,
                   s.created_at, s.updated_at, s.archived_at
            from public.shops s
            where {shop_filter}
            order by s.id
            """,
            (shop_param,),
        ),
        (
            "shop_memberships",
            f"""
            select sm.id, sm.business_id, sm.shop_id, sm.auth_user_id,
                   sm.telegram_user_id, sm.role::text, sm.display_name,
                   sm.phone, sm.active, sm.created_at, sm.updated_at
            from public.shop_memberships sm
            where {membership_filter}
            order by sm.shop_id, sm.id
            """,
            (shop_param,),
        ),
        (
            "bots",
            f"""
            select b.id, b.business_id, b.shop_id, b.role::text,
                   b.bot_username, b.healthy, b.last_health_at, b.created_at
            from public.bots b
            where {bot_filter}
            order by b.shop_id nulls first, b.role
            """,
            (shop_param,),
        ),
        (
            "subscriptions",
            f"""
            select s.id, s.business_id, s.shop_id, s.scope::text, s.status::text,
                   s.paid_from, s.paid_until, s.manual_override_until,
                   s.manual_override_reason, s.suspended_reason::text,
                   s.suspended_at, s.resumed_at, s.created_at, s.updated_at
            from public.subscriptions s
            where {subscription_filter}
            order by s.id
            """,
            subscription_params,
        ),
        (
            "subscription_cash_receipts",
            f"""
            select r.id, r.subscription_id, r.business_id, r.shop_id,
                   r.amount, r.currency, r.receipt_reference, r.receipt_sequence,
                   r.collected_at, r.coverage_from, r.coverage_until,
                   r.evidence_note, r.reversal_of_id, r.created_at
            from public.subscription_cash_receipts r
            where {receipt_filter}
            order by r.receipt_sequence
            """,
            (business_id if scope == "business" else shop_id,),
        ),
        (
            "audit_log",
            f"""
            select a.id, a.business_id, a.shop_id, a.actor_type::text,
                   a.actor_id, a.action, a.entity_type, a.entity_id,
                   a.request_id, a.before, a.after, a.created_at
            from public.audit_log a
            where {audit_filter}
            order by a.created_at, a.id
            """,
            (business_id if scope == "business" else shop_id,),
        ),
    ]
    return [
        await _fetch_dataset(
            connection,
            name=name,
            query=query,
            params=params,
        )
        for name, query, params in specs
    ]


async def _validate_subject(
    connection: Any,
    *,
    business_id: UUID,
    shop_id: UUID | None,
    scope: Literal["business", "shop"],
    lock: bool,
) -> tuple[str, str | None]:
    lock_clause = "for update" if lock else ""
    cursor = await connection.execute(
        f"""
        select status::text
        from public.businesses
        where id = %s
        {lock_clause}
        """,
        (business_id,),
    )
    business = await cursor.fetchone()
    if business is None:
        raise ExportSubjectNotFoundError
    if scope == "business":
        return str(business[0]), None

    cursor = await connection.execute(
        f"""
        select status::text
        from public.shops
        where id = %s and business_id = %s
        {lock_clause}
        """,
        (shop_id, business_id),
    )
    shop = await cursor.fetchone()
    if shop is None:
        raise ExportSubjectNotFoundError
    return str(business[0]), str(shop[0])


async def request_tenant_export(
    pool: Any,
    *,
    actor_id: UUID,
    idempotency_key: str,
    request_id: str,
    payload: ExportSubjectRequest,
) -> TenantExportResponse:
    async with pool.connection(timeout=5) as connection, connection.transaction():
        await connection.execute("set local statement_timeout = '5s'")
        await require_platform_admin(connection, actor_id)
        replay = await reserve_idempotency(
            connection,
            scope="platform.export.request",
            actor_id=actor_id,
            key=idempotency_key,
            payload=payload,
            expected_status=201,
        )
        if replay is not None:
            return TenantExportResponse.model_validate(replay)

        await _validate_subject(
            connection,
            business_id=payload.business_id,
            shop_id=payload.shop_id,
            scope=payload.scope,
            lock=False,
        )
        cursor = await connection.execute(
            """
            insert into public.tenant_exports (
              business_id, shop_id, scope, schema_version, format, requested_by
            )
            values (%s, %s, %s, %s, %s, %s)
            returning id
            """,
            (
                payload.business_id,
                payload.shop_id,
                payload.scope,
                EXPORT_SCHEMA_VERSION,
                EXPORT_FORMAT,
                actor_id,
            ),
        )
        export_id = UUID(str((await cursor.fetchone())[0]))
        response = TenantExportResponse(
            export_id=export_id,
            business_id=payload.business_id,
            shop_id=payload.shop_id,
            scope=payload.scope,
            status="requested",
            schema_version=EXPORT_SCHEMA_VERSION,
        )
        await write_platform_event(
            connection,
            business_id=payload.business_id,
            shop_id=payload.shop_id,
            actor_id=actor_id,
            action="tenant_export.requested",
            entity_type="tenant_export",
            entity_id=export_id,
            request_id=request_id,
            details={
                "export_id": str(export_id),
                "scope": payload.scope,
                "schema_version": EXPORT_SCHEMA_VERSION,
            },
        )
        await complete_idempotency(
            connection,
            scope="platform.export.request",
            actor_id=actor_id,
            key=idempotency_key,
            response_status=201,
            response=response,
        )
        return response


async def begin_offboarding(
    pool: Any,
    *,
    actor_id: UUID,
    idempotency_key: str,
    request_id: str,
    payload: OffboardingRequest,
) -> OffboardingResponse:
    try:
        async with pool.connection(timeout=5) as connection, connection.transaction():
            await connection.execute("set local statement_timeout = '5s'")
            await require_platform_admin(connection, actor_id)
            replay = await reserve_idempotency(
                connection,
                scope="platform.offboarding.begin",
                actor_id=actor_id,
                key=idempotency_key,
                payload=payload,
                expected_status=201,
            )
            if replay is not None:
                return OffboardingResponse.model_validate(replay)

            business_status, shop_status = await _validate_subject(
                connection,
                business_id=payload.business_id,
                shop_id=payload.shop_id,
                scope=payload.scope,
                lock=True,
            )
            if business_status in {"offboarding", "archived"} or shop_status in {
                "offboarding",
                "archived",
            }:
                raise OffboardingStateConflictError

            cursor = await connection.execute(
                """
                select id
                from public.offboarding_cases
                where business_id = %s
                  and state not in ('archived', 'cancelled')
                order by id
                for share
                """,
                (payload.business_id,),
            )
            if await cursor.fetchone() is not None:
                raise OffboardingStateConflictError

            if payload.scope == "business":
                await connection.execute(
                    """
                    select id
                    from public.shops
                    where business_id = %s and status <> 'archived'
                    order by id
                    for update
                    """,
                    (payload.business_id,),
                )
                await connection.execute(
                    """
                    select id
                    from public.subscriptions
                    where business_id = %s and status <> 'archived'
                    order by id
                    for update
                    """,
                    (payload.business_id,),
                )
            else:
                await connection.execute(
                    """
                    select id
                    from public.subscriptions
                    where business_id = %s
                      and shop_id = %s
                      and status <> 'archived'
                    order by id
                    for update
                    """,
                    (payload.business_id, payload.shop_id),
                )

            cursor = await connection.execute(
                """
                insert into public.tenant_exports (
                  business_id, shop_id, scope, schema_version, format, requested_by
                )
                values (%s, %s, %s, %s, %s, %s)
                returning id
                """,
                (
                    payload.business_id,
                    payload.shop_id,
                    payload.scope,
                    EXPORT_SCHEMA_VERSION,
                    EXPORT_FORMAT,
                    actor_id,
                ),
            )
            export_id = UUID(str((await cursor.fetchone())[0]))
            cursor = await connection.execute(
                """
                insert into public.offboarding_cases (
                  business_id, shop_id, scope, reason, export_id,
                  requested_by, state, frozen_at
                )
                values (%s, %s, %s, %s, %s, %s, 'frozen', now())
                returning id
                """,
                (
                    payload.business_id,
                    payload.shop_id,
                    payload.scope,
                    payload.reason,
                    export_id,
                    actor_id,
                ),
            )
            case_id = UUID(str((await cursor.fetchone())[0]))

            if payload.scope == "business":
                await connection.execute(
                    """
                    update public.businesses
                    set status = 'offboarding', updated_at = now()
                    where id = %s
                    """,
                    (payload.business_id,),
                )
                await connection.execute(
                    """
                    update public.shops
                    set status = 'offboarding', updated_at = now()
                    where business_id = %s and status <> 'archived'
                    """,
                    (payload.business_id,),
                )
                await connection.execute(
                    """
                    update public.subscriptions
                    set status = 'offboarding',
                        status_changed_by = %s,
                        updated_at = now()
                    where business_id = %s and status <> 'archived'
                    """,
                    (actor_id, payload.business_id),
                )
                await connection.execute(
                    """
                    update public.bots
                    set healthy = false
                    where business_id = %s
                    """,
                    (payload.business_id,),
                )
            else:
                await connection.execute(
                    """
                    update public.shops
                    set status = 'offboarding', updated_at = now()
                    where id = %s and business_id = %s
                    """,
                    (payload.shop_id, payload.business_id),
                )
                await connection.execute(
                    """
                    update public.subscriptions
                    set status = 'offboarding',
                        status_changed_by = %s,
                        updated_at = now()
                    where business_id = %s
                      and shop_id = %s
                      and status <> 'archived'
                    """,
                    (actor_id, payload.business_id, payload.shop_id),
                )
                await connection.execute(
                    """
                    update public.bots
                    set healthy = false
                    where business_id = %s and shop_id = %s
                    """,
                    (payload.business_id, payload.shop_id),
                )

            response = OffboardingResponse(
                case_id=case_id,
                export_id=export_id,
                business_id=payload.business_id,
                shop_id=payload.shop_id,
                scope=payload.scope,
                state="frozen",
            )
            await write_platform_event(
                connection,
                business_id=payload.business_id,
                shop_id=payload.shop_id,
                actor_id=actor_id,
                action="tenant_offboarding.frozen",
                entity_type="offboarding_case",
                entity_id=case_id,
                request_id=request_id,
                details={
                    "case_id": str(case_id),
                    "export_id": str(export_id),
                    "scope": payload.scope,
                    "reason": payload.reason,
                },
            )
            await complete_idempotency(
                connection,
                scope="platform.offboarding.begin",
                actor_id=actor_id,
                key=idempotency_key,
                response_status=201,
                response=response,
            )
            return response
    except UniqueViolation as exc:
        raise OffboardingStateConflictError from exc


async def claim_next_export(pool: Any) -> ClaimedExport | None:
    async with pool.connection(timeout=5) as connection, connection.transaction():
        await connection.execute("set local statement_timeout = '5s'")
        await connection.execute(
            """
            update public.tenant_exports
            set status = 'failed',
                failure_reason = 'retry_exhausted'
            where status = 'processing'
              and processing_started_at < now() - interval '15 minutes'
              and attempt_count >= 3
            """
        )
        cursor = await connection.execute(
            """
            with candidate as (
              select id
              from public.tenant_exports
              where status = 'requested'
                 or (
                   status = 'processing'
                   and processing_started_at < now() - interval '15 minutes'
                   and attempt_count < 3
                 )
              order by requested_at, id
              for update skip locked
              limit 1
            )
            update public.tenant_exports e
            set status = 'processing',
                processing_started_at = now(),
                attempt_count = attempt_count + 1,
                failure_reason = null
            from candidate
            where e.id = candidate.id
            returning e.id, e.business_id, e.shop_id, e.scope::text, e.attempt_count
            """
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return ClaimedExport(
            export_id=UUID(str(row[0])),
            business_id=UUID(str(row[1])),
            shop_id=UUID(str(row[2])) if row[2] is not None else None,
            scope=cast(Literal["business", "shop"], str(row[3])),
            attempt_count=int(row[4]),
        )


async def _mark_export_failed(
    pool: Any,
    *,
    export_id: UUID,
    failure_reason: Literal["generation_failed", "storage_failed"],
) -> None:
    async with pool.connection(timeout=5) as connection, connection.transaction():
        await connection.execute(
            """
            update public.tenant_exports
            set status = 'failed',
                failure_reason = %s
            where id = %s and status = 'processing'
            """,
            (failure_reason, export_id),
        )


async def process_next_export(
    pool: Any,
    storage: ExportStorage,
    *,
    retention_hours: int,
) -> bool:
    claimed = await claim_next_export(pool)
    if claimed is None:
        return False

    try:
        async with pool.connection(timeout=5) as connection, connection.transaction():
            await connection.execute("set transaction isolation level repeatable read read only")
            cursor = await connection.execute(
                """
                select status::text
                from public.tenant_exports
                where id = %s
                """,
                (claimed.export_id,),
            )
            row = await cursor.fetchone()
            if row is None or row[0] != "processing":
                raise ExportStateConflictError
            datasets = await _load_export_datasets(
                connection,
                business_id=claimed.business_id,
                shop_id=claimed.shop_id,
                scope=claimed.scope,
            )
        archive = build_export_archive(
            subject=ExportSubjectRequest(
                business_id=claimed.business_id,
                shop_id=claimed.shop_id,
                scope=claimed.scope,
            ),
            datasets=datasets,
            generated_at=datetime.now(UTC),
        )
    except Exception:
        await _mark_export_failed(
            pool,
            export_id=claimed.export_id,
            failure_reason="generation_failed",
        )
        return True

    object_key = f"tenant-exports/{claimed.export_id}.zip"
    try:
        await asyncio.to_thread(storage.upload, object_key, archive)
    except Exception:
        await _mark_export_failed(
            pool,
            export_id=claimed.export_id,
            failure_reason="storage_failed",
        )
        return True

    checksum = hashlib.sha256(archive).hexdigest()
    async with pool.connection(timeout=5) as connection, connection.transaction():
        await connection.execute("set local statement_timeout = '5s'")
        cursor = await connection.execute(
            """
            update public.tenant_exports
            set status = 'ready',
                object_key = %s,
                sha256 = %s,
                size_bytes = %s,
                content_type = %s,
                ready_at = now(),
                expires_at = now() + make_interval(hours => %s)
            where id = %s and status = 'processing'
            returning business_id, shop_id
            """,
            (
                object_key,
                checksum,
                len(archive),
                EXPORT_CONTENT_TYPE,
                retention_hours,
                claimed.export_id,
            ),
        )
        finalized = await cursor.fetchone()
        if finalized is None:
            raise ExportStateConflictError
        await connection.execute(
            """
            update public.offboarding_cases
            set state = 'export_ready'
            where export_id = %s and state = 'frozen'
            """,
            (claimed.export_id,),
        )
        await write_platform_event(
            connection,
            business_id=claimed.business_id,
            shop_id=claimed.shop_id,
            actor_id=None,
            action="tenant_export.ready",
            entity_type="tenant_export",
            entity_id=claimed.export_id,
            request_id=f"export-worker:{claimed.export_id}:{claimed.attempt_count}",
            details={
                "export_id": str(claimed.export_id),
                "schema_version": EXPORT_SCHEMA_VERSION,
                "sha256": checksum,
                "size_bytes": len(archive),
            },
            system_actor="tenant-export-worker",
        )
    return True


async def create_export_download(
    pool: Any,
    storage: ExportStorage,
    settings: Settings,
    *,
    actor_id: UUID,
    export_id: UUID,
    request_id: str,
) -> ExportDownloadResponse:
    async with pool.connection(timeout=5) as connection, connection.transaction():
        await require_platform_admin(connection, actor_id)
        cursor = await connection.execute(
            """
            select business_id, shop_id, status::text, object_key, expires_at,
                   object_deleted_at
            from public.tenant_exports
            where id = %s
            for share
            """,
            (export_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            raise ExportSubjectNotFoundError
        if row[5] is not None:
            raise ExportExpiredError
        if row[2] not in {"ready", "delivered"} or row[3] is None or row[4] is None:
            raise ExportStateConflictError
        remaining_seconds = int((row[4] - datetime.now(UTC)).total_seconds())
        if remaining_seconds <= 0:
            raise ExportExpiredError
        ttl_seconds = min(settings.export_download_ttl_seconds, remaining_seconds)
        business_id = UUID(str(row[0]))
        shop_id = UUID(str(row[1])) if row[1] is not None else None
        object_key = str(row[3])

    try:
        download_url = await asyncio.to_thread(
            storage.create_download_url,
            object_key,
            ttl_seconds,
        )
    except Exception as exc:
        raise ExportStorageUnavailableError from exc
    parsed = urlsplit(download_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ExportStorageUnavailableError
    if settings.env == "production" and parsed.scheme != "https":
        raise ExportStorageUnavailableError

    expires_at = datetime.now(UTC) + timedelta(seconds=ttl_seconds)
    async with pool.connection(timeout=5) as connection, connection.transaction():
        await require_platform_admin(connection, actor_id)
        cursor = await connection.execute(
            """
            select 1
            from public.tenant_exports
            where id = %s
              and status in ('ready', 'delivered')
              and object_key = %s
              and object_deleted_at is null
              and expires_at > now()
            for share
            """,
            (export_id, object_key),
        )
        if await cursor.fetchone() is None:
            raise ExportExpiredError
        await write_platform_event(
            connection,
            business_id=business_id,
            shop_id=shop_id,
            actor_id=actor_id,
            action="tenant_export.download_link_issued",
            entity_type="tenant_export",
            entity_id=export_id,
            request_id=request_id,
            details={
                "export_id": str(export_id),
                "link_expires_at": expires_at.isoformat(),
            },
        )
    return ExportDownloadResponse(
        export_id=export_id,
        download_url=download_url,
        expires_at=expires_at,
    )


async def confirm_export_delivery(
    pool: Any,
    *,
    actor_id: UUID,
    export_id: UUID,
    idempotency_key: str,
    request_id: str,
) -> ExportDeliveryResponse:
    payload = ExportActionRequest(export_id=export_id)
    async with pool.connection(timeout=5) as connection, connection.transaction():
        await connection.execute("set local statement_timeout = '5s'")
        await require_platform_admin(connection, actor_id)
        replay = await reserve_idempotency(
            connection,
            scope=f"platform.export.delivery:{export_id}",
            actor_id=actor_id,
            key=idempotency_key,
            payload=payload,
            expected_status=200,
        )
        if replay is not None:
            return ExportDeliveryResponse.model_validate(replay)

        cursor = await connection.execute(
            """
            select business_id, shop_id, status::text, expires_at, object_deleted_at
            from public.tenant_exports
            where id = %s
            for update
            """,
            (export_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            raise ExportSubjectNotFoundError
        if row[2] != "ready" or row[3] is None or row[3] <= datetime.now(UTC) or row[4] is not None:
            raise ExportStateConflictError
        await connection.execute(
            """
            update public.tenant_exports
            set status = 'delivered',
                delivered_at = now()
            where id = %s
            """,
            (export_id,),
        )
        await connection.execute(
            """
            update public.offboarding_cases
            set state = 'delivered',
                delivered_at = now()
            where export_id = %s and state = 'export_ready'
            """,
            (export_id,),
        )
        response = ExportDeliveryResponse(export_id=export_id, status="delivered")
        business_id = UUID(str(row[0]))
        shop_id = UUID(str(row[1])) if row[1] is not None else None
        await write_platform_event(
            connection,
            business_id=business_id,
            shop_id=shop_id,
            actor_id=actor_id,
            action="tenant_export.delivery_confirmed",
            entity_type="tenant_export",
            entity_id=export_id,
            request_id=request_id,
            details={"export_id": str(export_id)},
        )
        await complete_idempotency(
            connection,
            scope=f"platform.export.delivery:{export_id}",
            actor_id=actor_id,
            key=idempotency_key,
            response_status=200,
            response=response,
        )
        return response


async def archive_offboarding(
    pool: Any,
    *,
    actor_id: UUID,
    case_id: UUID,
    idempotency_key: str,
    request_id: str,
) -> OffboardingArchiveResponse:
    payload = OffboardingActionRequest(case_id=case_id)
    async with pool.connection(timeout=5) as connection, connection.transaction():
        await connection.execute("set local statement_timeout = '5s'")
        await require_platform_admin(connection, actor_id)
        replay = await reserve_idempotency(
            connection,
            scope=f"platform.offboarding.archive:{case_id}",
            actor_id=actor_id,
            key=idempotency_key,
            payload=payload,
            expected_status=200,
        )
        if replay is not None:
            return OffboardingArchiveResponse.model_validate(replay)

        cursor = await connection.execute(
            """
            select business_id, shop_id
            from public.offboarding_cases
            where id = %s
            """,
            (case_id,),
        )
        subject = await cursor.fetchone()
        if subject is None:
            raise ExportSubjectNotFoundError
        business_id = UUID(str(subject[0]))
        await connection.execute(
            "select id from public.businesses where id = %s for update",
            (business_id,),
        )
        cursor = await connection.execute(
            """
            select business_id, shop_id, scope::text, state, export_id
            from public.offboarding_cases
            where id = %s
            for update
            """,
            (case_id,),
        )
        case = await cursor.fetchone()
        if case is None or case[3] != "delivered":
            raise OffboardingStateConflictError
        shop_id = UUID(str(case[1])) if case[1] is not None else None
        scope = cast(Literal["business", "shop"], str(case[2]))

        cursor = await connection.execute(
            """
            select status::text
            from public.tenant_exports
            where id = %s
            for share
            """,
            (case[4],),
        )
        export = await cursor.fetchone()
        if export is None or export[0] != "delivered":
            raise OffboardingStateConflictError

        if scope == "business":
            await connection.execute(
                """
                select id
                from public.shops
                where business_id = %s and status <> 'archived'
                order by id
                for update
                """,
                (business_id,),
            )
            await connection.execute(
                """
                select id
                from public.subscriptions
                where business_id = %s and status <> 'archived'
                order by id
                for update
                """,
                (business_id,),
            )
            await connection.execute(
                """
                update public.subscriptions
                set status = 'archived',
                    status_changed_by = %s,
                    updated_at = now()
                where business_id = %s and status <> 'archived'
                """,
                (actor_id, business_id),
            )
            await connection.execute(
                """
                update public.shop_memberships
                set active = false, updated_at = now()
                where business_id = %s and active
                """,
                (business_id,),
            )
            await connection.execute(
                """
                update public.business_owners
                set active = false
                where business_id = %s and active
                """,
                (business_id,),
            )
            await connection.execute(
                """
                update public.bots
                set healthy = false
                where business_id = %s
                """,
                (business_id,),
            )
            await connection.execute(
                """
                update public.shops
                set status = 'archived',
                    archived_at = now(),
                    updated_at = now()
                where business_id = %s and status <> 'archived'
                """,
                (business_id,),
            )
            await connection.execute(
                """
                update public.businesses
                set status = 'archived',
                    archived_at = now(),
                    updated_at = now()
                where id = %s
                """,
                (business_id,),
            )
        else:
            await connection.execute(
                """
                select id
                from public.subscriptions
                where business_id = %s
                  and shop_id = %s
                  and status <> 'archived'
                order by id
                for update
                """,
                (business_id, shop_id),
            )
            await connection.execute(
                """
                update public.subscriptions
                set status = 'archived',
                    status_changed_by = %s,
                    updated_at = now()
                where business_id = %s
                  and shop_id = %s
                  and status <> 'archived'
                """,
                (actor_id, business_id, shop_id),
            )
            await connection.execute(
                """
                update public.shop_memberships
                set active = false, updated_at = now()
                where business_id = %s and shop_id = %s and active
                """,
                (business_id, shop_id),
            )
            await connection.execute(
                """
                update public.bots
                set healthy = false
                where business_id = %s and shop_id = %s
                """,
                (business_id, shop_id),
            )
            await connection.execute(
                """
                update public.shops
                set status = 'archived',
                    archived_at = now(),
                    updated_at = now()
                where id = %s and business_id = %s
                """,
                (shop_id, business_id),
            )

        await connection.execute(
            """
            update public.offboarding_cases
            set state = 'archived',
                archived_at = now()
            where id = %s
            """,
            (case_id,),
        )
        response = OffboardingArchiveResponse(
            case_id=case_id,
            business_id=business_id,
            shop_id=shop_id,
            state="archived",
        )
        await write_platform_event(
            connection,
            business_id=business_id,
            shop_id=shop_id,
            actor_id=actor_id,
            action="tenant_offboarding.archived",
            entity_type="offboarding_case",
            entity_id=case_id,
            request_id=request_id,
            details={
                "case_id": str(case_id),
                "export_id": str(case[4]),
                "scope": scope,
            },
        )
        await complete_idempotency(
            connection,
            scope=f"platform.offboarding.archive:{case_id}",
            actor_id=actor_id,
            key=idempotency_key,
            response_status=200,
            response=response,
        )
        return response


async def purge_expired_exports(
    pool: Any,
    storage: ExportStorage,
    *,
    batch_size: int = 25,
) -> int:
    async with pool.connection(timeout=5) as connection, connection.transaction():
        cursor = await connection.execute(
            """
            select id, business_id, shop_id, object_key
            from public.tenant_exports
            where status in ('ready', 'delivered')
              and expires_at <= now()
              and object_deleted_at is null
              and object_key is not null
            order by expires_at, id
            limit %s
            """,
            (batch_size,),
        )
        expired = await cursor.fetchall()

    deleted = 0
    for export_id, business_id, shop_id, object_key in expired:
        try:
            await asyncio.to_thread(storage.delete, str(object_key))
        except Exception:
            continue
        async with pool.connection(timeout=5) as connection, connection.transaction():
            cursor = await connection.execute(
                """
                update public.tenant_exports
                set object_deleted_at = now()
                where id = %s
                  and expires_at <= now()
                  and object_deleted_at is null
                returning id
                """,
                (export_id,),
            )
            if await cursor.fetchone() is None:
                continue
            await write_platform_event(
                connection,
                business_id=UUID(str(business_id)),
                shop_id=UUID(str(shop_id)) if shop_id is not None else None,
                actor_id=None,
                action="tenant_export.object_deleted",
                entity_type="tenant_export",
                entity_id=UUID(str(export_id)),
                request_id=f"export-purge:{export_id}",
                details={"export_id": str(export_id)},
                system_actor="tenant-export-worker",
            )
            deleted += 1
    return deleted
