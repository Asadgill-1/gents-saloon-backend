import hashlib
import json
import secrets
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from psycopg.errors import ForeignKeyViolation, UniqueViolation
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field, model_validator

ONBOARDING_SCOPE = "platform.tenant.onboard"
IDEMPOTENCY_TTL_HOURS = 24


class PlatformAdminRequiredError(Exception):
    """The authenticated user is not an active platform administrator."""


class IdempotencyConflictError(Exception):
    """An idempotency key was reused with a different request."""


class IdempotencyInProgressError(Exception):
    """An existing idempotent request has no recorded result."""


class OwnerIdentityNotFoundError(Exception):
    """The supplied owner UUID does not exist in Supabase Auth."""


class OwnerIdentityInactiveError(Exception):
    """The supplied owner has an inactive application profile."""


class TenantOnboardingConflictError(Exception):
    """A tenant record conflicts with an existing record."""


class TenantOnboardingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    legal_name: str = Field(min_length=1, max_length=200)
    display_name: str = Field(min_length=1, max_length=120)
    billing_mode: Literal["business", "per_shop"]
    trade_license_number: str | None = Field(default=None, max_length=64)
    trade_license_expiry: date | None = None
    vat_registered: bool = False
    trn: str | None = Field(default=None, min_length=1, max_length=20)
    invoice_address: str | None = Field(default=None, max_length=500)
    contact_name: str | None = Field(default=None, max_length=120)
    contact_phone: str | None = Field(default=None, max_length=32)
    contact_email: str | None = Field(
        default=None,
        max_length=320,
        pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$",
    )
    owner_auth_user_id: UUID
    owner_display_name: str = Field(min_length=1, max_length=120)
    owner_phone: str | None = Field(default=None, max_length=32)
    shop_name: str = Field(min_length=1, max_length=120)
    shop_internal_code: str = Field(
        min_length=1,
        max_length=32,
        pattern=r"^[A-Z0-9][A-Z0-9_-]*$",
    )
    shop_open_time: time
    shop_close_time: time
    shop_eod_time: time
    default_service_minutes: int = Field(ge=5, le=480)
    paid_from: date
    paid_until: date
    initial_payment_amount: Decimal = Field(gt=0, max_digits=14, decimal_places=2)
    initial_receipt_reference: str = Field(min_length=1, max_length=100)
    initial_collected_at: datetime
    initial_payment_evidence_note: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def validate_business_rules(self) -> "TenantOnboardingRequest":
        if self.vat_registered and self.trn is None:
            raise ValueError("TRN is required for a VAT-registered business")
        if self.paid_until < self.paid_from:
            raise ValueError("paid_until must be on or after paid_from")
        if self.initial_collected_at.tzinfo is None:
            raise ValueError("initial_collected_at must include a timezone")
        return self


class TenantOnboardingResponse(BaseModel):
    business_id: UUID
    shop_id: UUID
    subscription_id: UUID
    receipt_id: UUID
    owner_auth_user_id: UUID
    public_queue_token: str = Field(repr=False)


