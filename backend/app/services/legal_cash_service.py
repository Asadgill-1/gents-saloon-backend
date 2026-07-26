from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from psycopg.errors import CheckViolation, ForeignKeyViolation, UniqueViolation
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.entitlements import require_active_entitlement
from app.services.platform_operations import (
    IdempotencyConflictError,
    IdempotencyInProgressError,
    complete_idempotency,
    reserve_idempotency,
)

Money = Annotated[
    Decimal,
    Field(ge=Decimal("0"), max_digits=14, decimal_places=2),
]
PositiveMoney = Annotated[
    Decimal,
    Field(gt=Decimal("0"), max_digits=14, decimal_places=2),
]
DocumentCounterKind = Literal["sale", "credit_note"]
CashMovementType = Literal[
    "cash_sale",
    "pay_in",
    "pay_out",
    "advance",
    "payout",
    "refund",
]


class CashAccessDeniedError(Exception):
    """The actor cannot operate cash for this shop."""


class LegalProfileNotFoundError(Exception):
    """No effective legal profile covers the requested instant."""


class CashShiftNotFoundError(Exception):
    """The cash shift does not exist in the requested tenant scope."""


class CashShiftConflictError(Exception):
    """The cash operation conflicts with current durable state."""


class LegalDocumentProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_profile_id: UUID
    legal_name: str
    address: str
    vat_registered: bool
    trn: str | None
    pricing_mode: str
    document_type: str
    currency: Literal["AED"] = "AED"
    effective_from: datetime
    effective_until: datetime | None


class DocumentNumber(BaseModel):
    model_config = ConfigDict(extra="forbid")

    counter_kind: DocumentCounterKind
    fiscal_year: int
    sequence_number: int
    document_number: str


class CashShiftOpenRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    register_label: str = Field(min_length=1, max_length=64)
    opening_float: Money

    @field_validator("register_label")
    @classmethod
    def validate_register_label(cls, value: str) -> str:
        if value != value.strip():
            raise ValueError("register_label cannot have surrounding whitespace")
        return value


class CashMovementRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    movement_type: Literal["pay_in", "pay_out"]
    amount: PositiveMoney
    reason: str = Field(min_length=1, max_length=500)

    @field_validator("reason")
    @classmethod
    def validate_reason(cls, value: str) -> str:
        if value != value.strip():
            raise ValueError("reason cannot have surrounding whitespace")
        return value


class CashMovementRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    movement_type: CashMovementType
    amount: PositiveMoney
    reason: str | None = Field(default=None, min_length=1, max_length=500)
    source_entity_id: UUID | None = None

    @model_validator(mode="after")
    def validate_source(self) -> "CashMovementRecord":
        manual = self.movement_type in {"pay_in", "pay_out"}
        if manual and (self.reason is None or self.source_entity_id is not None):
            raise ValueError("manual cash movements require a reason and no source")
        if not manual and self.source_entity_id is None:
            raise ValueError("source-backed cash movements require source_entity_id")
        if self.reason is not None and self.reason != self.reason.strip():
            raise ValueError("reason cannot have surrounding whitespace")
        return self


class CashShiftCloseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    counted_cash: Money


class CashMovementResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    movement_id: UUID
    cash_shift_id: UUID
    movement_type: CashMovementType
    amount: Decimal
    expected_cash_after: Decimal
    created_at: datetime


class CashShiftResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cash_shift_id: UUID
    register_label: str
    status: Literal["open", "closed"]
    opening_float: Decimal
    cash_sales: Decimal
    pay_ins: Decimal
    pay_outs: Decimal
    advances: Decimal
    payouts: Decimal
    refunds: Decimal
    expected_cash: Decimal
    counted_cash: Decimal | None
    variance: Decimal | None
    opened_by_auth_user_id: UUID
    opened_at: datetime
    closed_by_auth_user_id: UUID | None
    closed_at: datetime | None


async def _require_operator(
    connection: Any,
    *,
    actor_id: UUID,
    business_id: UUID,
    shop_id: UUID,
) -> None:
    cursor = await connection.execute(
        """
        select 1
        from public.user_profiles up
        where up.auth_user_id = %s
          and up.active
          and (
            exists (
              select 1
              from public.platform_admins pa
              where pa.auth_user_id = up.auth_user_id and pa.active
            )
            or exists (
              select 1
              from public.business_owners bo
              where bo.auth_user_id = up.auth_user_id
                and bo.business_id = %s
                and bo.active
                and bo.is_primary
            )
            or exists (
              select 1
              from public.shop_memberships sm
              where sm.auth_user_id = up.auth_user_id
                and sm.business_id = %s
                and sm.shop_id = %s
                and sm.role in ('manager', 'receptionist')
                and sm.active
            )
          )
        for share of up
        """,
        (actor_id, business_id, business_id, shop_id),
    )
    if await cursor.fetchone() is None:
        raise CashAccessDeniedError


