import os
from uuid import UUID

import pytest

from app.core.config import Settings
from app.core.database import create_database_pool
from app.services.barber_bot_flow import handle_barber_callback

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_DATABASE_INTEGRATION") != "1",
    reason="requires reconstructed Phase 3 PostgreSQL test database",
)

BOT_ID = UUID("60000000-0000-0000-0000-000000000005")
BUSINESS_ID = UUID("10000000-0000-0000-0000-000000000001")
SHOP_ID = UUID("20000000-0000-0000-0000-000000000001")
BARBER_ACTOR_ID = UUID("00000000-0000-0000-0000-000000000005")
CUSTOMER_ID = UUID("61000000-0000-0000-0000-000000000001")
TELEGRAM_USER_ID = 999201
BOOKING_ID = UUID("62000000-0000-0000-0000-000000000001")
QUEUE_NUMBER = 999901


async def test_barber_own_views_and_once_only_reminder() -> None:
    pool = create_database_pool(Settings(_env_file=None))
    await pool.open()
    try:
        async with pool.connection(timeout=5) as connection, connection.transaction():
            cursor = await connection.execute(
                """
                update public.shop_memberships
                set telegram_user_id = %s, updated_at = now()
                where auth_user_id = %s and business_id = %s and shop_id = %s
                  and role = 'barber'
                returning id
                """,
                (TELEGRAM_USER_ID, BARBER_ACTOR_ID, BUSINESS_ID, SHOP_ID),
            )
            row = await cursor.fetchone()
            assert row is not None
            barber_membership_id = UUID(str(row[0]))
            cursor = await connection.execute(
                """
                select id from public.services
                where business_id = %s and shop_id = %s and active
                order by sort_order, id limit 1
                """,
                (BUSINESS_ID, SHOP_ID),
            )
            row = await cursor.fetchone()
            assert row is not None
            service_id = UUID(str(row[0]))
            await connection.execute(
                """
                insert into public.bookings (
                  id, business_id, shop_id, customer_id, barber_membership_id,
                  booking_type, status, source, queue_business_date, queue_number,
                  confirmed_at
                )
                select %s, %s, %s, %s, %s, 'queue', 'confirmed', 'telegram',
                       (now() at time zone sh.timezone)::date, %s, now()
                from public.shops sh
                where sh.id = %s and sh.business_id = %s
                on conflict (id) do nothing
                """,
                (
                    BOOKING_ID,
                    BUSINESS_ID,
                    SHOP_ID,
                    CUSTOMER_ID,
                    barber_membership_id,
                    QUEUE_NUMBER,
                    SHOP_ID,
                    BUSINESS_ID,
                ),
            )
            await connection.execute(
                """
                insert into public.booking_services (
                  business_id, shop_id, booking_id, service_id, service_name,
                  price_gross, vat_rate, duration_minutes, sort_order
                )
                select %s, %s, %s, s.id, s.name, s.price_gross,
                       s.vat_rate, s.duration_minutes, 0
                from public.services s
                where s.id = %s and s.business_id = %s and s.shop_id = %s
                  and not exists (
                    select 1 from public.booking_services bs where bs.booking_id = %s
                  )
                """,
                (
                    BUSINESS_ID,
                    SHOP_ID,
                    BOOKING_ID,
                    service_id,
                    BUSINESS_ID,
                    SHOP_ID,
                    BOOKING_ID,
                ),
            )
            await connection.execute(
                """
                insert into public.bots (
                  id, business_id, shop_id, role, token_ciphertext,
                  bot_username, webhook_secret_hash
                ) values (
                  %s, %s, %s, 'barber_crew',
                  'v1.AAAAAAAAAAAAAAAA.RRRRRRRRRRRRRRRRRRRRRR',
                  'test_barber_bot', 'v1:' || repeat('e', 64)
                )
                on conflict (id) do nothing
                """,
                (BOT_ID, BUSINESS_ID, SHOP_ID),
            )

        queue = await handle_barber_callback(
            pool,
            bot_id=BOT_ID,
            business_id=BUSINESS_ID,
            shop_id=SHOP_ID,
            actor_id=BARBER_ACTOR_ID,
            telegram_user_id=TELEGRAM_USER_ID,
            callback="v1.b01",
            request_id="telegram:test:barber-queue",
        )
        assert f"#{QUEUE_NUMBER}" in queue.text
        assert queue.keyboard is not None
        reminder_callback = next(
            button.callback_data
            for row in queue.keyboard.inline_keyboard
            for button in row
            if button.text == f"Remind #{QUEUE_NUMBER}"
        )
        assert reminder_callback is not None
        for _attempt in range(2):
            reminder = await handle_barber_callback(
                pool,
                bot_id=BOT_ID,
                business_id=BUSINESS_ID,
                shop_id=SHOP_ID,
                actor_id=BARBER_ACTOR_ID,
                telegram_user_id=TELEGRAM_USER_ID,
                callback=reminder_callback,
                request_id="telegram:test:barber-reminder",
            )
            assert reminder.text == "Five-minute reminder queued once."

        earnings = await handle_barber_callback(
            pool,
            bot_id=BOT_ID,
            business_id=BUSINESS_ID,
            shop_id=SHOP_ID,
            actor_id=BARBER_ACTOR_ID,
            telegram_user_id=TELEGRAM_USER_ID,
            callback="v1.b02",
            request_id="telegram:test:barber-earnings",
        )
        payouts = await handle_barber_callback(
            pool,
            bot_id=BOT_ID,
            business_id=BUSINESS_ID,
            shop_id=SHOP_ID,
            actor_id=BARBER_ACTOR_ID,
            telegram_user_id=TELEGRAM_USER_ID,
            callback="v1.b03",
            request_id="telegram:test:barber-payouts",
        )
        advances = await handle_barber_callback(
            pool,
            bot_id=BOT_ID,
            business_id=BUSINESS_ID,
            shop_id=SHOP_ID,
            actor_id=BARBER_ACTOR_ID,
            telegram_user_id=TELEGRAM_USER_ID,
            callback="v1.b04",
            request_id="telegram:test:barber-advances",
        )
        assert "My earnings today" in earnings.text
        assert "payout" in payouts.text.lower()
        assert "advance" in advances.text.lower()

        async with pool.connection(timeout=5) as connection:
            cursor = await connection.execute(
                """
                select
                  (select count(*) from public.outbox_events
                   where dedupe_key = %s),
                  (select count(*) from public.audit_log
                   where action = 'telegram.arrival_reminder_sent'
                     and entity_id = %s)
                """,
                (f"telegram:arrival-reminder:{BOOKING_ID}", BOOKING_ID),
            )
            assert await cursor.fetchone() == (1, 1)
    finally:
        await pool.close()