def _request_hash(payload: TenantOnboardingRequest) -> str:
    canonical = json.dumps(
        payload.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


async def onboard_tenant(
    pool: Any,
    *,
    actor_id: UUID,
    idempotency_key: str,
    request_id: str,
    payload: TenantOnboardingRequest,
) -> TenantOnboardingResponse:
    request_hash = _request_hash(payload)
    idempotency_scope = f"{ONBOARDING_SCOPE}:{actor_id}"

    try:
        async with pool.connection(timeout=5) as connection, connection.transaction():
            await connection.execute("set local statement_timeout = '5s'")

            admin_cursor = await connection.execute(
                """
                select 1
                from public.user_profiles up
                join public.platform_admins pa
                  on pa.auth_user_id = up.auth_user_id
                where up.auth_user_id = %s
                  and up.active
                  and pa.active
                for share of up, pa
                """,
                (actor_id,),
            )
            if await admin_cursor.fetchone() is None:
                raise PlatformAdminRequiredError

            await connection.execute(
                """
                delete from public.idempotency_keys
                where scope = %s
                  and key = %s
                  and expires_at <= now()
                """,
                (idempotency_scope, idempotency_key),
            )
            reservation_cursor = await connection.execute(
                """
                insert into public.idempotency_keys (
                  scope, key, actor_id, request_hash, expires_at
                )
                values (%s, %s, %s, %s, now() + make_interval(hours => %s))
                on conflict (scope, key) do nothing
                returning key
                """,
                (
                    idempotency_scope,
                    idempotency_key,
                    str(actor_id),
                    request_hash,
                    IDEMPOTENCY_TTL_HOURS,
                ),
            )
            if await reservation_cursor.fetchone() is None:
                existing_cursor = await connection.execute(
                    """
                    select request_hash, response_status, response_body, completed_at
                    from public.idempotency_keys
                    where scope = %s and key = %s
                    """,
                    (idempotency_scope, idempotency_key),
                )
                existing = await existing_cursor.fetchone()
                if existing is None or str(existing[0]) != request_hash:
                    raise IdempotencyConflictError
                if existing[3] is None or existing[1] != 201 or existing[2] is None:
                    raise IdempotencyInProgressError
                return TenantOnboardingResponse.model_validate(existing[2])

            await connection.execute(
                """
                insert into public.user_profiles (
                  auth_user_id, display_name, phone
                )
                values (%s, %s, %s)
                on conflict (auth_user_id) do nothing
                """,
                (
                    payload.owner_auth_user_id,
                    payload.owner_display_name,
                    payload.owner_phone,
                ),
            )
            owner_cursor = await connection.execute(
                """
                select active
                from public.user_profiles
                where auth_user_id = %s
                for share
                """,
                (payload.owner_auth_user_id,),
            )
            owner = await owner_cursor.fetchone()
            if owner is None:
                raise OwnerIdentityNotFoundError
            if not owner[0]:
                raise OwnerIdentityInactiveError

            business_cursor = await connection.execute(
                """
                insert into public.businesses (
                  legal_name,
                  display_name,
                  trade_license_number,
                  trade_license_expiry,
                  vat_registered,
                  trn,
                  invoice_address,
                  contact_name,
                  contact_phone,
                  contact_email,
                  billing_mode
                )
                values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                returning id
                """,
                (
                    payload.legal_name,
                    payload.display_name,
                    payload.trade_license_number,
                    payload.trade_license_expiry,
                    payload.vat_registered,
                    payload.trn,
                    payload.invoice_address,
                    payload.contact_name,
                    payload.contact_phone,
                    payload.contact_email,
                    payload.billing_mode,
                ),
            )
            business_id = UUID(str((await business_cursor.fetchone())[0]))

            await connection.execute(
                """
                insert into public.business_owners (
                  business_id, auth_user_id, is_primary
                )
                values (%s, %s, true)
                """,
                (business_id, payload.owner_auth_user_id),
            )

            public_queue_token = secrets.token_urlsafe(32)
            queue_token_hash = hashlib.sha256(public_queue_token.encode()).hexdigest()
            shop_cursor = await connection.execute(
                """
                insert into public.shops (
                  business_id,
                  name,
                  internal_code,
                  public_queue_token_hash,
                  open_time,
                  close_time,
                  default_service_minutes,
                  eod_time
                )
                values (%s, %s, %s, %s, %s, %s, %s, %s)
                returning id
                """,
                (
                    business_id,
                    payload.shop_name,
                    payload.shop_internal_code,
                    queue_token_hash,
                    payload.shop_open_time,
                    payload.shop_close_time,
                    payload.default_service_minutes,
                    payload.shop_eod_time,
                ),
            )
            shop_id = UUID(str((await shop_cursor.fetchone())[0]))

            subscription_scope = "business" if payload.billing_mode == "business" else "shop"
            subscription_shop_id = None if subscription_scope == "business" else shop_id
            subscription_cursor = await connection.execute(
                """
                insert into public.subscriptions (
                  business_id,
                  shop_id,
                  scope,
                  paid_from,
                  paid_until,
                  status_changed_by
                )
                values (%s, %s, %s, %s, %s, %s)
                returning id
                """,
                (
                    business_id,
                    subscription_shop_id,
                    subscription_scope,
                    payload.paid_from,
                    payload.paid_until,
                    actor_id,
                ),
            )
            subscription_id = UUID(str((await subscription_cursor.fetchone())[0]))

            receipt_cursor = await connection.execute(
                """
                insert into public.subscription_cash_receipts (
                  subscription_id,
                  business_id,
                  shop_id,
                  amount,
                  receipt_reference,
                  collected_at,
                  coverage_from,
                  coverage_until,
                  collected_by,
                  evidence_note
                )
                values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                returning id
                """,
                (
                    subscription_id,
                    business_id,
                    subscription_shop_id,
                    payload.initial_payment_amount,
                    payload.initial_receipt_reference,
                    payload.initial_collected_at,
                    payload.paid_from,
                    payload.paid_until,
                    actor_id,
                    payload.initial_payment_evidence_note,
                ),
            )
            receipt_id = UUID(str((await receipt_cursor.fetchone())[0]))

            response = TenantOnboardingResponse(
                business_id=business_id,
                shop_id=shop_id,
                subscription_id=subscription_id,
                receipt_id=receipt_id,
                owner_auth_user_id=payload.owner_auth_user_id,
                public_queue_token=public_queue_token,
            )
            safe_event = {
                "business_id": str(business_id),
                "shop_id": str(shop_id),
                "subscription_id": str(subscription_id),
                "receipt_id": str(receipt_id),
                "owner_auth_user_id": str(payload.owner_auth_user_id),
                "billing_mode": payload.billing_mode,
            }
            await connection.execute(
                """
                insert into public.audit_log (
                  business_id,
                  shop_id,
                  actor_type,
                  actor_id,
                  action,
                  entity_type,
                  entity_id,
                  request_id,
                  after
                )
                values (%s, %s, 'platform_admin', %s, %s, %s, %s, %s, %s)
                """,
                (
                    business_id,
                    shop_id,
                    str(actor_id),
                    "tenant.onboarded",
                    "business",
                    business_id,
                    request_id,
                    Jsonb(safe_event),
                ),
            )
            await connection.execute(
                """
                insert into public.outbox_events (
                  business_id, shop_id, topic, dedupe_key, payload
                )
                values (%s, %s, %s, %s, %s)
                """,
                (
                    business_id,
                    shop_id,
                    "tenant.onboarded",
                    f"tenant.onboarded:{business_id}",
                    Jsonb(safe_event),
                ),
            )
            await connection.execute(
                """
                update public.idempotency_keys
                set response_status = 201,
                    response_body = %s,
                    completed_at = now()
                where scope = %s and key = %s
                """,
                (
                    Jsonb(response.model_dump(mode="json")),
                    idempotency_scope,
                    idempotency_key,
                ),
            )
            return response
    except ForeignKeyViolation as exc:
        if exc.diag.constraint_name == "user_profiles_auth_user_id_fkey":
            raise OwnerIdentityNotFoundError from exc
        raise TenantOnboardingConflictError from exc
    except UniqueViolation as exc:
        raise TenantOnboardingConflictError from exc