async def _write_event(
    connection: Any,
    *,
    business_id: UUID,
    shop_id: UUID,
    actor_id: UUID,
    action: str,
    entity_type: str,
    entity_id: UUID,
    request_id: str,
    details: dict[str, Any],
) -> None:
    await connection.execute(
        """
        insert into public.audit_log (
          business_id, shop_id, actor_type, actor_id, action,
          entity_type, entity_id, request_id, after
        )
        values (%s, %s, 'auth_user', %s, %s, %s, %s, %s, %s)
        """,
        (
            business_id,
            shop_id,
            str(actor_id),
            action,
            entity_type,
            entity_id,
            request_id,
            Jsonb(details),
        ),
    )
    await connection.execute(
        """
        insert into public.outbox_events (
          business_id, shop_id, topic, dedupe_key, payload
        )
        values (%s, %s, %s, %s, %s)
        on conflict (dedupe_key) do nothing
        """,
        (
            business_id,
            shop_id,
            action,
            f"{action}:{request_id}:{entity_id}",
            Jsonb(details),
        ),
    )


async def select_legal_document_profile(
    connection: Any,
    *,
    business_id: UUID,
    shop_id: UUID,
    at: datetime,
) -> LegalDocumentProfile:
    cursor = await connection.execute(
        """
        select
          id,
          legal_name,
          address,
          vat_registered,
          trn,
          pricing_mode::text,
          invoice_document_type::text,
          effective_from,
          effective_until
        from public.shop_legal_profiles
        where business_id = %s
          and shop_id = %s
          and effective_from <= %s
          and (effective_until is null or %s < effective_until)
        order by effective_from desc
        limit 2
        """,
        (business_id, shop_id, at, at),
    )
    rows = await cursor.fetchall()
    if len(rows) != 1:
        raise LegalProfileNotFoundError
    row = rows[0]
    return LegalDocumentProfile(
        source_profile_id=UUID(str(row[0])),
        legal_name=str(row[1]),
        address=str(row[2]),
        vat_registered=bool(row[3]),
        trn=str(row[4]) if row[4] is not None else None,
        pricing_mode=str(row[5]),
        document_type=str(row[6]),
        effective_from=row[7],
        effective_until=row[8],
    )


async def get_current_legal_document_profile(
    pool: Any,
    *,
    business_id: UUID,
    shop_id: UUID,
    at: datetime | None = None,
) -> LegalDocumentProfile:
    selected_at = at or datetime.now(UTC)
    async with pool.connection(timeout=5) as connection:
        return await select_legal_document_profile(
            connection,
            business_id=business_id,
            shop_id=shop_id,
            at=selected_at,
        )


async def allocate_document_number(
    connection: Any,
    *,
    business_id: UUID,
    shop_id: UUID,
    counter_kind: DocumentCounterKind,
    at: datetime,
) -> DocumentNumber:
    shop_cursor = await connection.execute(
        """
        select internal_code, timezone
        from public.shops
        where id = %s and business_id = %s
        """,
        (shop_id, business_id),
    )
    shop = await shop_cursor.fetchone()
    if shop is None:
        raise CashShiftNotFoundError
    try:
        fiscal_year = at.astimezone(ZoneInfo(str(shop[1]))).year
    except ZoneInfoNotFoundError as exc:
        raise CashShiftConflictError("shop timezone is invalid") from exc

    cursor = await connection.execute(
        """
        insert into public.receipt_counters (
          business_id, shop_id, fiscal_year, counter_kind, last_number
        )
        values (%s, %s, %s, %s, 1)
        on conflict (shop_id, fiscal_year, counter_kind)
        do update
          set last_number = public.receipt_counters.last_number + 1,
              updated_at = now()
        returning last_number
        """,
        (business_id, shop_id, fiscal_year, counter_kind),
    )
    row = await cursor.fetchone()
    assert row is not None
    sequence = int(row[0])
    prefix = "" if counter_kind == "sale" else "CN-"
    return DocumentNumber(
        counter_kind=counter_kind,
        fiscal_year=fiscal_year,
        sequence_number=sequence,
        document_number=f"{prefix}{shop[0]}-{fiscal_year}-{sequence:06d}",
    )


