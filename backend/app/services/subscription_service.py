from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from psycopg.errors import UniqueViolation
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.core.entitlements import has_current_coverage
from app.services.platform_operations import (
    IdempotencyConflictError as IdempotencyConflictError,
)
from app.services.platform_operations import (
    IdempotencyInProgressError as IdempotencyInProgressError,
)
from app.services.platform_operations import (
    PlatformAdminRequiredError as PlatformAdminRequiredError,
)
from app.services.platform_operations import (
    complete_idempotency as _complete_idempotency,
)
from app.services.platform_operations import (
    require_platform_admin as _require_platform_admin,
)
from app.services.platform_operations import (
    reserve_idempotency as _reserve_idempotency,
)
from app.services.platform_operations import (
    write_platform_event as _write_event,
)


class SubscriptionNotFoundError(Exception):
    """The subscription does not exist."""


class SubscriptionStateConflictError(Exception):
    """The requested lifecycle transition is invalid."""


class PaidCoverageRequiredError(Exception):
    """The subscription has no current paid coverage."""


class BillingTransitionConflictError(Exception):
    """The requested billing-mode transition is incomplete or ambiguous."""


class CashReceiptRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    subscription_id: UUID
    amount: Decimal = Field(gt=0, max_digits=14, decimal_places=2)
    receipt_reference: str = Field(min_length=1, max_length=100)
    collected_at: datetime
    coverage_from: date
    coverage_until: date
    evidence_note: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def validate_receipt(self) -> "CashReceiptRequest":
        if self.coverage_until < self.coverage_from:
            raise ValueError("coverage_until must be on or after coverage_from")
        if self.collected_at.tzinfo is None:
            raise ValueError("collected_at must include a timezone")
        return self


class CashReceiptResponse(BaseModel):
    receipt_id: UUID
    receipt_sequence: int
    subscription_id: UUID
    business_id: UUID
    shop_id: UUID | None
    coverage_from: date
    coverage_until: date
    reversed_receipt_id: UUID | None = None


class ReceiptReversalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    receipt_reference: str = Field(min_length=1, max_length=100)
    collected_at: datetime
    evidence_note: str = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def validate_collected_at(self) -> "ReceiptReversalRequest":
        if self.collected_at.tzinfo is None:
            raise ValueError("collected_at must include a timezone")
        return self


class SuspendSubscriptionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    reason: Literal["non_payment", "manual", "security", "offboarding"]
    explanation: str = Field(min_length=1, max_length=500)


class ResumeSubscriptionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    explanation: str = Field(min_length=1, max_length=500)
    manual_override_until: datetime | None = None
    manual_override_reason: str | None = Field(default=None, min_length=1, max_length=500)

    @model_validator(mode="after")
    def validate_override(self) -> "ResumeSubscriptionRequest":
        paired = self.manual_override_until is not None and self.manual_override_reason is not None
        if (
            self.manual_override_until is not None or self.manual_override_reason is not None
        ) and not paired:
            raise ValueError("manual override expiry and reason must be supplied together")
        if self.manual_override_until is not None and self.manual_override_until.tzinfo is None:
            raise ValueError("manual_override_until must include a timezone")
        return self


class SubscriptionStateResponse(BaseModel):
    subscription_id: UUID
    business_id: UUID
    shop_id: UUID | None
    status: str


class BillingModeTransitionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    target_mode: Literal["business", "per_shop"]
    reason: str = Field(min_length=1, max_length=500)


class BillingModeTransitionResponse(BaseModel):
    business_id: UUID
    billing_mode: Literal["business", "per_shop"]
    subscription_ids: list[UUID]


async def _sync_subject_status(
    connection: Any,
    *,
    business_id: UUID,
    shop_id: UUID | None,
    scope: str,
    status: str,
) -> None:
    if scope == "business":
        await connection.execute(
            "update public.businesses set status = %s, updated_at = now() where id = %s",
            (status, business_id),
        )
    else:
        await connection.execute(
            "update public.shops set status = %s, updated_at = now() where id = %s",
            (status, shop_id),
        )


