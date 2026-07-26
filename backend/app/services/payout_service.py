from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal, cast
from uuid import UUID

from psycopg.errors import (
    CheckViolation,
    ExclusionViolation,
    ForeignKeyViolation,
    RaiseException,
    UniqueViolation,
)
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.entitlements import require_active_entitlement
from app.services.legal_cash_service import _write_event
from app.services.platform_operations import complete_idempotency, reserve_idempotency

Money = Annotated[
    Decimal,
    Field(ge=Decimal("0"), max_digits=14, decimal_places=2),
]
PositiveMoney = Annotated[
    Decimal,
    Field(gt=Decimal("0"), max_digits=14, decimal_places=2),
]
SignedMoney = Annotated[
    Decimal,
    Field(max_digits=14, decimal_places=2),
]


class FinanceAccessDeniedError(Exception):
    """The actor is not an owner or platform administrator."""


class FinanceNotFoundError(Exception):
    """The requested finance record does not exist in this shop."""


class FinanceConflictError(Exception):
    """The requested finance mutation conflicts with durable state."""


class FinanceInputError(Exception):
    """The finance request cannot produce a valid money result."""


class AdvanceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    barber_membership_id: UUID
    cash_shift_id: UUID
    amount: PositiveMoney
    note: str | None = Field(default=None, min_length=1, max_length=500)

    @field_validator("note")
    @classmethod
    def trimmed_note(cls, value: str | None) -> str | None:
        if value is not None and value != value.strip():
            raise ValueError("note must be trimmed")
        return value


class AdvanceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    advance_id: UUID
    barber_membership_id: UUID
    original_amount: Decimal
    outstanding_amount: Decimal
    status: Literal["open", "settled"]
    cash_shift_id: UUID
    given_at: datetime


class PayoutAdjustment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    barber_membership_id: UUID
    amount: SignedMoney
    reason: str = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def validate_adjustment(self) -> "PayoutAdjustment":
        if self.amount == 0:
            raise ValueError("adjustment amount cannot be zero")
        if self.reason != self.reason.strip():
            raise ValueError("adjustment reason must be trimmed")
        return self


class PayoutRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    period_start: datetime
    period_end: datetime
    adjustments: list[PayoutAdjustment] = Field(default_factory=list, max_length=100)

    @field_validator("period_start", "period_end")
    @classmethod
    def timezone_required(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("payout period timestamps must include a timezone")
        return value

    @field_validator("adjustments")
    @classmethod
    def unique_adjustments(
        cls,
        value: list[PayoutAdjustment],
    ) -> list[PayoutAdjustment]:
        barber_ids = [item.barber_membership_id for item in value]
        if len(barber_ids) != len(set(barber_ids)):
            raise ValueError("only one adjustment is allowed per barber")
        return value

    @model_validator(mode="after")
    def valid_period(self) -> "PayoutRunRequest":
        if self.period_start >= self.period_end:
            raise ValueError("payout period must be positive")
        return self


class PayoutActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PayoutPayRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cash_shift_id: UUID | None = None


class PayoutItemResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    payout_item_id: UUID
    barber_membership_id: UUID
    commission_earnings: Decimal
    tip_earnings: Decimal
    commission_reversals: Decimal
    tip_reversals: Decimal
    adjustments: Decimal
    adjustment_reason: str | None
    gross_payable: Decimal
    advance_deduction: Decimal
    net_paid: Decimal


class PayoutRunResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    payout_run_id: UUID
    period_start: datetime
    period_end: datetime
    status: Literal["draft", "approved", "paid", "cancelled"]
    cash_shift_id: UUID | None
    prepared_at: datetime
    approved_at: datetime | None
    paid_at: datetime | None
    cancelled_at: datetime | None
    items: list[PayoutItemResponse]


async def _require_finance_owner(
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
        join public.shops sh
          on sh.id = %s and sh.business_id = %s
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
                and bo.business_id = sh.business_id
                and bo.active
            )
          )
        for share of up, sh
        """,
        (shop_id, business_id, actor_id),
    )
    if await cursor.fetchone() is None:
        raise FinanceAccessDeniedError


async def _require_barber(
    connection: Any,
    *,
    business_id: UUID,
    shop_id: UUID,
    barber_membership_id: UUID,
    active: bool,
) -> None:
    cursor = await connection.execute(
        """
        select 1
        from public.shop_memberships
        where id = %s
          and business_id = %s
          and shop_id = %s
          and role = 'barber'
          and (%s = false or active)
        for share
        """,
        (barber_membership_id, business_id, shop_id, active),
    )
    if await cursor.fetchone() is None:
        raise FinanceNotFoundError


async def _require_open_shift(
    connection: Any,
    *,
    business_id: UUID,
    shop_id: UUID,
    cash_shift_id: UUID,
) -> None:
    cursor = await connection.execute(
        """
        select 1
        from public.cash_shifts
        where id = %s
          and business_id = %s
          and shop_id = %s
          and status = 'open'
        for update
        """,
        (cash_shift_id, business_id, shop_id),
    )
    if await cursor.fetchone() is None:
        raise FinanceConflictError("cash shift is not open")


async def grant_advance(
    pool: Any,
    *,
    actor_id: UUID,
    business_id: UUID,
    shop_id: UUID,
    idempotency_key: str,
    request_id: str,
    payload: AdvanceRequest,
    at: datetime | None = None,
) -> AdvanceResponse:
    created_at = at or datetime.now(UTC)
    scope = f"advance.grant:{business_id}:{shop_id}"
    try:
        async with pool.connection(timeout=5) as connection, connection.transaction():
            await _require_finance_owner(
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
                return AdvanceResponse.model_validate(replay)

            await _require_barber(
                connection,
                business_id=business_id,
                shop_id=shop_id,
                barber_membership_id=payload.barber_membership_id,
                active=True,
            )
            await _require_open_shift(
                connection,
                business_id=business_id,
                shop_id=shop_id,
                cash_shift_id=payload.cash_shift_id,
            )
            cursor = await connection.execute(
                """
                insert into public.advances (
                  business_id, shop_id, barber_membership_id, cash_shift_id,
                  original_amount, outstanding_amount, note,
                  given_by_auth_user_id, given_at
                )
                values (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                returning id, given_at
                """,
                (
                    business_id,
                    shop_id,
                    payload.barber_membership_id,
                    payload.cash_shift_id,
                    payload.amount,
                    payload.amount,
                    payload.note,
                    actor_id,
                    created_at,
                ),
            )
            row = await cursor.fetchone()
            assert row is not None
            advance_id = UUID(str(row[0]))

            journal_cursor = await connection.execute(
                """
                insert into public.journal_entries (
                  business_id, shop_id, source_type, source_entity_id,
                  idempotency_key, actor_auth_user_id, created_at
                )
                values (%s, %s, 'advance', %s, %s, %s, %s)
                returning id
                """,
                (
                    business_id,
                    shop_id,
                    advance_id,
                    idempotency_key,
                    actor_id,
                    created_at,
                ),
            )
            journal_row = await journal_cursor.fetchone()
            assert journal_row is not None
            for account, barber_id, debit, credit in (
                (
                    "advance_receivable",
                    payload.barber_membership_id,
                    payload.amount,
                    Decimal("0.00"),
                ),
                ("cash", None, Decimal("0.00"), payload.amount),
            ):
                await connection.execute(
                    """
                    insert into public.journal_postings (
                      business_id, shop_id, journal_entry_id, account_code,
                      barber_membership_id, debit, credit, created_at
                    )
                    values (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        business_id,
                        shop_id,
                        journal_row[0],
                        account,
                        barber_id,
                        debit,
                        credit,
                        created_at,
                    ),
                )
            await connection.execute(
                """
                insert into public.cash_shift_movements (
                  business_id, shop_id, cash_shift_id, movement_type,
                  amount, reason, source_entity_id,
                  created_by_auth_user_id, created_at
                )
                values (%s, %s, %s, 'advance', %s, %s, %s, %s, %s)
                """,
                (
                    business_id,
                    shop_id,
                    payload.cash_shift_id,
                    payload.amount,
                    payload.note,
                    advance_id,
                    actor_id,
                    created_at,
                ),
            )

            response = AdvanceResponse(
                advance_id=advance_id,
                barber_membership_id=payload.barber_membership_id,
                original_amount=payload.amount,
                outstanding_amount=payload.amount,
                status="open",
                cash_shift_id=payload.cash_shift_id,
                given_at=row[1],
            )
            await _write_event(
                connection,
                business_id=business_id,
                shop_id=shop_id,
                actor_id=actor_id,
                action="advance.granted",
                entity_type="advance",
                entity_id=advance_id,
                request_id=request_id,
                details={
                    "advance_id": str(advance_id),
                    "barber_membership_id": str(payload.barber_membership_id),
                    "amount": str(payload.amount),
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
    except (FinanceAccessDeniedError, FinanceNotFoundError, FinanceConflictError):
        raise
    except (CheckViolation, ForeignKeyViolation, RaiseException) as exc:
        raise FinanceConflictError("advance violates durable constraints") from exc
    except UniqueViolation as exc:
        raise FinanceConflictError("advance already exists") from exc


async def _source_totals(
    connection: Any,
    *,
    business_id: UUID,
    shop_id: UUID,
    period_start: datetime,
    period_end: datetime,
) -> dict[UUID, tuple[Decimal, Decimal, Decimal, Decimal]]:
    cursor = await connection.execute(
        """
        with source_rows as (
          select
            tic.barber_membership_id,
            sum(tic.barber_commission) commission_earnings,
            0::numeric tip_earnings,
            0::numeric commission_reversals,
            0::numeric tip_reversals
          from public.transaction_item_commissions tic
          join public.transactions t on t.id = tic.transaction_id
          where t.business_id = %s
            and t.shop_id = %s
            and t.created_at >= %s
            and t.created_at < %s
          group by tic.barber_membership_id
          union all
          select
            t.barber_membership_id,
            0::numeric,
            sum(t.tip_total),
            0::numeric,
            0::numeric
          from public.transactions t
          where t.business_id = %s
            and t.shop_id = %s
            and t.created_at >= %s
            and t.created_at < %s
          group by t.barber_membership_id
          union all
          select
            tcic.barber_membership_id,
            0::numeric,
            0::numeric,
            sum(tcic.barber_commission_refund),
            0::numeric
          from public.transaction_correction_item_commissions tcic
          join public.transaction_corrections tc on tc.id = tcic.correction_id
          where tc.business_id = %s
            and tc.shop_id = %s
            and tc.created_at >= %s
            and tc.created_at < %s
          group by tcic.barber_membership_id
          union all
          select
            tc.barber_membership_id,
            0::numeric,
            0::numeric,
            0::numeric,
            sum(tc.tip_refund)
          from public.transaction_corrections tc
          where tc.business_id = %s
            and tc.shop_id = %s
            and tc.created_at >= %s
            and tc.created_at < %s
          group by tc.barber_membership_id
        )
        select
          barber_membership_id,
          sum(commission_earnings),
          sum(tip_earnings),
          sum(commission_reversals),
          sum(tip_reversals)
        from source_rows
        group by barber_membership_id
        order by barber_membership_id
        """,
        (
            business_id,
            shop_id,
            period_start,
            period_end,
            business_id,
            shop_id,
            period_start,
            period_end,
            business_id,
            shop_id,
            period_start,
            period_end,
            business_id,
            shop_id,
            period_start,
            period_end,
        ),
    )
    rows = await cursor.fetchall()
    return {
        UUID(str(row[0])): (
            Decimal(row[1]),
            Decimal(row[2]),
            Decimal(row[3]),
            Decimal(row[4]),
        )
        for row in rows
    }


async def _payout_response(
    connection: Any,
    *,
    business_id: UUID,
    shop_id: UUID,
    payout_run_id: UUID,
) -> PayoutRunResponse:
    cursor = await connection.execute(
        """
        select
          period_start, period_end, status::text, cash_shift_id,
          prepared_at, approved_at, paid_at, cancelled_at
        from public.payout_runs
        where id = %s and business_id = %s and shop_id = %s
        """,
        (payout_run_id, business_id, shop_id),
    )
    run = await cursor.fetchone()
    if run is None:
        raise FinanceNotFoundError
    item_cursor = await connection.execute(
        """
        select
          id, barber_membership_id, commission_earnings, tip_earnings,
          commission_reversals, tip_reversals, adjustments,
          adjustment_reason, gross_payable, advance_deduction, net_paid
        from public.payout_items
        where payout_run_id = %s
        order by barber_membership_id
        """,
        (payout_run_id,),
    )
    items = await item_cursor.fetchall()
    return PayoutRunResponse(
        payout_run_id=payout_run_id,
        period_start=run[0],
        period_end=run[1],
        status=cast(
            Literal["draft", "approved", "paid", "cancelled"],
            str(run[2]),
        ),
        cash_shift_id=UUID(str(run[3])) if run[3] is not None else None,
        prepared_at=run[4],
        approved_at=run[5],
        paid_at=run[6],
        cancelled_at=run[7],
        items=[
            PayoutItemResponse(
                payout_item_id=UUID(str(item[0])),
                barber_membership_id=UUID(str(item[1])),
                commission_earnings=Decimal(item[2]),
                tip_earnings=Decimal(item[3]),
                commission_reversals=Decimal(item[4]),
                tip_reversals=Decimal(item[5]),
                adjustments=Decimal(item[6]),
                adjustment_reason=item[7],
                gross_payable=Decimal(item[8]),
                advance_deduction=Decimal(item[9]),
                net_paid=Decimal(item[10]),
            )
            for item in items
        ],
    )


async def create_payout_run(
    pool: Any,
    *,
    actor_id: UUID,
    business_id: UUID,
    shop_id: UUID,
    idempotency_key: str,
    request_id: str,
    payload: PayoutRunRequest,
    at: datetime | None = None,
) -> PayoutRunResponse:
    created_at = at or datetime.now(UTC)
    scope = f"payout.create:{business_id}:{shop_id}"
    if payload.period_end > created_at:
        raise FinanceInputError("payout period is not closed")
    adjustments = {item.barber_membership_id: item for item in payload.adjustments}
    try:
        async with pool.connection(timeout=5) as connection, connection.transaction():
            await _require_finance_owner(
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
                return PayoutRunResponse.model_validate(replay)

            for barber_id in sorted(adjustments, key=str):
                await _require_barber(
                    connection,
                    business_id=business_id,
                    shop_id=shop_id,
                    barber_membership_id=barber_id,
                    active=False,
                )
            totals = await _source_totals(
                connection,
                business_id=business_id,
                shop_id=shop_id,
                period_start=payload.period_start,
                period_end=payload.period_end,
            )
            barber_ids = sorted(totals.keys() | adjustments.keys(), key=str)
            if not barber_ids:
                raise FinanceInputError("payout period has no financial activity")

            run_cursor = await connection.execute(
                """
                insert into public.payout_runs (
                  business_id, shop_id, period_start, period_end,
                  prepared_by_auth_user_id, prepared_at
                )
                values (%s, %s, %s, %s, %s, %s)
                returning id
                """,
                (
                    business_id,
                    shop_id,
                    payload.period_start,
                    payload.period_end,
                    actor_id,
                    created_at,
                ),
            )
            run_row = await run_cursor.fetchone()
            assert run_row is not None
            payout_run_id = UUID(str(run_row[0]))
            total_gross = Decimal("0.00")
            for barber_id in barber_ids:
                commission, tips, commission_reversals, tip_reversals = totals.get(
                    barber_id,
                    (
                        Decimal("0.00"),
                        Decimal("0.00"),
                        Decimal("0.00"),
                        Decimal("0.00"),
                    ),
                )
                adjustment = adjustments.get(barber_id)
                adjustment_amount = adjustment.amount if adjustment is not None else Decimal("0.00")
                gross = commission + tips - commission_reversals - tip_reversals + adjustment_amount
                if gross < 0:
                    raise FinanceInputError("barber payout balance cannot be negative")
                total_gross += gross
                await connection.execute(
                    """
                    insert into public.payout_items (
                      business_id, shop_id, payout_run_id,
                      barber_membership_id, commission_earnings,
                      tip_earnings, commission_reversals, tip_reversals,
                      adjustments, adjustment_reason, gross_payable, net_paid,
                      created_at
                    )
                    values (
                      %s, %s, %s, %s, %s, %s, %s, %s,
                      %s, %s, %s, %s, %s
                    )
                    """,
                    (
                        business_id,
                        shop_id,
                        payout_run_id,
                        barber_id,
                        commission,
                        tips,
                        commission_reversals,
                        tip_reversals,
                        adjustment_amount,
                        adjustment.reason if adjustment is not None else None,
                        gross,
                        gross,
                        created_at,
                    ),
                )
            if total_gross <= 0:
                raise FinanceInputError("payout run total must be positive")

            response = await _payout_response(
                connection,
                business_id=business_id,
                shop_id=shop_id,
                payout_run_id=payout_run_id,
            )
            await _write_event(
                connection,
                business_id=business_id,
                shop_id=shop_id,
                actor_id=actor_id,
                action="payout.prepared",
                entity_type="payout_run",
                entity_id=payout_run_id,
                request_id=request_id,
                details={
                    "payout_run_id": str(payout_run_id),
                    "period_start": payload.period_start.isoformat(),
                    "period_end": payload.period_end.isoformat(),
                    "gross_total": str(total_gross),
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
    except (
        FinanceAccessDeniedError,
        FinanceNotFoundError,
        FinanceInputError,
        FinanceConflictError,
    ):
        raise
    except (UniqueViolation, ExclusionViolation) as exc:
        raise FinanceConflictError("payout period already exists or overlaps") from exc
    except (CheckViolation, ForeignKeyViolation, RaiseException) as exc:
        raise FinanceConflictError("payout run violates durable constraints") from exc


async def _locked_run(
    connection: Any,
    *,
    business_id: UUID,
    shop_id: UUID,
    payout_run_id: UUID,
    expected_status: str | tuple[str, ...],
) -> tuple[Any, ...]:
    statuses = (expected_status,) if isinstance(expected_status, str) else expected_status
    cursor = await connection.execute(
        """
        select id, status::text
        from public.payout_runs
        where id = %s and business_id = %s and shop_id = %s
        for update
        """,
        (payout_run_id, business_id, shop_id),
    )
    row = await cursor.fetchone()
    if row is None:
        raise FinanceNotFoundError
    if str(row[1]) not in statuses:
        raise FinanceConflictError("payout run has an invalid status")
    return cast(tuple[Any, ...], row)


async def approve_payout_run(
    pool: Any,
    *,
    actor_id: UUID,
    business_id: UUID,
    shop_id: UUID,
    payout_run_id: UUID,
    idempotency_key: str,
    request_id: str,
    payload: PayoutActionRequest,
    at: datetime | None = None,
) -> PayoutRunResponse:
    approved_at = at or datetime.now(UTC)
    scope = f"payout.approve:{business_id}:{shop_id}:{payout_run_id}"
    try:
        async with pool.connection(timeout=5) as connection, connection.transaction():
            await _require_finance_owner(
                connection,
                actor_id=actor_id,
                business_id=business_id,
                shop_id=shop_id,
            )
            await require_active_entitlement(
                connection,
                business_id=business_id,
                shop_id=shop_id,
                at=approved_at,
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
                return PayoutRunResponse.model_validate(replay)
            await _locked_run(
                connection,
                business_id=business_id,
                shop_id=shop_id,
                payout_run_id=payout_run_id,
                expected_status="draft",
            )
            await connection.execute(
                """
                select pg_advisory_xact_lock(
                  hashtextextended('payout-approval:' || %s::text, 0)
                )
                """,
                (shop_id,),
            )
            approved_cursor = await connection.execute(
                """
                select 1
                from public.payout_runs
                where shop_id = %s and status = 'approved' and id <> %s
                """,
                (shop_id, payout_run_id),
            )
            if await approved_cursor.fetchone() is not None:
                raise FinanceConflictError("another payout run is already approved")
            item_cursor = await connection.execute(
                """
                select id, barber_membership_id, gross_payable
                from public.payout_items
                where payout_run_id = %s
                order by barber_membership_id
                for update
                """,
                (payout_run_id,),
            )
            items = await item_cursor.fetchall()
            for item in items:
                advance_cursor = await connection.execute(
                    """
                    select outstanding_amount
                    from public.advances
                    where business_id = %s
                      and shop_id = %s
                      and barber_membership_id = %s
                      and status = 'open'
                    order by given_at, id
                    for share
                    """,
                    (business_id, shop_id, item[1]),
                )
                outstanding = sum(
                    (Decimal(row[0]) for row in await advance_cursor.fetchall()),
                    Decimal("0.00"),
                )
                deduction = min(Decimal(item[2]), outstanding)
                await connection.execute(
                    """
                    update public.payout_items
                    set advance_deduction = %s,
                        net_paid = gross_payable - %s
                    where id = %s
                    """,
                    (deduction, deduction, item[0]),
                )
            await connection.execute(
                """
                update public.payout_runs
                set status = 'approved',
                    approved_by_auth_user_id = %s,
                    approved_at = %s
                where id = %s
                """,
                (actor_id, approved_at, payout_run_id),
            )

            response = await _payout_response(
                connection,
                business_id=business_id,
                shop_id=shop_id,
                payout_run_id=payout_run_id,
            )
            await _write_event(
                connection,
                business_id=business_id,
                shop_id=shop_id,
                actor_id=actor_id,
                action="payout.approved",
                entity_type="payout_run",
                entity_id=payout_run_id,
                request_id=request_id,
                details={"payout_run_id": str(payout_run_id)},
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
    except (
        FinanceAccessDeniedError,
        FinanceNotFoundError,
        FinanceConflictError,
    ):
        raise
    except UniqueViolation as exc:
        raise FinanceConflictError("another payout run is already approved") from exc
    except (CheckViolation, ForeignKeyViolation, RaiseException) as exc:
        raise FinanceConflictError("payout approval violates durable constraints") from exc


def _signed_posting(
    account: str,
    barber_id: UUID,
    amount: Decimal,
) -> tuple[str, UUID | None, Decimal, Decimal] | None:
    if amount > 0:
        return account, barber_id, amount, Decimal("0.00")
    if amount < 0:
        return account, barber_id, Decimal("0.00"), -amount
    return None


async def pay_payout_run(
    pool: Any,
    *,
    actor_id: UUID,
    business_id: UUID,
    shop_id: UUID,
    payout_run_id: UUID,
    idempotency_key: str,
    request_id: str,
    payload: PayoutPayRequest,
    at: datetime | None = None,
) -> PayoutRunResponse:
    paid_at = at or datetime.now(UTC)
    scope = f"payout.pay:{business_id}:{shop_id}:{payout_run_id}"
    try:
        async with pool.connection(timeout=5) as connection, connection.transaction():
            await _require_finance_owner(
                connection,
                actor_id=actor_id,
                business_id=business_id,
                shop_id=shop_id,
            )
            await require_active_entitlement(
                connection,
                business_id=business_id,
                shop_id=shop_id,
                at=paid_at,
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
                return PayoutRunResponse.model_validate(replay)
            await _locked_run(
                connection,
                business_id=business_id,
                shop_id=shop_id,
                payout_run_id=payout_run_id,
                expected_status="approved",
            )
            item_cursor = await connection.execute(
                """
                select
                  id, barber_membership_id, commission_earnings,
                  tip_earnings, commission_reversals, tip_reversals,
                  adjustments, advance_deduction, net_paid
                from public.payout_items
                where payout_run_id = %s
                order by barber_membership_id
                for update
                """,
                (payout_run_id,),
            )
            items = await item_cursor.fetchall()
            total_net = sum(
                (Decimal(item[8]) for item in items),
                Decimal("0.00"),
            )
            if total_net > 0:
                if payload.cash_shift_id is None:
                    raise FinanceInputError("cash payout requires an open cash shift")
                await _require_open_shift(
                    connection,
                    business_id=business_id,
                    shop_id=shop_id,
                    cash_shift_id=payload.cash_shift_id,
                )
            elif payload.cash_shift_id is not None:
                raise FinanceInputError("zero-cash payout cannot include a cash shift")

            for item in items:
                remaining = Decimal(item[7])
                if remaining == 0:
                    continue
                advance_cursor = await connection.execute(
                    """
                    select id, outstanding_amount
                    from public.advances
                    where business_id = %s
                      and shop_id = %s
                      and barber_membership_id = %s
                      and status = 'open'
                    order by given_at, id
                    for update
                    """,
                    (business_id, shop_id, item[1]),
                )
                advances = await advance_cursor.fetchall()
                for advance in advances:
                    if remaining == 0:
                        break
                    application = min(remaining, Decimal(advance[1]))
                    await connection.execute(
                        """
                        insert into public.advance_applications (
                          business_id, shop_id, advance_id, payout_item_id,
                          amount, created_at
                        )
                        values (%s, %s, %s, %s, %s, %s)
                        """,
                        (
                            business_id,
                            shop_id,
                            advance[0],
                            item[0],
                            application,
                            paid_at,
                        ),
                    )
                    new_outstanding = Decimal(advance[1]) - application
                    await connection.execute(
                        """
                        update public.advances
                        set outstanding_amount = %s,
                            status = case
                              when %s = 0 then 'settled'::public.advance_status
                              else 'open'::public.advance_status
                            end
                        where id = %s
                        """,
                        (new_outstanding, new_outstanding, advance[0]),
                    )
                    remaining -= application
                if remaining != 0:
                    raise FinanceConflictError("approved advance deduction is unavailable")

            await connection.execute(
                """
                update public.payout_runs
                set status = 'paid',
                    paid_by_auth_user_id = %s,
                    paid_at = %s,
                    cash_shift_id = %s
                where id = %s
                """,
                (actor_id, paid_at, payload.cash_shift_id, payout_run_id),
            )
            journal_cursor = await connection.execute(
                """
                insert into public.journal_entries (
                  business_id, shop_id, source_type, source_entity_id,
                  idempotency_key, actor_auth_user_id, created_at
                )
                values (%s, %s, 'payout', %s, %s, %s, %s)
                returning id
                """,
                (
                    business_id,
                    shop_id,
                    payout_run_id,
                    idempotency_key,
                    actor_id,
                    paid_at,
                ),
            )
            journal_row = await journal_cursor.fetchone()
            assert journal_row is not None
            postings: list[tuple[str, UUID | None, Decimal, Decimal]] = []
            for item in items:
                barber_id = UUID(str(item[1]))
                for account, amount in (
                    (
                        "barber_payable",
                        Decimal(item[2]) - Decimal(item[4]),
                    ),
                    ("tip_payable", Decimal(item[3]) - Decimal(item[5])),
                    ("payout_adjustments", Decimal(item[6])),
                ):
                    posting = _signed_posting(account, barber_id, amount)
                    if posting is not None:
                        postings.append(posting)
                if Decimal(item[7]) > 0:
                    postings.append(
                        (
                            "advance_receivable",
                            barber_id,
                            Decimal("0.00"),
                            Decimal(item[7]),
                        )
                    )
            if total_net > 0:
                postings.append(
                    ("cash", None, Decimal("0.00"), total_net),
                )
            for account, posting_barber_id, debit, credit in postings:
                await connection.execute(
                    """
                    insert into public.journal_postings (
                      business_id, shop_id, journal_entry_id, account_code,
                      barber_membership_id, debit, credit, created_at
                    )
                    values (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        business_id,
                        shop_id,
                        journal_row[0],
                        account,
                        posting_barber_id,
                        debit,
                        credit,
                        paid_at,
                    ),
                )
            if total_net > 0:
                await connection.execute(
                    """
                    insert into public.cash_shift_movements (
                      business_id, shop_id, cash_shift_id, movement_type,
                      amount, source_entity_id, created_by_auth_user_id,
                      created_at
                    )
                    values (%s, %s, %s, 'payout', %s, %s, %s, %s)
                    """,
                    (
                        business_id,
                        shop_id,
                        payload.cash_shift_id,
                        total_net,
                        payout_run_id,
                        actor_id,
                        paid_at,
                    ),
                )

            response = await _payout_response(
                connection,
                business_id=business_id,
                shop_id=shop_id,
                payout_run_id=payout_run_id,
            )
            await _write_event(
                connection,
                business_id=business_id,
                shop_id=shop_id,
                actor_id=actor_id,
                action="payout.paid",
                entity_type="payout_run",
                entity_id=payout_run_id,
                request_id=request_id,
                details={
                    "payout_run_id": str(payout_run_id),
                    "net_paid": str(total_net),
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
    except (
        FinanceAccessDeniedError,
        FinanceNotFoundError,
        FinanceInputError,
        FinanceConflictError,
    ):
        raise
    except (UniqueViolation, ExclusionViolation) as exc:
        raise FinanceConflictError("payout settlement already exists") from exc
    except (CheckViolation, ForeignKeyViolation, RaiseException) as exc:
        raise FinanceConflictError("payout settlement violates durable constraints") from exc


async def cancel_payout_run(
    pool: Any,
    *,
    actor_id: UUID,
    business_id: UUID,
    shop_id: UUID,
    payout_run_id: UUID,
    idempotency_key: str,
    request_id: str,
    payload: PayoutActionRequest,
    at: datetime | None = None,
) -> PayoutRunResponse:
    cancelled_at = at or datetime.now(UTC)
    scope = f"payout.cancel:{business_id}:{shop_id}:{payout_run_id}"
    try:
        async with pool.connection(timeout=5) as connection, connection.transaction():
            await _require_finance_owner(
                connection,
                actor_id=actor_id,
                business_id=business_id,
                shop_id=shop_id,
            )
            await require_active_entitlement(
                connection,
                business_id=business_id,
                shop_id=shop_id,
                at=cancelled_at,
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
                return PayoutRunResponse.model_validate(replay)
            await _locked_run(
                connection,
                business_id=business_id,
                shop_id=shop_id,
                payout_run_id=payout_run_id,
                expected_status=("draft", "approved"),
            )
            await connection.execute(
                """
                update public.payout_runs
                set status = 'cancelled',
                    cancelled_by_auth_user_id = %s,
                    cancelled_at = %s
                where id = %s
                """,
                (actor_id, cancelled_at, payout_run_id),
            )
            response = await _payout_response(
                connection,
                business_id=business_id,
                shop_id=shop_id,
                payout_run_id=payout_run_id,
            )
            await _write_event(
                connection,
                business_id=business_id,
                shop_id=shop_id,
                actor_id=actor_id,
                action="payout.cancelled",
                entity_type="payout_run",
                entity_id=payout_run_id,
                request_id=request_id,
                details={"payout_run_id": str(payout_run_id)},
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
    except (
        FinanceAccessDeniedError,
        FinanceNotFoundError,
        FinanceConflictError,
    ):
        raise
    except (CheckViolation, ForeignKeyViolation, RaiseException) as exc:
        raise FinanceConflictError("payout cancellation violates durable constraints") from exc


__all__ = [
    "AdvanceRequest",
    "AdvanceResponse",
    "FinanceAccessDeniedError",
    "FinanceConflictError",
    "FinanceInputError",
    "FinanceNotFoundError",
    "PayoutActionRequest",
    "PayoutAdjustment",
    "PayoutPayRequest",
    "PayoutRunRequest",
    "PayoutRunResponse",
    "approve_payout_run",
    "cancel_payout_run",
    "create_payout_run",
    "grant_advance",
    "pay_payout_run",
]
