import re
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field

from app.core.telegram import callback_data
from app.services.payout_service import (
    AdvanceRequest,
    PayoutActionRequest,
    PayoutPayRequest,
    approve_payout_run,
    grant_advance,
    pay_payout_run,
)
from app.services.report_service import get_business_overview, get_shop_report

MONEY_INPUT = re.compile(r"^(?:[1-9][0-9]{0,11})(?:\.[0-9]{1,2})?$")


class OwnerMenuExpiredError(Exception):
    """The owner callback or input is stale or outside the owned business."""


class OwnerFlowResponse(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    text: str
    keyboard: InlineKeyboardMarkup | None = None


class OwnerSession(BaseModel):
    model_config = ConfigDict(extra="forbid")

    step: Literal[
        "shop",
        "advance_barber",
        "advance_cash",
        "advance_amount",
        "advance_confirm",
        "payout_select",
        "payout_approve_confirm",
        "payout_cash",
        "payout_pay_confirm",
    ]
    shop_ids: list[UUID] = Field(default_factory=list, max_length=100)
    shop_id: UUID
    barber_ids: list[UUID] = Field(default_factory=list, max_length=100)
    cash_shift_ids: list[UUID] = Field(default_factory=list, max_length=20)
    payout_run_ids: list[UUID] = Field(default_factory=list, max_length=20)
    barber_membership_id: UUID | None = None
    cash_shift_id: UUID | None = None
    payout_run_id: UUID | None = None
    amount: Decimal | None = Field(default=None, gt=0, max_digits=14, decimal_places=2)


def _keyboard(rows: tuple[tuple[tuple[str, str], ...], ...]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=label, callback_data=callback_data(action))
                for label, action in row
            ]
            for row in rows
        ]
    )


async def _require_owner(
    connection: Any,
    *,
    actor_id: UUID,
    business_id: UUID,
    telegram_user_id: int,
) -> None:
    cursor = await connection.execute(
        """
        select 1
        from public.business_owners bo
        join public.user_profiles up on up.auth_user_id = bo.auth_user_id and up.active
        join public.businesses b on b.id = bo.business_id and b.status = 'active'
        where bo.auth_user_id = %s and bo.business_id = %s
          and bo.telegram_user_id = %s and bo.active
          and not exists (
            select 1 from public.telegram_user_blocks tub
            where tub.telegram_user_id = bo.telegram_user_id
              and (tub.expires_at is null or tub.expires_at > now())
          )
        """,
        (actor_id, business_id, telegram_user_id),
    )
    if await cursor.fetchone() is None:
        raise OwnerMenuExpiredError


async def _shops(connection: Any, *, business_id: UUID) -> list[tuple[Any, ...]]:
    cursor = await connection.execute(
        """
        select id, name, timezone
        from public.shops
        where business_id = %s and status = 'active'
        order by name, id
        limit 100
        """,
        (business_id,),
    )
    return list(await cursor.fetchall())


async def _save_session(
    connection: Any,
    *,
    bot_id: UUID,
    business_id: UUID,
    shop_id: UUID,
    telegram_user_id: int,
    state: OwnerSession,
) -> None:
    await connection.execute(
        """
        insert into public.telegram_sessions (
          bot_id, business_id, shop_id, telegram_user_id, bot_role, state, payload
        ) values (%s, %s, %s, %s, 'owner', 'owner_operations', %s)
        on conflict (bot_id, telegram_user_id) do update
        set business_id = excluded.business_id, shop_id = excluded.shop_id,
            bot_role = excluded.bot_role, state = excluded.state,
            payload = excluded.payload, updated_at = now()
        """,
        (bot_id, business_id, shop_id, telegram_user_id, Jsonb(state.model_dump(mode="json"))),
    )


async def _load_session(
    connection: Any,
    *,
    bot_id: UUID,
    business_id: UUID,
    telegram_user_id: int,
) -> OwnerSession | None:
    cursor = await connection.execute(
        """
        select payload
        from public.telegram_sessions
        where bot_id = %s and telegram_user_id = %s and business_id = %s
          and bot_role = 'owner' and state = 'owner_operations'
          and updated_at >= now() - interval '15 minutes'
        for update
        """,
        (bot_id, telegram_user_id, business_id),
    )
    row = await cursor.fetchone()
    if row is None:
        return None
    try:
        return OwnerSession.model_validate(row[0])
    except Exception as exc:
        raise OwnerMenuExpiredError from exc