async def record_cash_receipt(
    pool: Any,
    *,
    actor_id: UUID,
    idempotency_key: str,
    request_id: str,
    payload: CashReceiptRequest,
    at: datetime | None = None,
) -> CashReceiptResponse:
    checked_at = at or datetime.now(UTC)
    try:
        async with pool.connection(timeout=5) as connection, connection.transaction():
            await connection.execute("set local statement_timeout = '5s'")
            await _require_platform_admin(connection, actor_id)
            replay = await _reserve_idempotency(
                connection,
                scope="platform.subscription.cash_receipt",
                actor_id=actor_id,
                key=idempotency_key,
                payload=payload,
                expected_status=201,
            )
            if replay is not None:
                return CashReceiptResponse.model_validate(replay)

            cursor = await connection.execute(
                """
                select business_id, shop_id, scope::text, status::text,
                       paid_from, paid_until, manual_override_until
                from public.subscriptions
                where id = %s
                for update
                """,
                (payload.subscription_id,),
            )
            subscription = await cursor.fetchone()
            if subscription is None:
                raise SubscriptionNotFoundError
            business_id, shop_id, scope, current_status = subscription[:4]
            if current_status in {"offboarding", "archived"}:
                raise SubscriptionStateConflictError

            cursor = await connection.execute(
                """
                insert into public.subscription_cash_receipts (
                  subscription_id, business_id, shop_id, amount, receipt_reference,
                  collected_at, coverage_from, coverage_until, collected_by, evidence_note
                )
                values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                returning id, receipt_sequence
                """,
                (
                    payload.subscription_id,
                    business_id,
                    shop_id,
                    payload.amount,
                    payload.receipt_reference,
                    payload.collected_at,
                    payload.coverage_from,
                    payload.coverage_until,
                    actor_id,
                    payload.evidence_note,
                ),
            )
            receipt_id, receipt_sequence = await cursor.fetchone()
            paid_from = min(subscription[4], payload.coverage_from)
            paid_until = max(subscription[5], payload.coverage_until)
            next_status = str(current_status)
            if current_status == "expired" and (
                has_current_coverage(paid_from, paid_until, at=checked_at)
                or (subscription[6] is not None and checked_at < subscription[6])
            ):
                next_status = "active"

            await connection.execute(
                """
                update public.subscriptions
                set paid_from = %s,
                    paid_until = %s,
                    status = %s,
                    status_changed_by = %s,
                    updated_at = now()
                where id = %s
                """,
                (paid_from, paid_until, next_status, actor_id, payload.subscription_id),
            )
            if next_status != current_status:
                await _sync_subject_status(
                    connection,
                    business_id=business_id,
                    shop_id=shop_id,
                    scope=scope,
                    status=next_status,
                )

            response = CashReceiptResponse(
                receipt_id=UUID(str(receipt_id)),
                receipt_sequence=int(receipt_sequence),
                subscription_id=payload.subscription_id,
                business_id=UUID(str(business_id)),
                shop_id=UUID(str(shop_id)) if shop_id is not None else None,
                coverage_from=payload.coverage_from,
                coverage_until=payload.coverage_until,
            )
            details = {
                "receipt_id": str(response.receipt_id),
                "subscription_id": str(payload.subscription_id),
                "coverage_from": payload.coverage_from.isoformat(),
                "coverage_until": payload.coverage_until.isoformat(),
            }
            await _write_event(
                connection,
                business_id=response.business_id,
                shop_id=response.shop_id,
                actor_id=actor_id,
                action="subscription.cash_receipt_recorded",
                entity_type="subscription_cash_receipt",
                entity_id=response.receipt_id,
                request_id=request_id,
                details=details,
            )
            await _complete_idempotency(
                connection,
                scope="platform.subscription.cash_receipt",
                actor_id=actor_id,
                key=idempotency_key,
                response_status=201,
                response=response,
            )
            return response
    except UniqueViolation as exc:
        raise SubscriptionStateConflictError from exc


