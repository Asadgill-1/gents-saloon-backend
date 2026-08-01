from typing import Any, Literal
from uuid import UUID

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field

from app.core.telegram import callback_data
from app.services.booking_service import BookingTransitionRequest, transition_booking


class ReceptionMenuExpiredError(Exception):
    """The receptionist callback is stale or outside its authorized shop."""


class ReceptionFlowResponse(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    text: str
    keyboard: InlineKeyboardMarkup | None = None


class ReceptionSession(BaseModel):
    model_config = ConfigDict(extra="forbid")

    view: Literal["queue", "appointments"]
    booking_ids: list[UUID] = Field(max_length=20)
    selected_booking_id: UUID | None = None


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


async def _require_receptionist(
    connection: Any,
    *,
    actor_id: UUID,
    business_id: UUID,
    shop_id: UUID,
    telegram_user_id: int,
) -> None:
    cursor = await connection.execute(
        """
        select 1
        from public.shop_memberships sm
        join public.user_profiles up on up.auth_user_id = sm.auth_user_id and up.active
        join public.shops sh
          on sh.id = sm.shop_id and sh.business_id = sm.business_id and sh.status = 'active'
        where sm.auth_user_id = %s and sm.business_id = %s and sm.shop_id = %s
          and sm.telegram_user_id = %s and sm.active
          and sm.role in ('manager', 'receptionist')
          and not exists (
            select 1 from public.telegram_user_blocks tub
            where tub.telegram_user_id = sm.telegram_user_id
              and (tub.expires_at is null or tub.expires_at > now())
          )
        """,
        (actor_id, business_id, shop_id, telegram_user_id),
    )
    if await cursor.fetchone() is None:
        raise ReceptionMenuExpiredError


async def _save_session(
    connection: Any,
    *,
    bot_id: UUID,
    business_id: UUID,
    shop_id: UUID,
    telegram_user_id: int,
    state: ReceptionSession,
) -> None:
    await connection.execute(
        """
        insert into public.telegram_sessions (
          bot_id, business_id, shop_id, telegram_user_id, bot_role, state, payload
        ) values (%s, %s, %s, %s, 'receptionist', 'reception_bookings', %s)
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
) -> ReceptionSession:
    cursor = await connection.execute(
        """
        select payload
        from public.telegram_sessions
        where bot_id = %s and telegram_user_id = %s
          and business_id = %s and shop_id = %s and bot_role = 'receptionist'
          and state = 'reception_bookings'
          and updated_at >= now() - interval '15 minutes'
        for update
        """,
        (bot_id, telegram_user_id, business_id, shop_id),
    )
    row = await cursor.fetchone()
    if row is None:
        raise ReceptionMenuExpiredError
    try:
        return ReceptionSession.model_validate(row[0])
    except Exception as exc:
        raise ReceptionMenuExpiredError from exc


async def _booking_rows(
    connection: Any,
    *,
    business_id: UUID,
    shop_id: UUID,
    view: Literal["queue", "appointments"],
) -> list[tuple[Any, ...]]:
    cursor = await connection.execute(
        """
        select b.id, b.status::text, b.queue_number, b.scheduled_start,
               coalesce(c.display_name, 'Walk-in'), sm.display_name,
               coalesce(string_agg(bs.service_name, ', ' order by bs.sort_order), '')
        from public.bookings b
        left join public.customers c
          on c.id = b.customer_id and c.business_id = b.business_id and c.shop_id = b.shop_id
        join public.shop_memberships sm
          on sm.id = b.barber_membership_id and sm.business_id = b.business_id
         and sm.shop_id = b.shop_id
        left join public.booking_services bs
          on bs.booking_id = b.id and bs.business_id = b.business_id and bs.shop_id = b.shop_id
        where b.business_id = %s and b.shop_id = %s
          and b.status in ('held', 'requested', 'confirmed', 'in_service')
          and ((%s = 'appointments' and b.booking_type = 'appointment')
            or (%s = 'queue' and b.booking_type <> 'appointment'))
        group by b.id, c.display_name, sm.display_name
        order by coalesce(b.scheduled_start, b.created_at), b.id
        limit 20
        """,
        (business_id, shop_id, view, view),
    )
    return list(await cursor.fetchall())


def _card(row: tuple[Any, ...]) -> str:
    lines = [
        f"Status: {row[1]}",
        f"Customer: {row[4]}",
        f"Services: {row[6]}",
        f"Barber: {row[5]}",
    ]
    if row[2] is not None:
        lines.append(f"Queue token: {row[2]}")
    if row[3] is not None:
        lines.append(f"Scheduled: {row[3].isoformat()}")
    return "\n".join(lines)


def _action_keyboard(status: str) -> InlineKeyboardMarkup:
    actions: dict[str, tuple[tuple[str, str], ...]] = {
        "held": (("Confirm", "recconfirm"), ("Cancel", "reccancel")),
        "requested": (("Confirm", "recconfirm"), ("Reject", "recreject")),
        "confirmed": (
            ("Start", "recstart"),
            ("No-show", "recnoshow"),
            ("Cancel", "reccancel"),
        ),
        "in_service": (("Continue to checkout", "r04"),),
    }
    return _keyboard((actions.get(status, ()), (("Refresh list", "recrefresh"),)))


async def _render_list(
    connection: Any,
    *,
    bot_id: UUID,
    business_id: UUID,
    shop_id: UUID,
    telegram_user_id: int,
    view: Literal["queue", "appointments"],
) -> ReceptionFlowResponse:
    rows = await _booking_rows(connection, business_id=business_id, shop_id=shop_id, view=view)
    state = ReceptionSession(view=view, booking_ids=[UUID(str(row[0])) for row in rows])
    await _save_session(
        connection,
        bot_id=bot_id,
        business_id=business_id,
        shop_id=shop_id,
        telegram_user_id=telegram_user_id,
        state=state,
    )
    if not rows:
        return ReceptionFlowResponse(text=f"No active {view}.")
    buttons = tuple(
        ((f"{row[1]} · {row[4]} · {row[5]}", f"rec{index}"),) for index, row in enumerate(rows)
    )
    return ReceptionFlowResponse(
        text=f"Active {view}: {len(rows)}. Choose a booking:",
        keyboard=_keyboard(buttons),
    )


async def handle_reception_callback(
    pool: Any,
    *,
    bot_id: UUID,
    business_id: UUID,
    shop_id: UUID,
    actor_id: UUID,
    telegram_user_id: int,
    callback: str,
    request_id: str,
) -> ReceptionFlowResponse:
    if not callback.startswith("v1."):
        raise ReceptionMenuExpiredError
    action = callback[3:]
    transition: tuple[UUID, str, str] | None = None
    async with pool.connection(timeout=5) as connection, connection.transaction():
        await _require_receptionist(
            connection,
            actor_id=actor_id,
            business_id=business_id,
            shop_id=shop_id,
            telegram_user_id=telegram_user_id,
        )
        if action in {"r01", "r02"}:
            return await _render_list(
                connection,
                bot_id=bot_id,
                business_id=business_id,
                shop_id=shop_id,
                telegram_user_id=telegram_user_id,
                view="queue" if action == "r01" else "appointments",
            )
        state = await _load_session(
            connection,
            bot_id=bot_id,
            business_id=business_id,
            shop_id=shop_id,
            telegram_user_id=telegram_user_id,
        )
        if action == "recrefresh":
            return await _render_list(
                connection,
                bot_id=bot_id,
                business_id=business_id,
                shop_id=shop_id,
                telegram_user_id=telegram_user_id,
                view=state.view,
            )
        if action.startswith("rec") and action[3:].isdigit():
            index = int(action[3:])
            if index >= len(state.booking_ids):
                raise ReceptionMenuExpiredError
            booking_id = state.booking_ids[index]
            rows = await _booking_rows(
                connection, business_id=business_id, shop_id=shop_id, view=state.view
            )
            selected = next((row for row in rows if UUID(str(row[0])) == booking_id), None)
            if selected is None:
                raise ReceptionMenuExpiredError
            state.selected_booking_id = booking_id
            await _save_session(
                connection,
                bot_id=bot_id,
                business_id=business_id,
                shop_id=shop_id,
                telegram_user_id=telegram_user_id,
                state=state,
            )
            return ReceptionFlowResponse(
                text=_card(selected), keyboard=_action_keyboard(str(selected[1]))
            )
        targets: dict[
            str,
            tuple[
                Literal["confirmed", "in_service", "completed", "cancelled", "no_show"],
                str,
            ],
        ] = {
            "recconfirm": ("confirmed", "reception confirmed"),
            "recreject": ("cancelled", "reception rejected request"),
            "recstart": ("in_service", "service started"),
            "recnoshow": ("no_show", "customer no-show"),
            "reccancel": ("cancelled", "reception cancelled"),
        }
        if action not in targets or state.selected_booking_id is None:
            raise ReceptionMenuExpiredError
        target, reason = targets[action]
        transition = (state.selected_booking_id, target, reason)

    assert transition is not None
    booking_id, target, reason = transition
    result = await transition_booking(
        pool,
        actor_id=actor_id,
        business_id=business_id,
        shop_id=shop_id,
        booking_id=booking_id,
        target_status=target,
        idempotency_key=f"telegram:{bot_id}:{request_id}",
        request_id=request_id,
        payload=BookingTransitionRequest(reason=reason),
    )
    return ReceptionFlowResponse(
        text=f"Booking {result.status}.",
        keyboard=_keyboard(((("Refresh list", "recrefresh"),),)),
    )


__all__ = [
    "ReceptionFlowResponse",
    "ReceptionMenuExpiredError",
    "handle_reception_callback",
]
