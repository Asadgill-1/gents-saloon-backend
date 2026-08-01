import os
from datetime import UTC, datetime
from uuid import UUID

import pytest

from app.core.config import Settings
from app.core.database import create_database_pool
from app.services.customer_bot_flow import CustomerMenuExpiredError, handle_customer_callback

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_DATABASE_INTEGRATION") != "1",
    reason="requires reconstructed Phase 3 PostgreSQL test database",
)

BOT_ID = UUID("60000000-0000-0000-0000-000000000001")
BUSINESS_ID = UUID("10000000-0000-0000-0000-000000000001")
SHOP_ID = UUID("20000000-0000-0000-0000-000000000001")
CUSTOMER_ID = UUID("61000000-0000-0000-0000-000000000001")
TELEGRAM_USER_ID = 999001


async def test_customer_queue_wizard_uses_opaque_state_and_transactional_booking() -> None:
    pool = create_database_pool(Settings(_env_file=None))
    await pool.open()
    try:
        today = datetime.now(UTC).date()
        async with pool.connection(timeout=5) as connection, connection.transaction():
            cursor = await connection.execute(
                """
                select id from public.shop_memberships
                where business_id = %s and shop_id = %s and role = 'barber' and active
                order by id limit 1
                """,
                (BUSINESS_ID, SHOP_ID),
            )
            barber = await cursor.fetchone()
            assert barber is not None
            barber_id = barber[0]
            await connection.execute(
                """
                insert into public.shop_business_hours (
                  business_id, shop_id, iso_weekday, open_time, close_time, effective_from
                )
                select %s, %s, %s, '00:01', '23:59', %s
                where not exists (
                  select 1 from public.shop_business_hours
                  where business_id = %s and shop_id = %s and iso_weekday = %s
                    and effective_from <= %s
                    and (effective_until is null or effective_until > %s) and active
                )
                """,
                (
                    BUSINESS_ID,
                    SHOP_ID,
                    today.isoweekday(),
                    today,
                    BUSINESS_ID,
                    SHOP_ID,
                    today.isoweekday(),
                    today,
                    today,
                ),
            )
            await connection.execute(
                """
                insert into public.staff_schedules (
                  business_id, shop_id, barber_membership_id, iso_weekday,
                  start_time, end_time, effective_from
                )
                select %s, %s, %s, %s, '00:01', '23:59', %s
                where not exists (
                  select 1 from public.staff_schedules
                  where business_id = %s and shop_id = %s and barber_membership_id = %s
                    and iso_weekday = %s and effective_from <= %s
                    and (effective_until is null or effective_until > %s) and active
                )
                """,
                (
                    BUSINESS_ID,
                    SHOP_ID,
                    barber_id,
                    today.isoweekday(),
                    today,
                    BUSINESS_ID,
                    SHOP_ID,
                    barber_id,
                    today.isoweekday(),
                    today,
                    today,
                ),
            )
        started = await handle_customer_callback(
            pool,
            bot_id=BOT_ID,
            business_id=BUSINESS_ID,
            shop_id=SHOP_ID,
            customer_id=CUSTOMER_ID,
            telegram_user_id=TELEGRAM_USER_ID,
            callback="v1.c01",
            request_id="telegram:test:queue-start",
        )
        assert "service" in started.text.lower()
        assert started.keyboard is not None
        callback_values = {
            button.callback_data for row in started.keyboard.inline_keyboard for button in row
        }
        assert "v1.svc0" in callback_values
        assert all(str(CUSTOMER_ID) not in (value or "") for value in callback_values)

        selected = await handle_customer_callback(
            pool,
            bot_id=BOT_ID,
            business_id=BUSINESS_ID,
            shop_id=SHOP_ID,
            customer_id=CUSTOMER_ID,
            telegram_user_id=TELEGRAM_USER_ID,
            callback="v1.svc0",
            request_id="telegram:test:queue-service",
        )
        assert "✓" in str(selected.keyboard)

        barber_menu = await handle_customer_callback(
            pool,
            bot_id=BOT_ID,
            business_id=BUSINESS_ID,
            shop_id=SHOP_ID,
            customer_id=CUSTOMER_ID,
            telegram_user_id=TELEGRAM_USER_ID,
            callback="v1.svcdone",
            request_id="telegram:test:queue-services-done",
        )
        assert "barber" in barber_menu.text.lower()

        confirmation = await handle_customer_callback(
            pool,
            bot_id=BOT_ID,
            business_id=BUSINESS_ID,
            shop_id=SHOP_ID,
            customer_id=CUSTOMER_ID,
            telegram_user_id=TELEGRAM_USER_ID,
            callback="v1.barany",
            request_id="telegram:test:queue-barber",
        )
        assert "AED 120.00" in confirmation.text

        created = await handle_customer_callback(
            pool,
            bot_id=BOT_ID,
            business_id=BUSINESS_ID,
            shop_id=SHOP_ID,
            customer_id=CUSTOMER_ID,
            telegram_user_id=TELEGRAM_USER_ID,
            callback="v1.confirm",
            request_id="telegram:test:queue-confirm",
        )
        assert "reception" in created.text.lower()

        async with pool.connection(timeout=5) as connection:
            cursor = await connection.execute(
                """
                select b.status::text, b.source::text, b.customer_id,
                       a.actor_type::text, a.actor_id,
                       exists (
                         select 1 from public.telegram_sessions ts
                         where ts.bot_id = %s and ts.telegram_user_id = %s
                       )
                from public.bookings b
                join public.audit_log a
                  on a.entity_id = b.id and a.action = 'booking.created'
                where b.business_id = %s and b.shop_id = %s and b.customer_id = %s
                order by b.created_at desc, b.id desc
                limit 1
                """,
                (BOT_ID, TELEGRAM_USER_ID, BUSINESS_ID, SHOP_ID, CUSTOMER_ID),
            )
            row = await cursor.fetchone()
            assert row == (
                "requested",
                "telegram",
                CUSTOMER_ID,
                "telegram_user",
                str(TELEGRAM_USER_ID),
                False,
            )

        await handle_customer_callback(
            pool,
            bot_id=BOT_ID,
            business_id=BUSINESS_ID,
            shop_id=SHOP_ID,
            customer_id=CUSTOMER_ID,
            telegram_user_id=TELEGRAM_USER_ID,
            callback="v1.c02",
            request_id="telegram:test:appointment-start",
        )
        await handle_customer_callback(
            pool,
            bot_id=BOT_ID,
            business_id=BUSINESS_ID,
            shop_id=SHOP_ID,
            customer_id=CUSTOMER_ID,
            telegram_user_id=TELEGRAM_USER_ID,
            callback="v1.svc0",
            request_id="telegram:test:appointment-service",
        )
        date_menu = await handle_customer_callback(
            pool,
            bot_id=BOT_ID,
            business_id=BUSINESS_ID,
            shop_id=SHOP_ID,
            customer_id=CUSTOMER_ID,
            telegram_user_id=TELEGRAM_USER_ID,
            callback="v1.svcdone",
            request_id="telegram:test:appointment-services-done",
        )
        assert date_menu.keyboard is not None
        assert date_menu.keyboard.inline_keyboard[0][0].callback_data == "v1.day0"

        await handle_customer_callback(
            pool,
            bot_id=BOT_ID,
            business_id=BUSINESS_ID,
            shop_id=SHOP_ID,
            customer_id=CUSTOMER_ID,
            telegram_user_id=TELEGRAM_USER_ID,
            callback="v1.day0",
            request_id="telegram:test:appointment-day",
        )
        slot_menu = await handle_customer_callback(
            pool,
            bot_id=BOT_ID,
            business_id=BUSINESS_ID,
            shop_id=SHOP_ID,
            customer_id=CUSTOMER_ID,
            telegram_user_id=TELEGRAM_USER_ID,
            callback="v1.barany",
            request_id="telegram:test:appointment-barber",
        )
        assert slot_menu.keyboard is not None
        slot_callback = slot_menu.keyboard.inline_keyboard[0][0].callback_data
        assert slot_callback is not None and slot_callback.startswith("v1.slot")

        held = await handle_customer_callback(
            pool,
            bot_id=BOT_ID,
            business_id=BUSINESS_ID,
            shop_id=SHOP_ID,
            customer_id=CUSTOMER_ID,
            telegram_user_id=TELEGRAM_USER_ID,
            callback=slot_callback,
            request_id="telegram:test:appointment-slot",
        )
        assert "held" in held.text.lower()
        confirmed = await handle_customer_callback(
            pool,
            bot_id=BOT_ID,
            business_id=BUSINESS_ID,
            shop_id=SHOP_ID,
            customer_id=CUSTOMER_ID,
            telegram_user_id=TELEGRAM_USER_ID,
            callback="v1.holdok",
            request_id="telegram:test:appointment-confirm",
        )
        assert "confirmed" in confirmed.text.lower()
        async with pool.connection(timeout=5) as connection:
            cursor = await connection.execute(
                """
                select status::text from public.bookings
                where business_id = %s and shop_id = %s and customer_id = %s
                  and booking_type = 'appointment'
                order by created_at desc, id desc limit 1
                """,
                (BUSINESS_ID, SHOP_ID, CUSTOMER_ID),
            )
            assert await cursor.fetchone() == ("confirmed",)
    finally:
        await pool.close()


