import re
from decimal import Decimal, InvalidOperation
from typing import Any, Literal
from uuid import UUID

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field

from app.core.telegram import callback_data
from app.services.legal_cash_service import (
    CashMovementRequest,
    CashShiftCloseRequest,
    CashShiftOpenRequest,
    close_cash_shift,
    open_cash_shift,
    record_manual_cash_movement,
)
from app.services.reception_bot_flow import require_receptionist
from app.services.report_service import get_reception_eod_report

MONEY_INPUT = re.compile(r"^(?:0|[1-9][0-9]{0,11})(?:\.[0-9]{1,2})?$")


class ReceptionCashExpiredError(Exception):
    """The cash-flow callback or input no longer matches a live session."""


class ReceptionCashResponse(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    text: str
    keyboard: InlineKeyboardMarkup | None = None


class CashSession(BaseModel):
    model_config = ConfigDict(extra="forbid")

    step: Literal[
        "choose_shift",
        "open_register",
        "open_float",
        "movement_amount",
        "movement_reason",
        "close_counted",
        "complete",
    ]
    cash_shift_ids: list[UUID] = Field(default_factory=list, max_length=20)
    cash_shift_id: UUID | None = None
    register_label: str | None = Field(default=None, max_length=64)
    movement_type: Literal["pay_in", "pay_out"] | None = None
    amount: Decimal | None = Field(default=None, ge=0, max_digits=14, decimal_places=2)
    last_request_id: str | None = Field(default=None, max_length=255)
    last_response_text: str | None = Field(default=None, max_length=1024)


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


async def _save_session(
    connection: Any,
    *,
    bot_id: UUID,
    business_id: UUID,
    shop_id: UUID,
    telegram_user_id: int,
    state: CashSession,
) -> None:
    await connection.execute(
        """
        insert into public.telegram_sessions (
          bot_id, business_id, shop_id, telegram_user_id, bot_role, state, payload
        ) values (%s, %s, %s, %s, 'receptionist', 'reception_cash', %s)
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
    shop_id: UUID,
    telegram_user_id: int,
) -> CashSession:
    cursor = await connection.execute(
        """
        select payload
        from public.telegram_sessions
        where bot_id = %s and telegram_user_id = %s
          and business_id = %s and shop_id = %s and bot_role = 'receptionist'
          and state = 'reception_cash'
          and updated_at >= now() - interval '15 minutes'
        for update
        """,
        (bot_id, telegram_user_id, business_id, shop_id),
    )
    row = await cursor.fetchone()
    if row is None:
        raise ReceptionCashExpiredError
    try:
        return CashSession.model_validate(row[0])
    except Exception as exc:
        raise ReceptionCashExpiredError from exc


async def _open_shifts(
    connection: Any, *, business_id: UUID, shop_id: UUID
) -> list[tuple[Any, ...]]:
    cursor = await connection.execute(
        """
        select id, register_label, opening_float,
               opening_float
                 + coalesce((
                     select sum(case
                       when csm.movement_type in ('cash_sale', 'pay_in') then csm.amount
                       else -csm.amount end)
                     from public.cash_shift_movements csm
                     where csm.cash_shift_id = cs.id
                   ), 0) as expected_cash,
               opened_at
        from public.cash_shifts cs
        where business_id = %s and shop_id = %s and status = 'open'
        order by register_label, id
        limit 20
        """,
        (business_id, shop_id),
    )
    return list(await cursor.fetchall())


def _shift_keyboard() -> InlineKeyboardMarkup:
    return _keyboard(
        (
            (("Pay in", "cashpayin"), ("Pay out", "cashpayout")),
            (("Close shift", "cashclose"), ("Refresh", "cashrefresh")),
        )
    )


def _shift_text(row: tuple[Any, ...]) -> str:
    return (
        f"Register: {row[1]}\n"
        f"Opening float: AED {Decimal(row[2]):.2f}\n"
        f"Expected cash: AED {Decimal(row[3]):.2f}\n"
        f"Opened: {row[4].isoformat()}"
    )


async def _render_cash_menu(
    connection: Any,
    *,
    bot_id: UUID,
    business_id: UUID,
    shop_id: UUID,
    telegram_user_id: int,
) -> ReceptionCashResponse:
    shifts = await _open_shifts(connection, business_id=business_id, shop_id=shop_id)
    state = CashSession(step="choose_shift", cash_shift_ids=[UUID(str(row[0])) for row in shifts])
    await _save_session(
        connection,
        bot_id=bot_id,
        business_id=business_id,
        shop_id=shop_id,
        telegram_user_id=telegram_user_id,
        state=state,
    )
    rows = [
        ((f"{row[1]} - AED {Decimal(row[3]):.2f}", f"cash{index}"),)
        for index, row in enumerate(shifts)
    ]
    rows.append((("Open shift", "cashopen"),))
    return ReceptionCashResponse(
        text=(
            f"Open cash shifts: {len(shifts)}. Choose a register:"
            if shifts
            else "No cash shift is open."
        ),
        keyboard=_keyboard(tuple(rows)),
    )


def _parse_money(text: str, *, positive: bool) -> Decimal:
    if text != text.strip() or MONEY_INPUT.fullmatch(text) is None:
        raise ReceptionCashExpiredError
    try:
        value = Decimal(text)
    except InvalidOperation as exc:
        raise ReceptionCashExpiredError from exc
    if positive and value <= 0:
        raise ReceptionCashExpiredError
    return value


async def handle_reception_cash_callback(
    pool: Any,
    *,
    bot_id: UUID,
    business_id: UUID,
    shop_id: UUID,
    actor_id: UUID,
    telegram_user_id: int,
    callback: str,
) -> ReceptionCashResponse:
    if not callback.startswith("v1."):
        raise ReceptionCashExpiredError
    action = callback[3:]
    async with pool.connection(timeout=5) as connection, connection.transaction():
        await require_receptionist(
            connection,
            actor_id=actor_id,
            business_id=business_id,
            shop_id=shop_id,
            telegram_user_id=telegram_user_id,
        )
        if action in {"r05", "cashrefresh"}:
            return await _render_cash_menu(
                connection,
                bot_id=bot_id,
                business_id=business_id,
                shop_id=shop_id,
                telegram_user_id=telegram_user_id,
            )
        state = await _load_session(
            connection,
            bot_id=bot_id,
            business_id=business_id,
            shop_id=shop_id,
            telegram_user_id=telegram_user_id,
        )
        if action == "cashopen":
            state = CashSession(step="open_register")
            await _save_session(
                connection,
                bot_id=bot_id,
                business_id=business_id,
                shop_id=shop_id,
                telegram_user_id=telegram_user_id,
                state=state,
            )
            return ReceptionCashResponse(text="Send the register label (1-64 characters).")
        if action.startswith("cash") and action[4:].isdigit():
            index = int(action[4:])
            if index >= len(state.cash_shift_ids):
                raise ReceptionCashExpiredError
            cash_shift_id = state.cash_shift_ids[index]
            shifts = await _open_shifts(connection, business_id=business_id, shop_id=shop_id)
            selected = next((row for row in shifts if UUID(str(row[0])) == cash_shift_id), None)
            if selected is None:
                raise ReceptionCashExpiredError
            state.cash_shift_id = cash_shift_id
            await _save_session(
                connection,
                bot_id=bot_id,
                business_id=business_id,
                shop_id=shop_id,
                telegram_user_id=telegram_user_id,
                state=state,
            )
            return ReceptionCashResponse(text=_shift_text(selected), keyboard=_shift_keyboard())
        if state.cash_shift_id is None:
            raise ReceptionCashExpiredError
        if action in {"cashpayin", "cashpayout"}:
            state.step = "movement_amount"
            state.movement_type = "pay_in" if action == "cashpayin" else "pay_out"
            state.amount = None
            await _save_session(
                connection,
                bot_id=bot_id,
                business_id=business_id,
                shop_id=shop_id,
                telegram_user_id=telegram_user_id,
                state=state,
            )
            return ReceptionCashResponse(text="Send the amount in AED (for example 25.00).")
        if action == "cashclose":
            state.step = "close_counted"
            await _save_session(
                connection,
                bot_id=bot_id,
                business_id=business_id,
                shop_id=shop_id,
                telegram_user_id=telegram_user_id,
                state=state,
            )
            return ReceptionCashResponse(text="Send the physically counted cash in AED.")
    raise ReceptionCashExpiredError


async def handle_reception_cash_input(
    pool: Any,
    *,
    bot_id: UUID,
    business_id: UUID,
    shop_id: UUID,
    actor_id: UUID,
    telegram_user_id: int,
    text: str,
    request_id: str,
) -> ReceptionCashResponse:
    operation: tuple[str, CashSession, str] | None = None
    async with pool.connection(timeout=5) as connection, connection.transaction():
        await require_receptionist(
            connection,
            actor_id=actor_id,
            business_id=business_id,
            shop_id=shop_id,
            telegram_user_id=telegram_user_id,
        )
        state = await _load_session(
            connection,
            bot_id=bot_id,
            business_id=business_id,
            shop_id=shop_id,
            telegram_user_id=telegram_user_id,
        )
        if state.step == "complete":
            if state.last_request_id == request_id and state.last_response_text is not None:
                return ReceptionCashResponse(
                    text=state.last_response_text,
                    keyboard=_keyboard(((("Refresh cash shifts", "cashrefresh"),),)),
                )
            raise ReceptionCashExpiredError
        if state.step == "open_register":
            label = text.strip()
            if label != text or not 1 <= len(label) <= 64:
                raise ReceptionCashExpiredError
            state.register_label = label
            state.step = "open_float"
            await _save_session(
                connection,
                bot_id=bot_id,
                business_id=business_id,
                shop_id=shop_id,
                telegram_user_id=telegram_user_id,
                state=state,
            )
            return ReceptionCashResponse(text="Send the opening float in AED.")
        if state.step == "open_float":
            state.amount = _parse_money(text, positive=False)
            operation = ("open", state, "")
        elif state.step == "movement_amount":
            state.amount = _parse_money(text, positive=True)
            state.step = "movement_reason"
            await _save_session(
                connection,
                bot_id=bot_id,
                business_id=business_id,
                shop_id=shop_id,
                telegram_user_id=telegram_user_id,
                state=state,
            )
            return ReceptionCashResponse(text="Send a short reason (1-500 characters).")
        elif state.step == "movement_reason":
            reason = text.strip()
            if reason != text or not 1 <= len(reason) <= 500:
                raise ReceptionCashExpiredError
            operation = ("movement", state, reason)
        elif state.step == "close_counted":
            state.amount = _parse_money(text, positive=False)
            operation = ("close", state, "")
        else:
            raise ReceptionCashExpiredError

    assert operation is not None
    kind, state, reason = operation
    key = f"telegram:{bot_id}:{request_id}"
    if kind == "open" and state.register_label is not None and state.amount is not None:
        shift = await open_cash_shift(
            pool,
            actor_id=actor_id,
            business_id=business_id,
            shop_id=shop_id,
            idempotency_key=key,
            request_id=request_id,
            payload=CashShiftOpenRequest(
                register_label=state.register_label, opening_float=state.amount
            ),
        )
        response = ReceptionCashResponse(
            text=(
                f"Cash shift opened for {shift.register_label}. "
                f"Expected cash: AED {shift.expected_cash:.2f}."
            ),
            keyboard=_keyboard(((("Refresh cash shifts", "cashrefresh"),),)),
        )
    elif (
        kind == "movement"
        and state.cash_shift_id is not None
        and state.movement_type is not None
        and state.amount is not None
    ):
        movement = await record_manual_cash_movement(
            pool,
            actor_id=actor_id,
            business_id=business_id,
            shop_id=shop_id,
            cash_shift_id=state.cash_shift_id,
            idempotency_key=key,
            request_id=request_id,
            payload=CashMovementRequest(
                movement_type=state.movement_type,
                amount=state.amount,
                reason=reason,
            ),
        )
        response = ReceptionCashResponse(
            text=(
                f"{movement.movement_type} recorded. "
                f"Expected cash: AED {movement.expected_cash_after:.2f}."
            ),
            keyboard=_keyboard(((("Refresh cash shifts", "cashrefresh"),),)),
        )
    elif kind == "close" and state.cash_shift_id is not None and state.amount is not None:
        shift = await close_cash_shift(
            pool,
            actor_id=actor_id,
            business_id=business_id,
            shop_id=shop_id,
            cash_shift_id=state.cash_shift_id,
            idempotency_key=key,
            request_id=request_id,
            payload=CashShiftCloseRequest(counted_cash=state.amount),
        )
        counted_cash = shift.counted_cash
        variance = shift.variance
        assert counted_cash is not None
        assert variance is not None
        response = ReceptionCashResponse(
            text=(
                f"Cash shift closed. Expected AED {shift.expected_cash:.2f}; "
                f"counted AED {counted_cash:.2f}; variance AED {variance:.2f}."
            ),
            keyboard=_keyboard(((("Refresh cash shifts", "cashrefresh"),),)),
        )
    else:
        raise ReceptionCashExpiredError
    async with pool.connection(timeout=5) as connection, connection.transaction():
        await _save_session(
            connection,
            bot_id=bot_id,
            business_id=business_id,
            shop_id=shop_id,
            telegram_user_id=telegram_user_id,
            state=CashSession(
                step="complete",
                last_request_id=request_id,
                last_response_text=response.text,
            ),
        )
    return response


async def handle_reception_eod_callback(
    pool: Any,
    *,
    business_id: UUID,
    shop_id: UUID,
    actor_id: UUID,
    telegram_user_id: int,
) -> ReceptionCashResponse:
    async with pool.connection(timeout=5) as connection, connection.transaction():
        await require_receptionist(
            connection,
            actor_id=actor_id,
            business_id=business_id,
            shop_id=shop_id,
            telegram_user_id=telegram_user_id,
        )
    report = await get_reception_eod_report(
        pool,
        actor_id=actor_id,
        business_id=business_id,
        shop_id=shop_id,
    )
    totals = report.totals
    return ReceptionCashResponse(
        text=(
            f"EOD report ({report.period_start.date().isoformat()})\n"
            f"Bookings completed: {totals.bookings_completed}\n"
            f"Sales: {totals.sale_count}\n"
            f"Net sales: AED {totals.net_grand:.2f}\n"
            f"VAT: AED {totals.net_vat:.2f}\n"
            f"Cash tender: AED {totals.cash_tender:.2f}\n"
            f"Card tender: AED {totals.card_tender:.2f}\n"
            f"Closed-shift variance: AED {totals.shift_variance:.2f}"
        ),
        keyboard=_keyboard(((("Refresh EOD report", "r07"),),)),
    )


async def handle_reception_advance_handoff(
    pool: Any,
    *,
    bot_id: UUID,
    business_id: UUID,
    shop_id: UUID,
    actor_id: UUID,
    telegram_user_id: int,
    request_id: str,
) -> ReceptionCashResponse:
    async with pool.connection(timeout=5) as connection, connection.transaction():
        await require_receptionist(
            connection,
            actor_id=actor_id,
            business_id=business_id,
            shop_id=shop_id,
            telegram_user_id=telegram_user_id,
        )
        cursor = await connection.execute(
            """
            select bot.id, bo.telegram_user_id
            from public.bots bot
            join public.business_owners bo
              on bo.business_id = bot.business_id
             and bo.active and bo.is_primary and bo.telegram_user_id is not null
            where bot.business_id = %s and bot.shop_id = %s
              and bot.role = 'owner' and bot.active
            order by bot.id
            limit 1
            """,
            (business_id, shop_id),
        )
        owner = await cursor.fetchone()
        if owner is None:
            return ReceptionCashResponse(
                text=(
                    "Advance grants require an owner. No active owner bot is registered; "
                    "ask the owner to use the secure dashboard."
                )
            )
        dedupe = f"telegram:advance-handoff:{bot_id}:{request_id}"
        cursor = await connection.execute(
            """
            insert into public.outbox_events (
              business_id, shop_id, topic, dedupe_key, payload
            ) values (%s, %s, 'telegram.send_message', %s, %s)
            on conflict (dedupe_key) do nothing
            returning id
            """,
            (
                business_id,
                shop_id,
                dedupe,
                Jsonb(
                    {
                        "kind": "message",
                        "bot_id": str(owner[0]),
                        "chat_id": int(owner[1]),
                        "text": (
                            "Reception requested an advance review for this shop. "
                            "Use the owner bot or secure dashboard to select the barber, "
                            "amount, payout policy, and cash shift."
                        ),
                        "keyboard": None,
                    }
                ),
            ),
        )
        if await cursor.fetchone() is not None:
            await connection.execute(
                """
                insert into public.audit_log (
                  business_id, shop_id, actor_type, actor_id, action,
                  entity_type, entity_id, request_id, after
                ) values (%s, %s, 'auth_user', %s,
                          'telegram.advance_handoff_requested', 'shop', %s, %s, %s)
                """,
                (
                    business_id,
                    shop_id,
                    actor_id,
                    shop_id,
                    request_id,
                    Jsonb({"owner_bot_id": str(owner[0])}),
                ),
            )
    return ReceptionCashResponse(
        text=(
            "Advance request sent to the owner. Only an owner or platform administrator "
            "can approve and disburse it."
        )
    )


__all__ = [
    "ReceptionCashExpiredError",
    "ReceptionCashResponse",
    "handle_reception_cash_callback",
    "handle_reception_advance_handoff",
    "handle_reception_eod_callback",
    "handle_reception_cash_input",
]
