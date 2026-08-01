import re
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Literal, cast
from uuid import UUID

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field

from app.core.telegram import callback_data
from app.services.booking_service import BookingCreateRequest, create_booking
from app.services.checkout_calculations import calculate_line
from app.services.checkout_service import (
    CheckoutDiscount,
    CheckoutPayment,
    CheckoutRequest,
    checkout,
)
from app.services.legal_cash_service import select_legal_document_profile
from app.services.reception_bot_flow import require_receptionist

MONEY_INPUT = re.compile(r"^(?:0|[1-9][0-9]{0,11})(?:\.[0-9]{1,2})?$")
CARD_REFERENCE = re.compile(r"^[A-Za-z0-9._:/-]{1,64}$")
PAN_LIKE = re.compile(r"(?:\d[._:/-]?){12}\d")


class ReceptionSalesExpiredError(Exception):
    """The sales callback or text input no longer matches a live session."""


class ReceptionSalesResponse(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    text: str
    keyboard: InlineKeyboardMarkup | None = None


class SalesDiscount(BaseModel):
    model_config = ConfigDict(extra="forbid")

    booking_service_id: UUID
    amount: Decimal = Field(ge=0, max_digits=14, decimal_places=2)


class SalesSession(BaseModel):
    model_config = ConfigDict(extra="forbid")

    step: Literal[
        "walkin_services",
        "walkin_barber",
        "checkout_booking",
        "checkout_summary",
        "discount_amount",
        "tip_amount",
        "cash_shift",
        "split_cash_shift",
        "split_cash_amount",
        "card_reference",
        "complete",
    ]
    service_ids: list[UUID] = Field(default_factory=list, max_length=20)
    selected_service_ids: list[UUID] = Field(default_factory=list, max_length=20)
    barber_ids: list[UUID] = Field(default_factory=list, max_length=20)
    booking_ids: list[UUID] = Field(default_factory=list, max_length=20)
    booking_id: UUID | None = None
    booking_service_id: UUID | None = None
    discounts: list[SalesDiscount] = Field(default_factory=list, max_length=20)
    tip_amount: Decimal = Field(default=Decimal("0.00"), ge=0, max_digits=14, decimal_places=2)
    cash_shift_ids: list[UUID] = Field(default_factory=list, max_length=20)
    cash_shift_id: UUID | None = None
    cash_amount: Decimal | None = Field(default=None, gt=0, max_digits=14, decimal_places=2)
    payment_mode: Literal["card", "split"] | None = None
    last_request_id: str | None = Field(default=None, max_length=255)
    last_response_text: str | None = Field(default=None, max_length=1024)


class CheckoutLine(BaseModel):
    booking_service_id: UUID
    name: str
    unit_amount: Decimal
    discount: Decimal
    gross: Decimal


class CheckoutSnapshot(BaseModel):
    lines: list[CheckoutLine]
    subtotal: Decimal
    discount_total: Decimal
    vat_total: Decimal
    service_gross: Decimal
    tip_amount: Decimal
    grand_total: Decimal


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
    state: SalesSession,
) -> None:
    await connection.execute(
        """
        insert into public.telegram_sessions (
          bot_id, business_id, shop_id, telegram_user_id, bot_role, state, payload
        ) values (%s, %s, %s, %s, 'receptionist', 'reception_sales', %s)
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
) -> SalesSession:
    cursor = await connection.execute(
        """
        select payload
        from public.telegram_sessions
        where bot_id = %s and telegram_user_id = %s
          and business_id = %s and shop_id = %s and bot_role = 'receptionist'
          and state = 'reception_sales'
          and updated_at >= now() - interval '15 minutes'
        for update
        """,
        (bot_id, telegram_user_id, business_id, shop_id),
    )
    row = await cursor.fetchone()
    if row is None:
        raise ReceptionSalesExpiredError
    try:
        return SalesSession.model_validate(row[0])
    except Exception as exc:
        raise ReceptionSalesExpiredError from exc


async def _services(connection: Any, *, business_id: UUID, shop_id: UUID) -> list[tuple[Any, ...]]:
    cursor = await connection.execute(
        """
        select id, name, price_gross
        from public.services
        where business_id = %s and shop_id = %s and active
        order by sort_order, id
        limit 20
        """,
        (business_id, shop_id),
    )
    return list(await cursor.fetchall())


async def _barbers(connection: Any, *, business_id: UUID, shop_id: UUID) -> list[tuple[Any, ...]]:
    cursor = await connection.execute(
        """
        select id, display_name
        from public.shop_memberships
        where business_id = %s and shop_id = %s and role = 'barber' and active
        order by display_name, id
        limit 20
        """,
        (business_id, shop_id),
    )
    return list(await cursor.fetchall())


async def _completed_bookings(
    connection: Any, *, business_id: UUID, shop_id: UUID
) -> list[tuple[Any, ...]]:
    cursor = await connection.execute(
        """
        select b.id, b.queue_number, coalesce(c.display_name, 'Walk-in'),
               sm.display_name,
               coalesce(string_agg(bs.service_name, ', ' order by bs.sort_order), '')
        from public.bookings b
        left join public.customers c
          on c.id = b.customer_id and c.business_id = b.business_id and c.shop_id = b.shop_id
        join public.shop_memberships sm
          on sm.id = b.barber_membership_id and sm.business_id = b.business_id
         and sm.shop_id = b.shop_id
        join public.booking_services bs
          on bs.booking_id = b.id and bs.business_id = b.business_id and bs.shop_id = b.shop_id
        left join public.transactions t on t.booking_id = b.id
        where b.business_id = %s and b.shop_id = %s and b.status = 'completed'
          and t.id is null
        group by b.id, c.display_name, sm.display_name
        order by b.completed_at, b.id
        limit 20
        """,
        (business_id, shop_id),
    )
    return list(await cursor.fetchall())


async def _open_shifts(
    connection: Any, *, business_id: UUID, shop_id: UUID
) -> list[tuple[Any, ...]]:
    cursor = await connection.execute(
        """
        select id, register_label
        from public.cash_shifts
        where business_id = %s and shop_id = %s and status = 'open'
        order by register_label, id
        limit 20
        """,
        (business_id, shop_id),
    )
    return list(await cursor.fetchall())


async def _checkout_snapshot(
    connection: Any,
    *,
    business_id: UUID,
    shop_id: UUID,
    state: SalesSession,
) -> CheckoutSnapshot:
    if state.booking_id is None:
        raise ReceptionSalesExpiredError
    cursor = await connection.execute(
        """
        select bs.id, bs.service_name, bs.price_gross, bs.vat_rate
        from public.booking_services bs
        join public.bookings b
          on b.id = bs.booking_id and b.business_id = bs.business_id and b.shop_id = bs.shop_id
        left join public.transactions t on t.booking_id = b.id
        where b.id = %s and b.business_id = %s and b.shop_id = %s
          and b.status = 'completed' and t.id is null
        order by bs.sort_order, bs.id
        """,
        (state.booking_id, business_id, shop_id),
    )
    rows = await cursor.fetchall()
    if not rows:
        raise ReceptionSalesExpiredError
    legal = await select_legal_document_profile(
        connection, business_id=business_id, shop_id=shop_id, at=datetime.now(UTC)
    )
    discount_map = {item.booking_service_id: item.amount for item in state.discounts}
    lines: list[CheckoutLine] = []
    subtotal = Decimal("0.00")
    discount_total = Decimal("0.00")
    vat_total = Decimal("0.00")
    service_gross = Decimal("0.00")
    for row in rows:
        booking_service_id = UUID(str(row[0]))
        unit_amount = Decimal(row[2])
        discount = discount_map.get(booking_service_id, Decimal("0.00"))
        try:
            calculated = calculate_line(
                unit_amount=unit_amount,
                discount_input=discount,
                vat_rate=Decimal(row[3]) if legal.vat_registered else Decimal("0.00"),
                pricing_mode=cast(Literal["vat_inclusive", "vat_exclusive"], legal.pricing_mode),
            )
        except ValueError as exc:
            raise ReceptionSalesExpiredError from exc
        lines.append(
            CheckoutLine(
                booking_service_id=booking_service_id,
                name=str(row[1]),
                unit_amount=unit_amount,
                discount=discount,
                gross=calculated.line_gross,
            )
        )
        subtotal += calculated.pre_discount_gross
        discount_total += calculated.discount_gross
        vat_total += calculated.line_vat
        service_gross += calculated.line_gross
    return CheckoutSnapshot(
        lines=lines,
        subtotal=subtotal,
        discount_total=discount_total,
        vat_total=vat_total,
        service_gross=service_gross,
        tip_amount=state.tip_amount,
        grand_total=service_gross + state.tip_amount,
    )


def _summary(snapshot: CheckoutSnapshot) -> ReceptionSalesResponse:
    lines = [
        f"{line.name}: AED {line.gross:.2f}"
        + (f" (discount AED {line.discount:.2f})" if line.discount else "")
        for line in snapshot.lines
    ]
    lines.extend(
        [
            f"VAT: AED {snapshot.vat_total:.2f}",
            f"Tip: AED {snapshot.tip_amount:.2f}",
            f"Total: AED {snapshot.grand_total:.2f}",
        ]
    )
    return ReceptionSalesResponse(
        text="Checkout\n" + "\n".join(lines),
        keyboard=_keyboard(
            (
                (("Discounts", "salesdiscounts"), ("Tip", "salestip")),
                (("Pay", "salespay"), ("Cancel", "r04")),
            )
        ),
    )


def _money(text: str, *, positive: bool) -> Decimal:
    if text != text.strip() or MONEY_INPUT.fullmatch(text) is None:
        raise ReceptionSalesExpiredError
    try:
        value = Decimal(text)
    except InvalidOperation as exc:
        raise ReceptionSalesExpiredError from exc
    if positive and value <= 0:
        raise ReceptionSalesExpiredError
    return value


async def _complete_session(
    pool: Any,
    *,
    bot_id: UUID,
    business_id: UUID,
    shop_id: UUID,
    telegram_user_id: int,
    request_id: str,
    response: ReceptionSalesResponse,
) -> ReceptionSalesResponse:
    async with pool.connection(timeout=5) as connection, connection.transaction():
        await _save_session(
            connection,
            bot_id=bot_id,
            business_id=business_id,
            shop_id=shop_id,
            telegram_user_id=telegram_user_id,
            state=SalesSession(
                step="complete",
                last_request_id=request_id,
                last_response_text=response.text,
            ),
        )
    return response


async def _run_checkout(
    pool: Any,
    *,
    bot_id: UUID,
    business_id: UUID,
    shop_id: UUID,
    actor_id: UUID,
    telegram_user_id: int,
    request_id: str,
    state: SalesSession,
    payments: list[CheckoutPayment],
    cash_shift_id: UUID | None,
) -> ReceptionSalesResponse:
    if state.booking_id is None:
        raise ReceptionSalesExpiredError
    result = await checkout(
        pool,
        actor_id=actor_id,
        business_id=business_id,
        shop_id=shop_id,
        idempotency_key=f"telegram:{bot_id}:{request_id}",
        request_id=request_id,
        payload=CheckoutRequest(
            booking_id=state.booking_id,
            discounts=[
                CheckoutDiscount(booking_service_id=item.booking_service_id, amount=item.amount)
                for item in state.discounts
            ],
            payments=payments,
            tip_amount=state.tip_amount,
            cash_shift_id=cash_shift_id,
        ),
    )
    response = ReceptionSalesResponse(
        text=(
            f"Checkout complete. Receipt {result.receipt_number}.\n"
            f"Net AED {result.net_total:.2f}; VAT AED {result.vat_total:.2f}; "
            f"total AED {result.grand_total:.2f}."
        ),
        keyboard=_keyboard(((("Checkout another", "r04"),),)),
    )
    return await _complete_session(
        pool,
        bot_id=bot_id,
        business_id=business_id,
        shop_id=shop_id,
        telegram_user_id=telegram_user_id,
        request_id=request_id,
        response=response,
    )


async def handle_reception_sales_callback(
    pool: Any,
    *,
    bot_id: UUID,
    business_id: UUID,
    shop_id: UUID,
    actor_id: UUID,
    telegram_user_id: int,
    callback: str,
    request_id: str,
) -> ReceptionSalesResponse:
    if not callback.startswith("v1."):
        raise ReceptionSalesExpiredError
    action = callback[3:]
    mutation: tuple[str, SalesSession, UUID | None] | None = None
    async with pool.connection(timeout=5) as connection, connection.transaction():
        await require_receptionist(
            connection,
            actor_id=actor_id,
            business_id=business_id,
            shop_id=shop_id,
            telegram_user_id=telegram_user_id,
        )
        if action == "r03":
            services = await _services(connection, business_id=business_id, shop_id=shop_id)
            state = SalesSession(
                step="walkin_services",
                service_ids=[UUID(str(row[0])) for row in services],
            )
            await _save_session(
                connection,
                bot_id=bot_id,
                business_id=business_id,
                shop_id=shop_id,
                telegram_user_id=telegram_user_id,
                state=state,
            )
            rows = tuple(
                ((f"{row[1]} - AED {Decimal(row[2]):.2f}", f"salesws{index}"),)
                for index, row in enumerate(services)
            )
            return ReceptionSalesResponse(
                text="Choose one or more services, then press Done.",
                keyboard=_keyboard((*rows, (("Done", "saleswdone"),))),
            )
        if action == "r04":
            bookings = await _completed_bookings(
                connection, business_id=business_id, shop_id=shop_id
            )
            state = SalesSession(
                step="checkout_booking",
                booking_ids=[UUID(str(row[0])) for row in bookings],
            )
            await _save_session(
                connection,
                bot_id=bot_id,
                business_id=business_id,
                shop_id=shop_id,
                telegram_user_id=telegram_user_id,
                state=state,
            )
            if not bookings:
                return ReceptionSalesResponse(text="No completed booking is awaiting checkout.")
            rows = tuple(
                (
                    (
                        f"#{row[1] or '-'} - {row[2]} - {row[4]}",
                        f"salesco{index}",
                    ),
                )
                for index, row in enumerate(bookings)
            )
            return ReceptionSalesResponse(
                text="Choose a completed booking:", keyboard=_keyboard(rows)
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
                return ReceptionSalesResponse(text=state.last_response_text)
            raise ReceptionSalesExpiredError
        if action.startswith("salesws") and action[7:].isdigit():
            index = int(action[7:])
            if state.step != "walkin_services" or index >= len(state.service_ids):
                raise ReceptionSalesExpiredError
            service_id = state.service_ids[index]
            if service_id in state.selected_service_ids:
                state.selected_service_ids.remove(service_id)
            else:
                state.selected_service_ids.append(service_id)
            services = await _services(connection, business_id=business_id, shop_id=shop_id)
            await _save_session(
                connection,
                bot_id=bot_id,
                business_id=business_id,
                shop_id=shop_id,
                telegram_user_id=telegram_user_id,
                state=state,
            )
            selected = set(state.selected_service_ids)
            rows = tuple(
                (
                    (
                        f"{'Selected: ' if UUID(str(row[0])) in selected else ''}"
                        f"{row[1]} - AED {Decimal(row[2]):.2f}",
                        f"salesws{item_index}",
                    ),
                )
                for item_index, row in enumerate(services)
            )
            return ReceptionSalesResponse(
                text="Choose one or more services, then press Done.",
                keyboard=_keyboard((*rows, (("Done", "saleswdone"),))),
            )
        if action == "saleswdone":
            if state.step != "walkin_services" or not state.selected_service_ids:
                raise ReceptionSalesExpiredError
            barbers = await _barbers(connection, business_id=business_id, shop_id=shop_id)
            state.step = "walkin_barber"
            state.barber_ids = [UUID(str(row[0])) for row in barbers]
            await _save_session(
                connection,
                bot_id=bot_id,
                business_id=business_id,
                shop_id=shop_id,
                telegram_user_id=telegram_user_id,
                state=state,
            )
            rows = tuple(((str(row[1]), f"saleswb{index}"),) for index, row in enumerate(barbers))
            return ReceptionSalesResponse(
                text="Choose a barber:",
                keyboard=_keyboard(((("Any barber", "saleswbany"),), *rows)),
            )
        if action == "saleswbany" or (action.startswith("saleswb") and action[7:].isdigit()):
            if state.step != "walkin_barber":
                raise ReceptionSalesExpiredError
            barber_id = None
            if action != "saleswbany":
                index = int(action[7:])
                if index >= len(state.barber_ids):
                    raise ReceptionSalesExpiredError
                barber_id = state.barber_ids[index]
            mutation = ("walkin", state, barber_id)
        elif action.startswith("salesco") and action[7:].isdigit():
            index = int(action[7:])
            if state.step != "checkout_booking" or index >= len(state.booking_ids):
                raise ReceptionSalesExpiredError
            state.booking_id = state.booking_ids[index]
            state.step = "checkout_summary"
            await _save_session(
                connection,
                bot_id=bot_id,
                business_id=business_id,
                shop_id=shop_id,
                telegram_user_id=telegram_user_id,
                state=state,
            )
            return _summary(
                await _checkout_snapshot(
                    connection, business_id=business_id, shop_id=shop_id, state=state
                )
            )
        elif action == "salesdiscounts":
            snapshot = await _checkout_snapshot(
                connection, business_id=business_id, shop_id=shop_id, state=state
            )
            rows = tuple(
                ((f"{line.name} - AED {line.discount:.2f}", f"salesds{index}"),)
                for index, line in enumerate(snapshot.lines)
            )
            return ReceptionSalesResponse(
                text="Choose a service to set its discount:", keyboard=_keyboard(rows)
            )
        elif action.startswith("salesds") and action[7:].isdigit():
            snapshot = await _checkout_snapshot(
                connection, business_id=business_id, shop_id=shop_id, state=state
            )
            index = int(action[7:])
            if index >= len(snapshot.lines):
                raise ReceptionSalesExpiredError
            state.booking_service_id = snapshot.lines[index].booking_service_id
            state.step = "discount_amount"
            await _save_session(
                connection,
                bot_id=bot_id,
                business_id=business_id,
                shop_id=shop_id,
                telegram_user_id=telegram_user_id,
                state=state,
            )
            return ReceptionSalesResponse(
                text=f"Send the discount in AED, from 0 to {snapshot.lines[index].unit_amount:.2f}."
            )
        elif action == "salestip":
            state.step = "tip_amount"
            await _save_session(
                connection,
                bot_id=bot_id,
                business_id=business_id,
                shop_id=shop_id,
                telegram_user_id=telegram_user_id,
                state=state,
            )
            return ReceptionSalesResponse(text="Send the tip in AED (0 is allowed).")
        elif action == "salespay":
            snapshot = await _checkout_snapshot(
                connection, business_id=business_id, shop_id=shop_id, state=state
            )
            return ReceptionSalesResponse(
                text=f"Choose payment method for AED {snapshot.grand_total:.2f}:",
                keyboard=_keyboard(
                    (
                        (("Cash", "salescash"), ("Card", "salescard")),
                        (("Split cash/card", "salessplit"),),
                    )
                ),
            )
        elif action in {"salescash", "salessplit"}:
            shifts = await _open_shifts(connection, business_id=business_id, shop_id=shop_id)
            if not shifts:
                return ReceptionSalesResponse(text="Open a cash shift before taking cash.")
            state.step = "cash_shift" if action == "salescash" else "split_cash_shift"
            state.cash_shift_ids = [UUID(str(row[0])) for row in shifts]
            await _save_session(
                connection,
                bot_id=bot_id,
                business_id=business_id,
                shop_id=shop_id,
                telegram_user_id=telegram_user_id,
                state=state,
            )
            rows = tuple(((str(row[1]), f"salessh{index}"),) for index, row in enumerate(shifts))
            return ReceptionSalesResponse(text="Choose the cash shift:", keyboard=_keyboard(rows))
        elif action.startswith("salessh") and action[7:].isdigit():
            index = int(action[7:])
            if state.step not in {"cash_shift", "split_cash_shift"} or index >= len(
                state.cash_shift_ids
            ):
                raise ReceptionSalesExpiredError
            state.cash_shift_id = state.cash_shift_ids[index]
            snapshot = await _checkout_snapshot(
                connection, business_id=business_id, shop_id=shop_id, state=state
            )
            if state.step == "cash_shift":
                mutation = ("cash", state, None)
            else:
                state.step = "split_cash_amount"
                await _save_session(
                    connection,
                    bot_id=bot_id,
                    business_id=business_id,
                    shop_id=shop_id,
                    telegram_user_id=telegram_user_id,
                    state=state,
                )
                return ReceptionSalesResponse(
                    text=(
                        f"Send the cash portion, greater than 0 and less than "
                        f"AED {snapshot.grand_total:.2f}."
                    )
                )
        elif action == "salescard":
            state.step = "card_reference"
            state.payment_mode = "card"
            await _save_session(
                connection,
                bot_id=bot_id,
                business_id=business_id,
                shop_id=shop_id,
                telegram_user_id=telegram_user_id,
                state=state,
            )
            return ReceptionSalesResponse(text="Send the card slip reference (never the PAN).")
        else:
            raise ReceptionSalesExpiredError

    assert mutation is not None
    kind, state, barber_id = mutation
    if kind == "walkin":
        result = await create_booking(
            pool,
            actor_id=actor_id,
            business_id=business_id,
            shop_id=shop_id,
            idempotency_key=f"telegram:{bot_id}:{request_id}",
            request_id=request_id,
            payload=BookingCreateRequest(
                booking_type="walk_in",
                barber_membership_id=barber_id,
                service_ids=state.selected_service_ids,
            ),
        )
        response = ReceptionSalesResponse(
            text=f"Walk-in created. Queue token {result.queue_number}.",
            keyboard=_keyboard(((("Open queue", "r01"),),)),
        )
        return await _complete_session(
            pool,
            bot_id=bot_id,
            business_id=business_id,
            shop_id=shop_id,
            telegram_user_id=telegram_user_id,
            request_id=request_id,
            response=response,
        )
    async with pool.connection(timeout=5) as connection:
        snapshot = await _checkout_snapshot(
            connection, business_id=business_id, shop_id=shop_id, state=state
        )
    assert state.cash_shift_id is not None
    return await _run_checkout(
        pool,
        bot_id=bot_id,
        business_id=business_id,
        shop_id=shop_id,
        actor_id=actor_id,
        telegram_user_id=telegram_user_id,
        request_id=request_id,
        state=state,
        payments=[CheckoutPayment(method="cash", amount=snapshot.grand_total)],
        cash_shift_id=state.cash_shift_id,
    )


async def handle_reception_sales_input(
    pool: Any,
    *,
    bot_id: UUID,
    business_id: UUID,
    shop_id: UUID,
    actor_id: UUID,
    telegram_user_id: int,
    text: str,
    request_id: str,
) -> ReceptionSalesResponse:
    operation: tuple[str, SalesSession, CheckoutSnapshot, str] | None = None
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
                return ReceptionSalesResponse(text=state.last_response_text)
            raise ReceptionSalesExpiredError
        snapshot = await _checkout_snapshot(
            connection, business_id=business_id, shop_id=shop_id, state=state
        )
        if state.step == "discount_amount":
            if state.booking_service_id is None:
                raise ReceptionSalesExpiredError
            amount = _money(text, positive=False)
            line = next(
                (
                    item
                    for item in snapshot.lines
                    if item.booking_service_id == state.booking_service_id
                ),
                None,
            )
            if line is None or amount > line.unit_amount:
                raise ReceptionSalesExpiredError
            state.discounts = [
                item
                for item in state.discounts
                if item.booking_service_id != state.booking_service_id
            ]
            if amount:
                state.discounts.append(
                    SalesDiscount(booking_service_id=state.booking_service_id, amount=amount)
                )
            state.step = "checkout_summary"
            state.booking_service_id = None
            await _save_session(
                connection,
                bot_id=bot_id,
                business_id=business_id,
                shop_id=shop_id,
                telegram_user_id=telegram_user_id,
                state=state,
            )
            return _summary(
                await _checkout_snapshot(
                    connection, business_id=business_id, shop_id=shop_id, state=state
                )
            )
        if state.step == "tip_amount":
            state.tip_amount = _money(text, positive=False)
            state.step = "checkout_summary"
            await _save_session(
                connection,
                bot_id=bot_id,
                business_id=business_id,
                shop_id=shop_id,
                telegram_user_id=telegram_user_id,
                state=state,
            )
            return _summary(
                await _checkout_snapshot(
                    connection, business_id=business_id, shop_id=shop_id, state=state
                )
            )
        if state.step == "split_cash_amount":
            cash_amount = _money(text, positive=True)
            if cash_amount >= snapshot.grand_total:
                raise ReceptionSalesExpiredError
            state.cash_amount = cash_amount
            state.payment_mode = "split"
            state.step = "card_reference"
            await _save_session(
                connection,
                bot_id=bot_id,
                business_id=business_id,
                shop_id=shop_id,
                telegram_user_id=telegram_user_id,
                state=state,
            )
            return ReceptionSalesResponse(text="Send the card slip reference (never the PAN).")
        if state.step == "card_reference":
            reference = text.strip()
            if (
                reference != text
                or CARD_REFERENCE.fullmatch(reference) is None
                or PAN_LIKE.search(reference) is not None
            ):
                raise ReceptionSalesExpiredError
            operation = ("checkout", state, snapshot, reference)
        else:
            raise ReceptionSalesExpiredError

    assert operation is not None
    _, state, snapshot, reference = operation
    payments = [
        CheckoutPayment(method="card", amount=snapshot.grand_total, card_slip_reference=reference)
    ]
    cash_shift_id = None
    if state.payment_mode == "split":
        if state.cash_amount is None or state.cash_shift_id is None:
            raise ReceptionSalesExpiredError
        payments = [
            CheckoutPayment(method="cash", amount=state.cash_amount),
            CheckoutPayment(
                method="card",
                amount=snapshot.grand_total - state.cash_amount,
                card_slip_reference=reference,
            ),
        ]
        cash_shift_id = state.cash_shift_id
    return await _run_checkout(
        pool,
        bot_id=bot_id,
        business_id=business_id,
        shop_id=shop_id,
        actor_id=actor_id,
        telegram_user_id=telegram_user_id,
        request_id=request_id,
        state=state,
        payments=payments,
        cash_shift_id=cash_shift_id,
    )


__all__ = [
    "ReceptionSalesExpiredError",
    "ReceptionSalesResponse",
    "handle_reception_sales_callback",
    "handle_reception_sales_input",
]
