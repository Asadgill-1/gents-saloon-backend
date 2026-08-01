from typing import Any
from uuid import UUID

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field

from app.core.telegram import callback_data


class BarberMenuExpiredError(Exception):
    """The barber callback is stale or outside the authorized membership."""


class BarberFlowResponse(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    text: str
    keyboard: InlineKeyboardMarkup | None = None


class BarberSession(BaseModel):
    model_config = ConfigDict(extra="forbid")

    booking_ids: list[UUID] = Field(default_factory=list, max_length=20)


REMINDERS = {
    "en": "Your barber is ready. Please arrive within five minutes.",
    "ar": "الحلاق جاهز. يرجى الوصول خلال خمس دقائق.",
    "hi": "आपका बार्बर तैयार है। कृपया पाँच मिनट में पहुँचें।",
    "ur": "آپ کا حجام تیار ہے۔ براہ کرم پانچ منٹ میں پہنچیں۔",
}


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


async def _require_barber(
    connection: Any,
    *,
    actor_id: UUID,
    business_id: UUID,
    shop_id: UUID,
    telegram_user_id: int,
) -> UUID:
    cursor = await connection.execute(
        """
        select sm.id
        from public.shop_memberships sm
        join public.user_profiles up on up.auth_user_id = sm.auth_user_id and up.active
        join public.shops sh
          on sh.id = sm.shop_id and sh.business_id = sm.business_id and sh.status = 'active'
        where sm.auth_user_id = %s and sm.business_id = %s and sm.shop_id = %s
          and sm.telegram_user_id = %s and sm.active and sm.role = 'barber'
          and not exists (
            select 1 from public.telegram_user_blocks tub
            where tub.telegram_user_id = sm.telegram_user_id
              and (tub.expires_at is null or tub.expires_at > now())
          )
        """,
        (actor_id, business_id, shop_id, telegram_user_id),
    )
    row = await cursor.fetchone()
    if row is None:
        raise BarberMenuExpiredError
    return UUID(str(row[0]))


async def _save_session(
    connection: Any,
    *,
    bot_id: UUID,
    business_id: UUID,
    shop_id: UUID,
    telegram_user_id: int,
    booking_ids: list[UUID],
) -> None:
    state = BarberSession(booking_ids=booking_ids)
    await connection.execute(
        """
        insert into public.telegram_sessions (
          bot_id, business_id, shop_id, telegram_user_id, bot_role, state, payload
        ) values (%s, %s, %s, %s, 'barber_crew', 'barber_queue', %s)
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
) -> BarberSession:
    cursor = await connection.execute(
        """
        select payload
        from public.telegram_sessions
        where bot_id = %s and telegram_user_id = %s
          and business_id = %s and shop_id = %s and bot_role = 'barber_crew'
          and state = 'barber_queue' and updated_at >= now() - interval '15 minutes'
        for update
        """,
        (bot_id, telegram_user_id, business_id, shop_id),
    )
    row = await cursor.fetchone()
    if row is None:
        raise BarberMenuExpiredError
    try:
        return BarberSession.model_validate(row[0])
    except Exception as exc:
        raise BarberMenuExpiredError from exc


async def _queue(
    connection: Any,
    *,
    business_id: UUID,
    shop_id: UUID,
    barber_membership_id: UUID,
) -> list[tuple[Any, ...]]:
    cursor = await connection.execute(
        """
        select b.id, b.status::text, b.queue_number,
               coalesce(c.display_name, 'Walk-in'),
               coalesce(string_agg(bs.service_name, ', ' order by bs.sort_order), ''),
               c.telegram_user_id is not null
        from public.bookings b
        join public.shops sh on sh.id = b.shop_id and sh.business_id = b.business_id
        left join public.customers c
          on c.id = b.customer_id and c.business_id = b.business_id and c.shop_id = b.shop_id
        left join public.booking_services bs
          on bs.booking_id = b.id and bs.business_id = b.business_id and bs.shop_id = b.shop_id
        where b.business_id = %s and b.shop_id = %s
          and b.barber_membership_id = %s
          and b.status in ('confirmed', 'in_service')
          and (
            b.queue_business_date = (now() at time zone sh.timezone)::date
            or (b.scheduled_start at time zone sh.timezone)::date
               = (now() at time zone sh.timezone)::date
          )
        group by b.id, c.display_name, c.telegram_user_id
        order by (b.status = 'in_service') desc,
                 coalesce(b.queue_number, 2147483647), b.scheduled_start, b.id
        limit 20
        """,
        (business_id, shop_id, barber_membership_id),
    )
    return list(await cursor.fetchall())


async def _queue_response(
    connection: Any,
    *,
    bot_id: UUID,
    business_id: UUID,
    shop_id: UUID,
    telegram_user_id: int,
    barber_membership_id: UUID,
) -> BarberFlowResponse:
    rows = await _queue(
        connection,
        business_id=business_id,
        shop_id=shop_id,
        barber_membership_id=barber_membership_id,
    )
    await _save_session(
        connection,
        bot_id=bot_id,
        business_id=business_id,
        shop_id=shop_id,
        telegram_user_id=telegram_user_id,
        booking_ids=[UUID(str(row[0])) for row in rows],
    )
    if not rows:
        return BarberFlowResponse(text="No active booking is assigned to you today.")
    cards = [f"#{row[2] or '-'} - {row[1]} - {row[3]} - {row[4]}" for row in rows]
    reminder_rows = tuple(
        ((f"Remind #{row[2] or '-'}", f"barrem{index}"),)
        for index, row in enumerate(rows)
        if row[1] == "confirmed" and bool(row[5])
    )
    keyboard = _keyboard(reminder_rows) if reminder_rows else None
    return BarberFlowResponse(text="My queue today\n" + "\n".join(cards), keyboard=keyboard)


async def _earnings(
    connection: Any,
    *,
    business_id: UUID,
    shop_id: UUID,
    barber_membership_id: UUID,
) -> BarberFlowResponse:
    cursor = await connection.execute(
        """
        with own_transactions as (
          select t.id, t.service_gross_total, t.tip_total
          from public.transactions t
          join public.shops sh on sh.id = t.shop_id and sh.business_id = t.business_id
          where t.business_id = %s and t.shop_id = %s
            and t.barber_membership_id = %s
            and (t.created_at at time zone sh.timezone)::date
                = (now() at time zone sh.timezone)::date
        ), commissions as (
          select tic.transaction_id, sum(tic.barber_commission) as amount
          from public.transaction_item_commissions tic
          join own_transactions ot on ot.id = tic.transaction_id
          where tic.barber_membership_id = %s
          group by tic.transaction_id
        )
        select count(ot.id), coalesce(sum(ot.service_gross_total), 0),
               coalesce(sum(c.amount), 0), coalesce(sum(ot.tip_total), 0)
        from own_transactions ot
        left join commissions c on c.transaction_id = ot.id
        """,
        (business_id, shop_id, barber_membership_id, barber_membership_id),
    )
    row = await cursor.fetchone()
    assert row is not None
    return BarberFlowResponse(
        text=(
            "My earnings today\n"
            f"Closed transactions: {row[0]}\n"
            f"Service revenue: AED {row[1]:.2f}\n"
            f"Commission earned: AED {row[2]:.2f}\n"
            f"Tips earned: AED {row[3]:.2f}"
        )
    )


async def _payouts(
    connection: Any,
    *,
    business_id: UUID,
    shop_id: UUID,
    barber_membership_id: UUID,
) -> BarberFlowResponse:
    cursor = await connection.execute(
        """
        select pr.period_start, pr.period_end, pr.status::text,
               pi.gross_payable, pi.advance_deduction, pi.net_paid
        from public.payout_items pi
        join public.payout_runs pr
          on pr.id = pi.payout_run_id and pr.business_id = pi.business_id
         and pr.shop_id = pi.shop_id
        where pi.business_id = %s and pi.shop_id = %s
          and pi.barber_membership_id = %s
        order by pr.period_end desc, pi.id desc
        limit 10
        """,
        (business_id, shop_id, barber_membership_id),
    )
    rows = await cursor.fetchall()
    if not rows:
        return BarberFlowResponse(text="No payout record is available.")
    return BarberFlowResponse(
        text="My payouts\n"
        + "\n".join(
            f"{row[0].date()} to {row[1].date()} - {row[2]} - "
            f"gross AED {row[3]:.2f}, advance AED {row[4]:.2f}, net AED {row[5]:.2f}"
            for row in rows
        )
    )


async def _advances(
    connection: Any,
    *,
    business_id: UUID,
    shop_id: UUID,
    barber_membership_id: UUID,
) -> BarberFlowResponse:
    cursor = await connection.execute(
        """
        select a.given_at, a.status::text, a.original_amount, a.outstanding_amount,
               coalesce(sum(aa.amount), 0)
        from public.advances a
        left join public.advance_applications aa on aa.advance_id = a.id
        where a.business_id = %s and a.shop_id = %s
          and a.barber_membership_id = %s
        group by a.id
        order by a.given_at desc, a.id desc
        limit 10
        """,
        (business_id, shop_id, barber_membership_id),
    )
    rows = await cursor.fetchall()
    if not rows:
        return BarberFlowResponse(text="No advance record is available.")
    return BarberFlowResponse(
        text="My advances\n"
        + "\n".join(
            f"{row[0].date()} - {row[1]} - given AED {row[2]:.2f}, "
            f"deducted AED {row[4]:.2f}, outstanding AED {row[3]:.2f}"
            for row in rows
        )
    )


async def handle_barber_callback(
    pool: Any,
    *,
    bot_id: UUID,
    business_id: UUID,
    shop_id: UUID,
    actor_id: UUID,
    telegram_user_id: int,
    callback: str,
    request_id: str,
) -> BarberFlowResponse:
    if not callback.startswith("v1."):
        raise BarberMenuExpiredError
    action = callback[3:]
    async with pool.connection(timeout=5) as connection, connection.transaction():
        barber_membership_id = await _require_barber(
            connection,
            actor_id=actor_id,
            business_id=business_id,
            shop_id=shop_id,
            telegram_user_id=telegram_user_id,
        )
        if action == "b01":
            return await _queue_response(
                connection,
                bot_id=bot_id,
                business_id=business_id,
                shop_id=shop_id,
                telegram_user_id=telegram_user_id,
                barber_membership_id=barber_membership_id,
            )
        if action == "b02":
            return await _earnings(
                connection,
                business_id=business_id,
                shop_id=shop_id,
                barber_membership_id=barber_membership_id,
            )
        if action == "b03":
            return await _payouts(
                connection,
                business_id=business_id,
                shop_id=shop_id,
                barber_membership_id=barber_membership_id,
            )
        if action == "b04":
            return await _advances(
                connection,
                business_id=business_id,
                shop_id=shop_id,
                barber_membership_id=barber_membership_id,
            )
        if action.startswith("barrem") and action[6:].isdigit():
            state = await _load_session(
                connection,
                bot_id=bot_id,
                business_id=business_id,
                shop_id=shop_id,
                telegram_user_id=telegram_user_id,
            )
            index = int(action[6:])
            if index >= len(state.booking_ids):
                raise BarberMenuExpiredError
            booking_id = state.booking_ids[index]
            cursor = await connection.execute(
                """
                select customer_bot.id, c.telegram_user_id, c.language::text
                from public.bookings b
                join public.customers c
                  on c.id = b.customer_id and c.business_id = b.business_id
                 and c.shop_id = b.shop_id and c.telegram_user_id is not null
                 and c.blocked_at is null and c.anonymized_at is null
                join public.bots customer_bot
                  on customer_bot.business_id = b.business_id
                 and customer_bot.shop_id = b.shop_id
                 and customer_bot.role = 'customer' and customer_bot.active
                where b.id = %s and b.business_id = %s and b.shop_id = %s
                  and b.barber_membership_id = %s and b.status = 'confirmed'
                """,
                (booking_id, business_id, shop_id, barber_membership_id),
            )
            target = await cursor.fetchone()
            if target is None:
                raise BarberMenuExpiredError
            dedupe = f"telegram:arrival-reminder:{booking_id}"
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
                            "bot_id": str(target[0]),
                            "chat_id": int(target[1]),
                            "text": REMINDERS.get(str(target[2]), REMINDERS["en"]),
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
                              'telegram.arrival_reminder_sent', 'booking', %s, %s, %s)
                    """,
                    (
                        business_id,
                        shop_id,
                        actor_id,
                        booking_id,
                        request_id,
                        Jsonb({"booking_id": str(booking_id)}),
                    ),
                )
            return BarberFlowResponse(text="Five-minute reminder queued once.")
    raise BarberMenuExpiredError


__all__ = ["BarberFlowResponse", "BarberMenuExpiredError", "handle_barber_callback"]