async def _cash_totals(connection: Any, cash_shift_id: UUID) -> tuple[Decimal, ...]:
    cursor = await connection.execute(
        """
        select
          cs.opening_float,
          coalesce(sum(csm.amount) filter (
            where csm.movement_type = 'cash_sale'
          ), 0),
          coalesce(sum(csm.amount) filter (
            where csm.movement_type = 'pay_in'
          ), 0),
          coalesce(sum(csm.amount) filter (
            where csm.movement_type = 'pay_out'
          ), 0),
          coalesce(sum(csm.amount) filter (
            where csm.movement_type = 'advance'
          ), 0),
          coalesce(sum(csm.amount) filter (
            where csm.movement_type = 'payout'
          ), 0),
          coalesce(sum(csm.amount) filter (
            where csm.movement_type = 'refund'
          ), 0)
        from public.cash_shifts cs
        left join public.cash_shift_movements csm
          on csm.cash_shift_id = cs.id
        where cs.id = %s
        group by cs.id
        """,
        (cash_shift_id,),
    )
    row = await cursor.fetchone()
    if row is None:
        raise CashShiftNotFoundError
    return tuple(Decimal(value) for value in row)


def _expected_cash(totals: tuple[Decimal, ...]) -> Decimal:
    opening, cash_sales, pay_ins, pay_outs, advances, payouts, refunds = totals
    return opening + cash_sales + pay_ins - pay_outs - advances - payouts - refunds


async def _shift_response(
    connection: Any,
    *,
    business_id: UUID,
    shop_id: UUID,
    cash_shift_id: UUID,
) -> CashShiftResponse:
    cursor = await connection.execute(
        """
        select
          id,
          register_label,
          status::text,
          opening_float,
          counted_cash,
          variance,
          opened_by_auth_user_id,
          opened_at,
          closed_by_auth_user_id,
          closed_at,
          expected_cash
        from public.cash_shifts
        where id = %s and business_id = %s and shop_id = %s
        """,
        (cash_shift_id, business_id, shop_id),
    )
    row = await cursor.fetchone()
    if row is None:
        raise CashShiftNotFoundError
    totals = await _cash_totals(connection, cash_shift_id)
    expected = Decimal(row[10]) if row[10] is not None else _expected_cash(totals)
    return CashShiftResponse(
        cash_shift_id=UUID(str(row[0])),
        register_label=str(row[1]),
        status=str(row[2]),
        opening_float=totals[0],
        cash_sales=totals[1],
        pay_ins=totals[2],
        pay_outs=totals[3],
        advances=totals[4],
        payouts=totals[5],
        refunds=totals[6],
        expected_cash=expected,
        counted_cash=Decimal(row[4]) if row[4] is not None else None,
        variance=Decimal(row[5]) if row[5] is not None else None,
        opened_by_auth_user_id=UUID(str(row[6])),
        opened_at=row[7],
        closed_by_auth_user_id=UUID(str(row[8])) if row[8] is not None else None,
        closed_at=row[9],
    )


async def open_cash_shift(
    pool: Any,
    *,
    actor_id: UUID,
    business_id: UUID,
    shop_id: UUID,
    idempotency_key: str,
    request_id: str,
    payload: CashShiftOpenRequest,
    at: datetime | None = None,
) -> CashShiftResponse:
    opened_at = at or datetime.now(UTC)
    scope = f"cash-shift.open:{business_id}:{shop_id}"
    try:
        async with pool.connection(timeout=5) as connection, connection.transaction():
            await _require_operator(
                connection,
                actor_id=actor_id,
                business_id=business_id,
                shop_id=shop_id,
            )
            await require_active_entitlement(
                connection,
                business_id=business_id,
                shop_id=shop_id,
                at=opened_at,
            )
            replay = await reserve_idempotency(
                connection,
                scope=scope,
                actor_id=actor_id,
                key=idempotency_key,
                payload=payload,
                expected_status=201,
            )
            if replay is not None:
                return CashShiftResponse.model_validate(replay)

            cursor = await connection.execute(
                """
                insert into public.cash_shifts (
                  business_id, shop_id, register_label, opening_float,
                  opened_by_auth_user_id, opened_at
                )
                values (%s, %s, %s, %s, %s, %s)
                returning id
                """,
                (
                    business_id,
                    shop_id,
                    payload.register_label,
                    payload.opening_float,
                    actor_id,
                    opened_at,
                ),
            )
            row = await cursor.fetchone()
            assert row is not None
            shift_id = UUID(str(row[0]))
            response = await _shift_response(
                connection,
                business_id=business_id,
                shop_id=shop_id,
                cash_shift_id=shift_id,
            )
            await _write_event(
                connection,
                business_id=business_id,
                shop_id=shop_id,
                actor_id=actor_id,
                action="cash_shift.opened",
                entity_type="cash_shift",
                entity_id=shift_id,
                request_id=request_id,
                details={
                    "cash_shift_id": str(shift_id),
                    "register_label": payload.register_label,
                    "opening_float": str(payload.opening_float),
                },
            )
            await complete_idempotency(
                connection,
                scope=scope,
                actor_id=actor_id,
                key=idempotency_key,
                response_status=201,
                response=response,
            )
            return response
    except UniqueViolation as exc:
        raise CashShiftConflictError("register already has an open shift") from exc
    except (CheckViolation, ForeignKeyViolation) as exc:
        raise CashShiftConflictError("cash shift violates durable constraints") from exc