def _day_period(timezone: str, *, month: bool = False) -> tuple[datetime, datetime]:
    zone = ZoneInfo(timezone)
    local_now = datetime.now(UTC).astimezone(zone)
    start_date = local_now.date().replace(day=1) if month else local_now.date()
    start = datetime.combine(start_date, time.min, tzinfo=zone)
    if month:
        next_month = (start_date.replace(day=28) + timedelta(days=4)).replace(day=1)
        end = datetime.combine(next_month, time.min, tzinfo=zone)
    else:
        end = datetime.combine(start_date + timedelta(days=1), time.min, tzinfo=zone)
    return start.astimezone(UTC), end.astimezone(UTC)


async def _shop_context(
    connection: Any,
    *,
    business_id: UUID,
    selected_shop_id: UUID,
) -> tuple[UUID, str, str]:
    shops = await _shops(connection, business_id=business_id)
    selected = next((row for row in shops if UUID(str(row[0])) == selected_shop_id), None)
    if selected is None:
        raise OwnerMenuExpiredError
    return UUID(str(selected[0])), str(selected[1]), str(selected[2])


def _shop_report_text(label: str, report: Any) -> str:
    totals = report.totals
    lines = [
        label,
        f"Bookings completed: {totals.bookings_completed}",
        f"Sales: {totals.sale_count}",
        f"Net sales: AED {totals.net_grand:.2f}",
        f"VAT: AED {totals.net_vat:.2f}",
        f"Cash/Card: AED {totals.cash_tender:.2f} / AED {totals.card_tender:.2f}",
        f"Advance outstanding: AED {totals.advance_outstanding:.2f}",
    ]
    if report.barbers:
        lines.append("Barbers:")
        lines.extend(
            f"{row.display_name}: service AED {row.service_gross:.2f}, "
            f"commission AED {row.commission_earnings:.2f}, tips AED {row.tip_earnings:.2f}"
            for row in report.barbers
        )
    return "\n".join(lines)


def _payout_total(items: list[Any]) -> Decimal:
    return sum((Decimal(item.net_paid) for item in items), Decimal("0.00"))