async def reverse_cash_receipt(
    pool: Any,
    *,
    actor_id: UUID,
    receipt_id: UUID,
    idempotency_key: str,
    request_id: str,
    payload: ReceiptReversalRequest,
) -> CashReceiptResponse:
    async with pool.connection(timeout=5) as connection, connection.transaction():
        await connection.execute("set local statement_timeout = '5s'")
        await _require_platform_admin(connection, actor_id)
        replay = await _reserve_idempotency(
            connection,
            scope=f"platform.subscription.receipt_reverse:{receipt_id}",
            actor_id=actor_id,
            key=idempotency_key,
            payload=payload,
            expected_status=201,
        )
        if replay is not None:
            return CashReceiptResponse.model_validate(replay)

        cursor = await connection.execute(
            """
            select id, subscription_id, business_id, shop_id, amount, currency,
                   coverage_from, coverage_until, reversal_of_id
            from public.subscription_cash_receipts
            where id = %s
            for share
            """,
            (receipt_id,),
        )
        original = await cursor.fetchone()
        if original is None:
            raise SubscriptionNotFoundError
        if original[8] is not None:
            raise SubscriptionStateConflictError

        try:
            cursor = await connection.execute(
                """
                insert into public.subscription_cash_receipts (
                  subscription_id, business_id, shop_id, amount, currency,
                  receipt_reference, collected_at, coverage_from, coverage_until,
                  collected_by, evidence_note, reversal_of_id
                )
                values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                returning id, receipt_sequence
                """,
                (
                    original[1],
                    original[2],
                    original[3],
                    original[4],
                    original[5],
                    payload.receipt_reference,
                    payload.collected_at,
                    original[6],
                    original[7],
                    actor_id,
                    payload.evidence_note,
                    receipt_id,
                ),
            )
        except UniqueViolation as exc:
            raise SubscriptionStateConflictError from exc
        reversal_id, receipt_sequence = await cursor.fetchone()
        response = CashReceiptResponse(
            receipt_id=UUID(str(reversal_id)),
            receipt_sequence=int(receipt_sequence),
            subscription_id=UUID(str(original[1])),
            business_id=UUID(str(original[2])),
            shop_id=UUID(str(original[3])) if original[3] is not None else None,
            coverage_from=original[6],
            coverage_until=original[7],
            reversed_receipt_id=receipt_id,
        )
        await _write_event(
            connection,
            business_id=response.business_id,
            shop_id=response.shop_id,
            actor_id=actor_id,
            action="subscription.cash_receipt_reversed",
            entity_type="subscription_cash_receipt",
            entity_id=response.receipt_id,
            request_id=request_id,
            details={
                "reversal_receipt_id": str(response.receipt_id),
                "original_receipt_id": str(receipt_id),
                "subscription_id": str(response.subscription_id),
            },
        )
        await _complete_idempotency(
            connection,
            scope=f"platform.subscription.receipt_reverse:{receipt_id}",
            actor_id=actor_id,
            key=idempotency_key,
            response_status=201,
            response=response,
        )
        return response