async def record_cash_movement(
    pool: Any,
    *,
    actor_id: UUID,
    business_id: UUID,
    shop_id: UUID,
    cash_shift_id: UUID,
    idempotency_key: str,
    request_id: str,
    payload: CashMovementRecord,
    at: datetime | None = None,
) -> CashMovementResponse:
    created_at = at or datetime.now(UTC)
    scope = f"cash-shift.movement:{business_id}:{shop_id}:{cash_shift_id}"
    try:
        async with pool.connection(timeout=5) as connection, connection.transaction():
            await _require_operator(
                connection,
                actor_id=actor_id,
                business_id=business_id,
                shop_id=shop_id,
            )
            await require_active_entitlement(
                connection,
                business_id=business_id,
                shop_id=shop_id,
                at=created_at,
            )
            replay = await reserve_idempotency(
                connection,
                scope=scope,
                actor_id=actor_id,
                key=idempotency_key,
                payload=payload,
                expected_status=201,
            )
            if replay is not None:
                return CashMovementResponse.model_validate(replay)

            shift_cursor = await connection.execute(
                """
                select status::text
                from public.cash_shifts
                where id = %s and business_id = %s and shop_id = %s
                for update
                """,
                (cash_shift_id, business_id, shop_id),
            )
            shift = await shift_cursor.fetchone()
            if shift is None:
                raise CashShiftNotFoundError
            if shift[0] != "open":
                raise CashShiftConflictError("cash shift is closed")

            cursor = await connection.execute(
                """
                insert into public.cash_shift_movements (
                  business_id, shop_id, cash_shift_id, movement_type,
                  amount, reason, source_entity_id,
                  created_by_auth_user_id, created_at
                )
                values (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                returning id
                """,
                (
                    business_id,
                    shop_id,
                    cash_shift_id,
                    payload.movement_type,
                    payload.amount,
                    payload.reason,
                    payload.source_entity_id,
                    actor_id,
                    created_at,
                ),
            )
            row = await cursor.fetchone()
            assert row is not None
            movement_id = UUID(str(row[0]))
            expected_after = _expected_cash(await _cash_totals(connection, cash_shift_id))
            if expected_after < 0:
                raise CashShiftConflictError("cash movement exceeds expected physical cash")
            response = CashMovementResponse(
                movement_id=movement_id,
                cash_shift_id=cash_shift_id,
                movement_type=payload.movement_type,
                amount=payload.amount,
                expected_cash_after=expected_after,
                created_at=created_at,
            )
            await _write_event(
                connection,
                business_id=business_id,
                shop_id=shop_id,
                actor_id=actor_id,
                action="cash_shift.movement_recorded",
                entity_type="cash_shift_movement",
                entity_id=movement_id,
                request_id=request_id,
                details={
                    "cash_shift_id": str(cash_shift_id),
                    "movement_id": str(movement_id),
                    "movement_type": payload.movement_type,
                    "amount": str(payload.amount),
                    "source_entity_id": (
                        str(payload.source_entity_id)
                        if payload.source_entity_id is not None
                        else None
                    ),
                },
            )
            await complete_idempotency(
                connection,
                scope=scope,
                actor_id=actor_id,
                key=idempotency_key,
                response_status=201,
                response=response,
            )
            return response
    except UniqueViolation as exc:
        raise CashShiftConflictError("cash source was already recorded") from exc
    except (CheckViolation, ForeignKeyViolation) as exc:
        raise CashShiftConflictError("cash movement violates durable constraints") from exc