async def handle_owner_callback(
    pool: Any,
    *,
    bot_id: UUID,
    business_id: UUID,
    bot_shop_id: UUID,
    actor_id: UUID,
    telegram_user_id: int,
    callback: str,
    request_id: str,
) -> OwnerFlowResponse:
    if not callback.startswith("v1."):
        raise OwnerMenuExpiredError
    action = callback[3:]
    report_operation: tuple[str, UUID, str] | None = None
    advance_state: OwnerSession | None = None
    payout_action: tuple[Literal["approve", "pay"], OwnerSession] | None = None
    async with pool.connection(timeout=5) as connection, connection.transaction():
        await _require_owner(
            connection,
            actor_id=actor_id,
            business_id=business_id,
            telegram_user_id=telegram_user_id,
        )
        state = await _load_session(
            connection,
            bot_id=bot_id,
            business_id=business_id,
            telegram_user_id=telegram_user_id,
        )
        selected_shop_id = state.shop_id if state is not None else bot_shop_id
        selected_shop_id, shop_name, shop_timezone = await _shop_context(
            connection, business_id=business_id, selected_shop_id=selected_shop_id
        )
        if action == "o01":
            report_operation = ("business", selected_shop_id, "Asia/Dubai")
        elif action == "o02":
            shops = await _shops(connection, business_id=business_id)
            state = OwnerSession(
                step="shop",
                shop_ids=[UUID(str(row[0])) for row in shops],
                shop_id=selected_shop_id,
            )
            await _save_session(
                connection,
                bot_id=bot_id,
                business_id=business_id,
                shop_id=selected_shop_id,
                telegram_user_id=telegram_user_id,
                state=state,
            )
            return OwnerFlowResponse(
                text="Choose an owned shop:",
                keyboard=_keyboard(
                    tuple(((str(row[1]), f"ownsh{index}"),) for index, row in enumerate(shops))
                ),
            )
        elif action.startswith("ownsh") and action[5:].isdigit():
            if state is None or state.step != "shop":
                raise OwnerMenuExpiredError
            index = int(action[5:])
            if index >= len(state.shop_ids):
                raise OwnerMenuExpiredError
            selected_shop_id, shop_name, _ = await _shop_context(
                connection,
                business_id=business_id,
                selected_shop_id=state.shop_ids[index],
            )
            state.shop_id = selected_shop_id
            await _save_session(
                connection,
                bot_id=bot_id,
                business_id=business_id,
                shop_id=selected_shop_id,
                telegram_user_id=telegram_user_id,
                state=state,
            )
            return OwnerFlowResponse(text=f"Selected shop: {shop_name}.")
        elif action in {"o03", "o04", "o05"}:
            period = "month" if action == "o04" else "day"
            report_operation = (period, selected_shop_id, shop_timezone)
        elif action == "o06":
            cursor = await connection.execute(
                """
                select coalesce(sum(outstanding_amount), 0), count(*) filter (where status = 'open')
                from public.advances
                where business_id = %s and shop_id = %s
                """,
                (business_id, selected_shop_id),
            )
            row = await cursor.fetchone()
            assert row is not None
            payout_cursor = await connection.execute(
                """
                select status::text, period_start, period_end,
                       coalesce(sum(pi.net_paid), 0)
                from public.payout_runs pr
                left join public.payout_items pi on pi.payout_run_id = pr.id
                where pr.business_id = %s and pr.shop_id = %s
                group by pr.id
                order by pr.prepared_at desc, pr.id desc limit 5
                """,
                (business_id, selected_shop_id),
            )
            payout_rows = await payout_cursor.fetchall()
            payout_lines = (
                "\n".join(
                    f"{item[0]} {item[1].date()} to {item[2].date()}: AED {item[3]:.2f}"
                    for item in payout_rows
                )
                if payout_rows
                else "No payout runs."
            )
            return OwnerFlowResponse(
                text=(
                    f"{shop_name} advances and payouts\n"
                    f"Open advances: {row[1]}\nOutstanding: AED {row[0]:.2f}\n"
                    f"Recent payouts:\n{payout_lines}"
                ),
                keyboard=_keyboard(
                    ((("Grant advance", "ownadv"), ("Manage payouts", "ownpayouts")),)
                ),
            )
        elif action == "ownadv":
            cursor = await connection.execute(
                """
                select id, display_name from public.shop_memberships
                where business_id = %s and shop_id = %s and role = 'barber' and active
                order by display_name, id limit 100
                """,
                (business_id, selected_shop_id),
            )
            rows = await cursor.fetchall()
            state = OwnerSession(
                step="advance_barber",
                shop_id=selected_shop_id,
                barber_ids=[UUID(str(row[0])) for row in rows],
            )
            await _save_session(
                connection,
                bot_id=bot_id,
                business_id=business_id,
                shop_id=selected_shop_id,
                telegram_user_id=telegram_user_id,
                state=state,
            )
            return OwnerFlowResponse(
                text="Choose the barber receiving the advance:",
                keyboard=_keyboard(
                    tuple(((str(row[1]), f"ownbar{index}"),) for index, row in enumerate(rows))
                ),
            )
        elif action.startswith("ownbar") and action[6:].isdigit():
            if state is None or state.step != "advance_barber":
                raise OwnerMenuExpiredError
            index = int(action[6:])
            if index >= len(state.barber_ids):
                raise OwnerMenuExpiredError
            cursor = await connection.execute(
                """
                select id, register_label from public.cash_shifts
                where business_id = %s and shop_id = %s and status = 'open'
                order by register_label, id limit 20
                """,
                (business_id, selected_shop_id),
            )
            rows = await cursor.fetchall()
            if not rows:
                return OwnerFlowResponse(text="Open a cash shift before granting an advance.")
            state.step = "advance_cash"
            state.barber_membership_id = state.barber_ids[index]
            state.cash_shift_ids = [UUID(str(row[0])) for row in rows]
            await _save_session(
                connection,
                bot_id=bot_id,
                business_id=business_id,
                shop_id=selected_shop_id,
                telegram_user_id=telegram_user_id,
                state=state,
            )
            return OwnerFlowResponse(
                text="Choose the cash shift:",
                keyboard=_keyboard(
                    tuple(((str(row[1]), f"owncash{index}"),) for index, row in enumerate(rows))
                ),
            )
        elif action.startswith("owncash") and action[7:].isdigit():
            if state is None or state.step != "advance_cash":
                raise OwnerMenuExpiredError
            index = int(action[7:])
            if index >= len(state.cash_shift_ids):
                raise OwnerMenuExpiredError
            state.step = "advance_amount"
            state.cash_shift_id = state.cash_shift_ids[index]
            await _save_session(
                connection,
                bot_id=bot_id,
                business_id=business_id,
                shop_id=selected_shop_id,
                telegram_user_id=telegram_user_id,
                state=state,
            )
            return OwnerFlowResponse(text="Send the advance amount in AED.")
        elif action == "ownconfirm":
            if (
                state is None
                or state.step != "advance_confirm"
                or state.barber_membership_id is None
                or state.cash_shift_id is None
                or state.amount is None
            ):
                raise OwnerMenuExpiredError
            advance_state = state
        elif action == "ownpayouts":
            cursor = await connection.execute(
                """
                select id, status::text, period_start, period_end
                from public.payout_runs
                where business_id = %s and shop_id = %s
                  and status in ('draft', 'approved')
                order by prepared_at, id limit 20
                """,
                (business_id, selected_shop_id),
            )
            rows = await cursor.fetchall()
            if not rows:
                return OwnerFlowResponse(
                    text="No draft or approved payout run requires owner action."
                )
            state = OwnerSession(
                step="payout_select",
                shop_id=selected_shop_id,
                payout_run_ids=[UUID(str(row[0])) for row in rows],
            )
            await _save_session(
                connection,
                bot_id=bot_id,
                business_id=business_id,
                shop_id=selected_shop_id,
                telegram_user_id=telegram_user_id,
                state=state,
            )
            return OwnerFlowResponse(
                text="Choose a payout run to review:",
                keyboard=_keyboard(
                    tuple(
                        (
                            (
                                f"{row[1]}: {row[2].date()} to {row[3].date()}",
                                f"ownpr{index}",
                            ),
                        )
                        for index, row in enumerate(rows)
                    )
                ),
            )
        elif action.startswith("ownpr") and action[5:].isdigit():
            if state is None or state.step != "payout_select":
                raise OwnerMenuExpiredError
            index = int(action[5:])
            if index >= len(state.payout_run_ids):
                raise OwnerMenuExpiredError
            payout_run_id = state.payout_run_ids[index]
            cursor = await connection.execute(
                """
                select pr.status::text, pr.period_start, pr.period_end,
                       coalesce(sum(pi.net_paid), 0)
                from public.payout_runs pr
                join public.payout_items pi on pi.payout_run_id = pr.id
                where pr.id = %s and pr.business_id = %s and pr.shop_id = %s
                  and pr.status in ('draft', 'approved')
                group by pr.id
                """,
                (payout_run_id, business_id, selected_shop_id),
            )
            row = await cursor.fetchone()
            if row is None:
                raise OwnerMenuExpiredError
            amount = Decimal(row[3])
            state.payout_run_id = payout_run_id
            if row[0] == "draft":
                state.step = "payout_approve_confirm"
                await _save_session(
                    connection,
                    bot_id=bot_id,
                    business_id=business_id,
                    shop_id=selected_shop_id,
                    telegram_user_id=telegram_user_id,
                    state=state,
                )
                return OwnerFlowResponse(
                    text=(
                        f"Confirm approval for {shop_name}, {row[1].date()} to "
                        f"{row[2].date()}, gross before advance deductions AED {amount:.2f}."
                    ),
                    keyboard=_keyboard(((("Confirm payout approval", "ownpapprove"),),)),
                )
            cash_cursor = await connection.execute(
                """
                select id, register_label from public.cash_shifts
                where business_id = %s and shop_id = %s and status = 'open'
                order by register_label, id limit 20
                """,
                (business_id, selected_shop_id),
            )
            cash_rows = await cash_cursor.fetchall()
            if amount > 0 and not cash_rows:
                return OwnerFlowResponse(text="Open a cash shift before paying this payout run.")
            if amount == 0:
                state.step = "payout_pay_confirm"
                state.cash_shift_id = None
                await _save_session(
                    connection,
                    bot_id=bot_id,
                    business_id=business_id,
                    shop_id=selected_shop_id,
                    telegram_user_id=telegram_user_id,
                    state=state,
                )
                return OwnerFlowResponse(
                    text=f"Confirm zero-cash settlement for {shop_name}.",
                    keyboard=_keyboard(((("Confirm payout", "ownppay"),),)),
                )
            state.step = "payout_cash"
            state.cash_shift_ids = [UUID(str(item[0])) for item in cash_rows]
            await _save_session(
                connection,
                bot_id=bot_id,
                business_id=business_id,
                shop_id=selected_shop_id,
                telegram_user_id=telegram_user_id,
                state=state,
            )
            return OwnerFlowResponse(
                text=f"Choose the cash shift for payout AED {amount:.2f}:",
                keyboard=_keyboard(
                    tuple(
                        ((str(item[1]), f"ownpcash{index}"),)
                        for index, item in enumerate(cash_rows)
                    )
                ),
            )
        elif action.startswith("ownpcash") and action[8:].isdigit():
            if state is None or state.step != "payout_cash" or state.payout_run_id is None:
                raise OwnerMenuExpiredError
            index = int(action[8:])
            if index >= len(state.cash_shift_ids):
                raise OwnerMenuExpiredError
            cash_shift_id = state.cash_shift_ids[index]
            cursor = await connection.execute(
                """
                select coalesce(sum(pi.net_paid), 0)
                from public.payout_runs pr
                join public.payout_items pi on pi.payout_run_id = pr.id
                join public.cash_shifts cs on cs.id = %s and cs.business_id = pr.business_id
                  and cs.shop_id = pr.shop_id and cs.status = 'open'
                where pr.id = %s and pr.business_id = %s and pr.shop_id = %s
                  and pr.status = 'approved'
                group by pr.id
                """,
                (cash_shift_id, state.payout_run_id, business_id, selected_shop_id),
            )
            row = await cursor.fetchone()
            if row is None:
                raise OwnerMenuExpiredError
            state.step = "payout_pay_confirm"
            state.cash_shift_id = cash_shift_id
            await _save_session(
                connection,
                bot_id=bot_id,
                business_id=business_id,
                shop_id=selected_shop_id,
                telegram_user_id=telegram_user_id,
                state=state,
            )
            return OwnerFlowResponse(
                text=(
                    f"Confirm cash payout AED {Decimal(row[0]):.2f} for {shop_name}. "
                    "This pays the run and applies approved advance deductions once."
                ),
                keyboard=_keyboard(((("Confirm payout", "ownppay"),),)),
            )
        elif action == "ownpapprove":
            if (
                state is None
                or state.step != "payout_approve_confirm"
                or state.payout_run_id is None
            ):
                raise OwnerMenuExpiredError
            payout_action = ("approve", state)
        elif action == "ownppay":
            if state is None or state.step != "payout_pay_confirm" or state.payout_run_id is None:
                raise OwnerMenuExpiredError
            payout_action = ("pay", state)
        elif action == "o07":
            cursor = await connection.execute(
                """
                select created_at, action, entity_type
                from public.audit_log
                where business_id = %s and (shop_id is null or shop_id = %s)
                order by created_at desc, id desc limit 10
                """,
                (business_id, selected_shop_id),
            )
            rows = await cursor.fetchall()
            return OwnerFlowResponse(
                text=(
                    f"{shop_name} recent audit\n"
                    + (
                        "\n".join(f"{row[0].isoformat()} - {row[1]} - {row[2]}" for row in rows)
                        if rows
                        else "No audit record is available."
                    )
                )
            )
        elif action == "o08":
            cursor = await connection.execute(
                """
                select b.billing_mode::text, sub.scope::text, sub.status::text,
                       sub.paid_from, sub.paid_until, sub.manual_override_until
                from public.businesses b
                left join public.subscriptions sub
                  on sub.business_id = b.id and sub.status <> 'archived'
                 and ((b.billing_mode = 'business' and sub.scope = 'business'
                       and sub.shop_id is null)
                   or (b.billing_mode = 'per_shop' and sub.scope = 'shop' and sub.shop_id = %s))
                where b.id = %s
                """,
                (selected_shop_id, business_id),
            )
            row = await cursor.fetchone()
            if row is None or row[1] is None:
                return OwnerFlowResponse(text="No current subscription record is available.")
            return OwnerFlowResponse(
                text=(
                    f"Subscription: {row[2]} ({row[0]}, {row[1]})\n"
                    f"Coverage: {row[3]} to {row[4]}\n"
                    f"Override until: {row[5] or '-'}"
                )
            )
        else:
            raise OwnerMenuExpiredError

    if report_operation is not None:
        kind, selected_shop_id, timezone = report_operation
        period_start, period_end = _day_period(timezone, month=kind == "month")
        if kind == "business":
            report = await get_business_overview(
                pool,
                actor_id=actor_id,
                business_id=business_id,
                period_start=period_start,
                period_end=period_end,
                cursor=None,
                limit=100,
            )
            totals = report.totals
            return OwnerFlowResponse(
                text=(
                    "Business today\n"
                    f"Sales: {totals.sale_count}\nNet sales: AED {totals.net_grand:.2f}\n"
                    f"Cash/Card: AED {totals.cash_tender:.2f} / AED {totals.card_tender:.2f}\n"
                    f"Advance outstanding: AED {totals.advance_outstanding:.2f}"
                )
            )
        shop_report = await get_shop_report(
            pool,
            actor_id=actor_id,
            business_id=business_id,
            shop_id=selected_shop_id,
            period_start=period_start,
            period_end=period_end,
            cursor=None,
            limit=100,
        )
        label = "This month" if kind == "month" else "Shop today"
        return OwnerFlowResponse(text=_shop_report_text(label, shop_report))

    if advance_state is not None:
        result = await grant_advance(
            pool,
            actor_id=actor_id,
            business_id=business_id,
            shop_id=advance_state.shop_id,
            idempotency_key=f"telegram:{bot_id}:{request_id}",
            request_id=request_id,
            payload=AdvanceRequest(
                barber_membership_id=advance_state.barber_membership_id,
                cash_shift_id=advance_state.cash_shift_id,
                amount=advance_state.amount,
                note="Owner bot: deduct from next applicable payout",
            ),
        )
        return OwnerFlowResponse(
            text=(
                f"Advance granted: AED {result.original_amount:.2f}. "
                f"Outstanding AED {result.outstanding_amount:.2f}; deduction occurs in a "
                "payout run."
            )
        )

    assert payout_action is not None
    operation, payout_state = payout_action
    assert payout_state.payout_run_id is not None
    if operation == "approve":
        payout = await approve_payout_run(
            pool,
            actor_id=actor_id,
            business_id=business_id,
            shop_id=payout_state.shop_id,
            payout_run_id=payout_state.payout_run_id,
            idempotency_key=f"telegram:{bot_id}:{request_id}",
            request_id=request_id,
            payload=PayoutActionRequest(),
        )
        return OwnerFlowResponse(
            text=(
                "Payout approved. Net cash after advance deductions: "
                f"AED {_payout_total(payout.items):.2f}."
            )
        )
    payout = await pay_payout_run(
        pool,
        actor_id=actor_id,
        business_id=business_id,
        shop_id=payout_state.shop_id,
        payout_run_id=payout_state.payout_run_id,
        idempotency_key=f"telegram:{bot_id}:{request_id}",
        request_id=request_id,
        payload=PayoutPayRequest(cash_shift_id=payout_state.cash_shift_id),
    )
    return OwnerFlowResponse(text=f"Payout paid once: AED {_payout_total(payout.items):.2f}.")