async def suspend_subscription(
    pool: Any,
    *,
    actor_id: UUID,
    subscription_id: UUID,
    idempotency_key: str,
    request_id: str,
    payload: SuspendSubscriptionRequest,
    at: datetime | None = None,
) -> SubscriptionStateResponse:
    changed_at = at or datetime.now(UTC)
    async with pool.connection(timeout=5) as connection, connection.transaction():
        await connection.execute("set local statement_timeout = '5s'")
        await _require_platform_admin(connection, actor_id)
        replay = await _reserve_idempotency(
            connection,
            scope=f"platform.subscription.suspend:{subscription_id}",
            actor_id=actor_id,
            key=idempotency_key,
            payload=payload,
            expected_status=200,
        )
        if replay is not None:
            return SubscriptionStateResponse.model_validate(replay)

        cursor = await connection.execute(
            """
            select business_id, shop_id, scope::text, status::text
            from public.subscriptions
            where id = %s
            for update
            """,
            (subscription_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            raise SubscriptionNotFoundError
        if row[3] in {"suspended", "offboarding", "archived"}:
            raise SubscriptionStateConflictError

        await connection.execute(
            """
            update public.subscriptions
            set status = 'suspended',
                suspended_reason = %s,
                suspended_at = %s,
                resumed_at = null,
                manual_override_until = null,
                manual_override_reason = null,
                status_changed_by = %s,
                updated_at = now()
            where id = %s
            """,
            (payload.reason, changed_at, actor_id, subscription_id),
        )
        await _sync_subject_status(
            connection,
            business_id=row[0],
            shop_id=row[1],
            scope=row[2],
            status="suspended",
        )
        response = SubscriptionStateResponse(
            subscription_id=subscription_id,
            business_id=UUID(str(row[0])),
            shop_id=UUID(str(row[1])) if row[1] is not None else None,
            status="suspended",
        )
        await _write_event(
            connection,
            business_id=response.business_id,
            shop_id=response.shop_id,
            actor_id=actor_id,
            action="subscription.suspended",
            entity_type="subscription",
            entity_id=subscription_id,
            request_id=request_id,
            details={
                "subscription_id": str(subscription_id),
                "reason": payload.reason,
                "explanation": payload.explanation,
            },
        )
        await _complete_idempotency(
            connection,
            scope=f"platform.subscription.suspend:{subscription_id}",
            actor_id=actor_id,
            key=idempotency_key,
            response_status=200,
            response=response,
        )
        return response


async def resume_subscription(
    pool: Any,
    *,
    actor_id: UUID,
    subscription_id: UUID,
    idempotency_key: str,
    request_id: str,
    payload: ResumeSubscriptionRequest,
    at: datetime | None = None,
) -> SubscriptionStateResponse:
    changed_at = at or datetime.now(UTC)
    async with pool.connection(timeout=5) as connection, connection.transaction():
        await connection.execute("set local statement_timeout = '5s'")
        await _require_platform_admin(connection, actor_id)
        replay = await _reserve_idempotency(
            connection,
            scope=f"platform.subscription.resume:{subscription_id}",
            actor_id=actor_id,
            key=idempotency_key,
            payload=payload,
            expected_status=200,
        )
        if replay is not None:
            return SubscriptionStateResponse.model_validate(replay)

        cursor = await connection.execute(
            """
            select business_id, shop_id, scope::text, status::text,
                   paid_from, paid_until, suspended_reason::text
            from public.subscriptions
            where id = %s
            for update
            """,
            (subscription_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            raise SubscriptionNotFoundError
        if row[3] != "suspended":
            raise SubscriptionStateConflictError

        current_coverage = has_current_coverage(row[4], row[5], at=changed_at)
        if row[6] == "non_payment" and not current_coverage:
            raise PaidCoverageRequiredError
        override_active = (
            payload.manual_override_until is not None and payload.manual_override_until > changed_at
        )
        if not current_coverage and not override_active:
            raise PaidCoverageRequiredError

        await connection.execute(
            """
            update public.subscriptions
            set status = 'active',
                suspended_reason = null,
                suspended_at = null,
                resumed_at = %s,
                manual_override_until = %s,
                manual_override_reason = %s,
                status_changed_by = %s,
                updated_at = now()
            where id = %s
            """,
            (
                changed_at,
                payload.manual_override_until,
                payload.manual_override_reason,
                actor_id,
                subscription_id,
            ),
        )
        await _sync_subject_status(
            connection,
            business_id=row[0],
            shop_id=row[1],
            scope=row[2],
            status="active",
        )
        response = SubscriptionStateResponse(
            subscription_id=subscription_id,
            business_id=UUID(str(row[0])),
            shop_id=UUID(str(row[1])) if row[1] is not None else None,
            status="active",
        )
        await _write_event(
            connection,
            business_id=response.business_id,
            shop_id=response.shop_id,
            actor_id=actor_id,
            action="subscription.resumed",
            entity_type="subscription",
            entity_id=subscription_id,
            request_id=request_id,
            details={
                "subscription_id": str(subscription_id),
                "explanation": payload.explanation,
                "manual_override_until": (
                    payload.manual_override_until.isoformat()
                    if payload.manual_override_until is not None
                    else None
                ),
            },
        )
        await _complete_idempotency(
            connection,
            scope=f"platform.subscription.resume:{subscription_id}",
            actor_id=actor_id,
            key=idempotency_key,
            response_status=200,
            response=response,
        )
        return response


async def transition_billing_mode(
    pool: Any,
    *,
    actor_id: UUID,
    business_id: UUID,
    idempotency_key: str,
    request_id: str,
    payload: BillingModeTransitionRequest,
) -> BillingModeTransitionResponse:
    async with pool.connection(timeout=5) as connection, connection.transaction():
        await connection.execute("set local statement_timeout = '5s'")
        await _require_platform_admin(connection, actor_id)
        replay = await _reserve_idempotency(
            connection,
            scope=f"platform.business.billing_mode:{business_id}",
            actor_id=actor_id,
            key=idempotency_key,
            payload=payload,
            expected_status=200,
        )
        if replay is not None:
            return BillingModeTransitionResponse.model_validate(replay)

        cursor = await connection.execute(
            """
            select billing_mode::text, status::text
            from public.businesses
            where id = %s
            for update
            """,
            (business_id,),
        )
        business = await cursor.fetchone()
        if business is None:
            raise SubscriptionNotFoundError
        if business[0] == payload.target_mode or business[1] in {"offboarding", "archived"}:
            raise BillingTransitionConflictError

        cursor = await connection.execute(
            """
            select id
            from public.shops
            where business_id = %s and status <> 'archived'
            order by id
            for update
            """,
            (business_id,),
        )
        shop_ids = [UUID(str(row[0])) for row in await cursor.fetchall()]
        if not shop_ids:
            raise BillingTransitionConflictError

        cursor = await connection.execute(
            """
            select id, shop_id, scope::text, status::text, paid_from, paid_until,
                   manual_override_until, manual_override_reason
            from public.subscriptions
            where business_id = %s and status <> 'archived'
            order by id
            for update
            """,
            (business_id,),
        )
        current = await cursor.fetchall()
        if not current or any(row[3] not in {"active", "expired"} for row in current):
            raise BillingTransitionConflictError

        if payload.target_mode == "per_shop":
            if len(current) != 1 or current[0][2] != "business":
                raise BillingTransitionConflictError
            template = current[0]
            targets: list[UUID | None] = [*shop_ids]
        else:
            if (
                len(current) != len(shop_ids)
                or any(row[2] != "shop" for row in current)
                or {UUID(str(row[1])) for row in current} != set(shop_ids)
            ):
                raise BillingTransitionConflictError
            templates = {(row[3], row[4], row[5], row[6], row[7]) for row in current}
            if len(templates) != 1:
                raise BillingTransitionConflictError
            template = current[0]
            targets = [None]

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
            "update public.businesses set billing_mode = %s, updated_at = now() where id = %s",
            (payload.target_mode, business_id),
        )

        new_ids: list[UUID] = []
        target_scope = "business" if payload.target_mode == "business" else "shop"
        for shop_id in targets:
            cursor = await connection.execute(
                """
                insert into public.subscriptions (
                  business_id, shop_id, scope, status, paid_from, paid_until,
                  manual_override_until, manual_override_reason, status_changed_by
                )
                values (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                returning id
                """,
                (
                    business_id,
                    shop_id,
                    target_scope,
                    template[3],
                    template[4],
                    template[5],
                    template[6],
                    template[7],
                    actor_id,
                ),
            )
            new_ids.append(UUID(str((await cursor.fetchone())[0])))

        if payload.target_mode == "business":
            await connection.execute(
                "update public.businesses set status = %s, updated_at = now() where id = %s",
                (template[3], business_id),
            )
            await connection.execute(
                """
                update public.shops
                set status = 'active', updated_at = now()
                where business_id = %s and status not in ('offboarding', 'archived')
                """,
                (business_id,),
            )
        else:
            await connection.execute(
                """
                update public.businesses
                set status = 'active', updated_at = now()
                where id = %s
                """,
                (business_id,),
            )
            await connection.execute(
                """
                update public.shops
                set status = %s, updated_at = now()
                where business_id = %s and status <> 'archived'
                """,
                (template[3], business_id),
            )

        response = BillingModeTransitionResponse(
            business_id=business_id,
            billing_mode=payload.target_mode,
            subscription_ids=new_ids,
        )
        await _write_event(
            connection,
            business_id=business_id,
            shop_id=None,
            actor_id=actor_id,
            action="subscription.billing_mode_changed",
            entity_type="business",
            entity_id=business_id,
            request_id=request_id,
            details={
                "business_id": str(business_id),
                "from": business[0],
                "to": payload.target_mode,
                "reason": payload.reason,
                "subscription_ids": [str(value) for value in new_ids],
            },
        )
        await _complete_idempotency(
            connection,
            scope=f"platform.business.billing_mode:{business_id}",
            actor_id=actor_id,
            key=idempotency_key,
            response_status=200,
            response=response,
        )
        return response


async def expire_due_subscriptions(
    pool: Any,
    *,
    at: datetime | None = None,
    batch_size: int = 100,
) -> int:
    checked_at = at or datetime.now(UTC)
    async with pool.connection(timeout=5) as connection, connection.transaction():
        await connection.execute("set local statement_timeout = '10s'")
        cursor = await connection.execute(
            """
            select id, business_id, shop_id, scope::text
            from public.subscriptions
            where status = 'active'
              and (
                manual_override_until is null
                or manual_override_until <= %s
              )
              and %s >= (
                ((paid_until + 1)::timestamp + interval '5 minutes')
                at time zone 'Asia/Dubai'
              )
            order by id
            for update skip locked
            limit %s
            """,
            (checked_at, checked_at, batch_size),
        )
        due = await cursor.fetchall()
        for subscription_id, business_id, shop_id, scope in due:
            await connection.execute(
                """
                update public.subscriptions
                set status = 'expired',
                    status_changed_by = null,
                    updated_at = now()
                where id = %s and status = 'active'
                """,
                (subscription_id,),
            )
            await _sync_subject_status(
                connection,
                business_id=business_id,
                shop_id=shop_id,
                scope=scope,
                status="expired",
            )
            await _write_event(
                connection,
                business_id=business_id,
                shop_id=shop_id,
                actor_id=None,
                action="subscription.expired",
                entity_type="subscription",
                entity_id=subscription_id,
                request_id=f"expiry-{checked_at.date().isoformat()}",
                details={"subscription_id": str(subscription_id)},
            )
        return len(due)