async def record_manual_cash_movement(
    pool: Any,
    *,
    actor_id: UUID,
    business_id: UUID,
    shop_id: UUID,
    cash_shift_id: UUID,
    idempotency_key: str,
    request_id: str,
    payload: CashMovementRequest,
    at: datetime | None = None,
) -> CashMovementResponse:
    return await record_cash_movement(
        pool,
        actor_id=actor_id,
        business_id=business_id,
        shop_id=shop_id,
        cash_shift_id=cash_shift_id,
        idempotency_key=idempotency_key,
        request_id=request_id,
        payload=CashMovementRecord(
            movement_type=payload.movement_type,
            amount=payload.amount,
            reason=payload.reason,
        ),
        at=at,
    )


async def close_cash_shift(
    pool: Any,
    *,
    actor_id: UUID,
    business_id: UUID,
    shop_id: UUID,
    cash_shift_id: UUID,
    idempotency_key: str,
    request_id: str,
    payload: CashShiftCloseRequest,
    at: datetime | None = None,
) -> CashShiftResponse:
    closed_at = at or datetime.now(UTC)
    scope = f"cash-shift.close:{business_id}:{shop_id}:{cash_shift_id}"
    async with pool.connection(timeout=5) as connection, connection.transaction():
        await _require_operator(
            connection,
            actor_id=actor_id,
            business_id=business_id,
            shop_id=shop_id,
        )
        await require_active_entitlement(
            connection,
            business_id=business_id,
            shop_id=shop_id,
            at=closed_at,
        )
        replay = await reserve_idempotency(
            connection,
            scope=scope,
            actor_id=actor_id,
            key=idempotency_key,
            payload=payload,
            expected_status=200,
        )
        if replay is not None:
            return CashShiftResponse.model_validate(replay)

        shift_cursor = await connection.execute(
            """
            select status::text
            from public.cash_shifts
            where id = %s and business_id = %s and shop_id = %s
            for update
            """,
            (cash_shift_id, business_id, shop_id),
        )
        shift = await shift_cursor.fetchone()
        if shift is None:
            raise CashShiftNotFoundError
        if shift[0] != "open":
            raise CashShiftConflictError("cash shift is closed")

        expected = _expected_cash(await _cash_totals(connection, cash_shift_id))
        variance = payload.counted_cash - expected
        await connection.execute(
            """
            update public.cash_shifts
            set status = 'closed',
                expected_cash = %s,
                counted_cash = %s,
                variance = %s,
                closed_by_auth_user_id = %s,
                closed_at = %s
            where id = %s
            """,
            (
                expected,
                payload.counted_cash,
                variance,
                actor_id,
                closed_at,
                cash_shift_id,
            ),
        )
        response = await _shift_response(
            connection,
            business_id=business_id,
            shop_id=shop_id,
            cash_shift_id=cash_shift_id,
        )
        await _write_event(
            connection,
            business_id=business_id,
            shop_id=shop_id,
            actor_id=actor_id,
            action="cash_shift.closed",
            entity_type="cash_shift",
            entity_id=cash_shift_id,
            request_id=request_id,
            details={
                "cash_shift_id": str(cash_shift_id),
                "expected_cash": str(expected),
                "counted_cash": str(payload.counted_cash),
                "variance": str(variance),
            },
        )
        await complete_idempotency(
            connection,
            scope=scope,
            actor_id=actor_id,
            key=idempotency_key,
            response_status=200,
            response=response,
        )
        return response


async def get_cash_shift(
    pool: Any,
    *,
    business_id: UUID,
    shop_id: UUID,
    cash_shift_id: UUID,
) -> CashShiftResponse:
    async with pool.connection(timeout=5) as connection:
        return await _shift_response(
            connection,
            business_id=business_id,
            shop_id=shop_id,
            cash_shift_id=cash_shift_id,
        )


__all__ = [
    "CashAccessDeniedError",
    "CashMovementRecord",
    "CashMovementRequest",
    "CashMovementResponse",
    "CashShiftCloseRequest",
    "CashShiftConflictError",
    "CashShiftNotFoundError",
    "CashShiftOpenRequest",
    "CashShiftResponse",
    "DocumentNumber",
    "IdempotencyConflictError",
    "IdempotencyInProgressError",
    "LegalDocumentProfile",
    "LegalProfileNotFoundError",
    "allocate_document_number",
    "close_cash_shift",
    "get_cash_shift",
    "get_current_legal_document_profile",
    "open_cash_shift",
    "record_cash_movement",
    "record_manual_cash_movement",
    "select_legal_document_profile",
]