async def handle_owner_input(
    pool: Any,
    *,
    bot_id: UUID,
    business_id: UUID,
    actor_id: UUID,
    telegram_user_id: int,
    text: str,
) -> OwnerFlowResponse:
    async with pool.connection(timeout=5) as connection, connection.transaction():
        await _require_owner(
            connection,
            actor_id=actor_id,
            business_id=business_id,
            telegram_user_id=telegram_user_id,
        )
        state = await _load_session(
            connection,
            bot_id=bot_id,
            business_id=business_id,
            telegram_user_id=telegram_user_id,
        )
        if state is None or state.step != "advance_amount":
            raise OwnerMenuExpiredError
        if text != text.strip() or MONEY_INPUT.fullmatch(text) is None:
            raise OwnerMenuExpiredError
        try:
            state.amount = Decimal(text)
        except InvalidOperation as exc:
            raise OwnerMenuExpiredError from exc
        state.step = "advance_confirm"
        await _save_session(
            connection,
            bot_id=bot_id,
            business_id=business_id,
            shop_id=state.shop_id,
            telegram_user_id=telegram_user_id,
            state=state,
        )
        return OwnerFlowResponse(
            text=(
                f"Confirm advance AED {state.amount:.2f}. This disburses cash now and creates "
                "an outstanding receivable deducted only in a later payout run."
            ),
            keyboard=_keyboard(((("Confirm advance", "ownconfirm"),),)),
        )


__all__ = [
    "OwnerFlowResponse",
    "OwnerMenuExpiredError",
    "handle_owner_callback",
    "handle_owner_input",
]