async def test_customer_language_callback_is_scoped_to_authenticated_identity() -> None:
    pool = create_database_pool(Settings(_env_file=None))
    await pool.open()
    try:
        response = await handle_customer_callback(
            pool,
            bot_id=BOT_ID,
            business_id=BUSINESS_ID,
            shop_id=SHOP_ID,
            customer_id=CUSTOMER_ID,
            telegram_user_id=TELEGRAM_USER_ID,
            callback="v1.lar",
            request_id="telegram:test:language-ar",
        )
        assert response.language == "ar"
        assert response.keyboard is not None
        async with pool.connection(timeout=5) as connection:
            cursor = await connection.execute(
                "select language::text from public.customers where id = %s",
                (CUSTOMER_ID,),
            )
            assert await cursor.fetchone() == ("ar",)

        with pytest.raises(CustomerMenuExpiredError):
            await handle_customer_callback(
                pool,
                bot_id=BOT_ID,
                business_id=BUSINESS_ID,
                shop_id=SHOP_ID,
                customer_id=CUSTOMER_ID,
                telegram_user_id=999999999,
                callback="v1.len",
                request_id="telegram:test:foreign-language",
            )

        for _attempt in range(2):
            escalation = await handle_customer_callback(
                pool,
                bot_id=BOT_ID,
                business_id=BUSINESS_ID,
                shop_id=SHOP_ID,
                customer_id=CUSTOMER_ID,
                telegram_user_id=TELEGRAM_USER_ID,
                callback="v1.c06",
                request_id="telegram:test:escalation-replay",
            )
            assert escalation.language == "ar"
        async with pool.connection(timeout=5) as connection:
            cursor = await connection.execute(
                """
                select
                  (select count(*) from public.audit_log
                   where action = 'telegram.escalation.created'
                     and entity_id = %s and request_id = %s),
                  (select count(*) from public.outbox_events
                   where topic = 'telegram.escalation'
                     and payload ->> 'customer_id' = %s)
                """,
                (CUSTOMER_ID, "telegram:test:escalation-replay", str(CUSTOMER_ID)),
            )
            assert await cursor.fetchone() == (1, 1)
    finally:
        await pool.close()
