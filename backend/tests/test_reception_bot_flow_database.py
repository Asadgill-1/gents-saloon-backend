import os
from uuid import UUID

import pytest

from app.core.config import Settings
from app.core.database import create_database_pool
from app.services.reception_bot_flow import handle_reception_callback
from app.services.reception_cash_flow import (
    handle_reception_cash_callback,
    handle_reception_cash_input,
    handle_reception_eod_callback,
)

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_DATABASE_INTEGRATION") != "1",
    reason="requires reconstructed Phase 3 PostgreSQL test database",
)

BOT_ID = UUID("60000000-0000-0000-0000-000000000003")
BUSINESS_ID = UUID("10000000-0000-0000-0000-000000000001")
SHOP_ID = UUID("20000000-0000-0000-0000-000000000001")
ACTOR_ID = UUID("00000000-0000-0000-0000-000000000003")
TELEGRAM_USER_ID = 999101


async def _authorize_reception_bot(pool: object) -> None:
    async with pool.connection(timeout=5) as connection, connection.transaction():  # type: ignore[attr-defined]
        await connection.execute(
            """
            update public.shop_memberships
            set telegram_user_id = %s, updated_at = now()
            where auth_user_id = %s and business_id = %s and shop_id = %s
              and role = 'receptionist'
            """,
            (TELEGRAM_USER_ID, ACTOR_ID, BUSINESS_ID, SHOP_ID),
        )
        await connection.execute(
            """
            insert into public.bots (
              id, business_id, shop_id, role, token_ciphertext,
              bot_username, webhook_secret_hash
            ) values (
              %s, %s, %s, 'receptionist',
              'v1.AAAAAAAAAAAAAAAA.RRRRRRRRRRRRRRRRRRRRRR',
              'test_reception_bot', 'v1:' || repeat('c', 64)
            )
            on conflict (id) do nothing
            """,
            (BOT_ID, BUSINESS_ID, SHOP_ID),
        )


async def test_reception_queue_card_reauthorizes_and_runs_lifecycle() -> None:
    pool = create_database_pool(Settings(_env_file=None))
    await pool.open()
    try:
        await _authorize_reception_bot(pool)

        listing = await handle_reception_callback(
            pool,
            bot_id=BOT_ID,
            business_id=BUSINESS_ID,
            shop_id=SHOP_ID,
            actor_id=ACTOR_ID,
            telegram_user_id=TELEGRAM_USER_ID,
            callback="v1.r01",
            request_id="telegram:test:reception-list",
        )
        assert listing.keyboard is not None
        requested_callback = next(
            button.callback_data
            for row in listing.keyboard.inline_keyboard
            for button in row
            if button.text.startswith("requested")
        )
        assert requested_callback is not None
        card = await handle_reception_callback(
            pool,
            bot_id=BOT_ID,
            business_id=BUSINESS_ID,
            shop_id=SHOP_ID,
            actor_id=ACTOR_ID,
            telegram_user_id=TELEGRAM_USER_ID,
            callback=requested_callback,
            request_id="telegram:test:reception-card",
        )
        assert "Status: requested" in card.text
        assert card.keyboard is not None

        for _attempt in range(2):
            confirmed = await handle_reception_callback(
                pool,
                bot_id=BOT_ID,
                business_id=BUSINESS_ID,
                shop_id=SHOP_ID,
                actor_id=ACTOR_ID,
                telegram_user_id=TELEGRAM_USER_ID,
                callback="v1.recconfirm",
                request_id="telegram:test:reception-confirm",
            )
            assert confirmed.text == "Booking confirmed."

        started = await handle_reception_callback(
            pool,
            bot_id=BOT_ID,
            business_id=BUSINESS_ID,
            shop_id=SHOP_ID,
            actor_id=ACTOR_ID,
            telegram_user_id=TELEGRAM_USER_ID,
            callback="v1.recstart",
            request_id="telegram:test:reception-start",
        )
        assert started.text == "Booking in_service."
        completed = await handle_reception_callback(
            pool,
            bot_id=BOT_ID,
            business_id=BUSINESS_ID,
            shop_id=SHOP_ID,
            actor_id=ACTOR_ID,
            telegram_user_id=TELEGRAM_USER_ID,
            callback="v1.reccomplete",
            request_id="telegram:test:reception-complete",
        )
        assert completed.text == "Booking completed."
        assert completed.keyboard is not None
        assert completed.keyboard.inline_keyboard[0][0].callback_data == "v1.r04"
        async with pool.connection(timeout=5) as connection:
            cursor = await connection.execute(
                """
                select count(*), min(status::text)
                from public.bookings
                where business_id = %s and shop_id = %s and status = 'completed'
                  and source = 'telegram'
                """,
                (BUSINESS_ID, SHOP_ID),
            )
            assert await cursor.fetchone() == (1, "completed")
    finally:
        await pool.close()


async def test_reception_cash_flow_is_replay_safe_and_renders_eod() -> None:
    pool = create_database_pool(Settings(_env_file=None))
    await pool.open()
    try:
        await _authorize_reception_bot(pool)
        await handle_reception_cash_callback(
            pool,
            bot_id=BOT_ID,
            business_id=BUSINESS_ID,
            shop_id=SHOP_ID,
            actor_id=ACTOR_ID,
            telegram_user_id=TELEGRAM_USER_ID,
            callback="v1.r05",
        )
        prompt = await handle_reception_cash_callback(
            pool,
            bot_id=BOT_ID,
            business_id=BUSINESS_ID,
            shop_id=SHOP_ID,
            actor_id=ACTOR_ID,
            telegram_user_id=TELEGRAM_USER_ID,
            callback="v1.cashopen",
        )
        assert "register label" in prompt.text
        await handle_reception_cash_input(
            pool,
            bot_id=BOT_ID,
            business_id=BUSINESS_ID,
            shop_id=SHOP_ID,
            actor_id=ACTOR_ID,
            telegram_user_id=TELEGRAM_USER_ID,
            text="Telegram Desk",
            request_id="telegram:test:cash-register",
        )
        for _attempt in range(2):
            opened = await handle_reception_cash_input(
                pool,
                bot_id=BOT_ID,
                business_id=BUSINESS_ID,
                shop_id=SHOP_ID,
                actor_id=ACTOR_ID,
                telegram_user_id=TELEGRAM_USER_ID,
                text="125.00",
                request_id="telegram:test:cash-open",
            )
            assert "Expected cash: AED 125.00" in opened.text

        listing = await handle_reception_cash_callback(
            pool,
            bot_id=BOT_ID,
            business_id=BUSINESS_ID,
            shop_id=SHOP_ID,
            actor_id=ACTOR_ID,
            telegram_user_id=TELEGRAM_USER_ID,
            callback="v1.cashrefresh",
        )
        assert listing.keyboard is not None
        shift_callback = next(
            button.callback_data
            for row in listing.keyboard.inline_keyboard
            for button in row
            if button.text.startswith("Telegram Desk")
        )
        assert shift_callback is not None
        await handle_reception_cash_callback(
            pool,
            bot_id=BOT_ID,
            business_id=BUSINESS_ID,
            shop_id=SHOP_ID,
            actor_id=ACTOR_ID,
            telegram_user_id=TELEGRAM_USER_ID,
            callback=shift_callback,
        )
        await handle_reception_cash_callback(
            pool,
            bot_id=BOT_ID,
            business_id=BUSINESS_ID,
            shop_id=SHOP_ID,
            actor_id=ACTOR_ID,
            telegram_user_id=TELEGRAM_USER_ID,
            callback="v1.cashpayin",
        )
        await handle_reception_cash_input(
            pool,
            bot_id=BOT_ID,
            business_id=BUSINESS_ID,
            shop_id=SHOP_ID,
            actor_id=ACTOR_ID,
            telegram_user_id=TELEGRAM_USER_ID,
            text="25.00",
            request_id="telegram:test:cash-pay-in-amount",
        )
        for _attempt in range(2):
            movement = await handle_reception_cash_input(
                pool,
                bot_id=BOT_ID,
                business_id=BUSINESS_ID,
                shop_id=SHOP_ID,
                actor_id=ACTOR_ID,
                telegram_user_id=TELEGRAM_USER_ID,
                text="Additional float",
                request_id="telegram:test:cash-pay-in",
            )
            assert "Expected cash: AED 150.00" in movement.text

        listing = await handle_reception_cash_callback(
            pool,
            bot_id=BOT_ID,
            business_id=BUSINESS_ID,
            shop_id=SHOP_ID,
            actor_id=ACTOR_ID,
            telegram_user_id=TELEGRAM_USER_ID,
            callback="v1.cashrefresh",
        )
        assert listing.keyboard is not None
        shift_callback = next(
            button.callback_data
            for row in listing.keyboard.inline_keyboard
            for button in row
            if button.text.startswith("Telegram Desk")
        )
        assert shift_callback is not None
        await handle_reception_cash_callback(
            pool,
            bot_id=BOT_ID,
            business_id=BUSINESS_ID,
            shop_id=SHOP_ID,
            actor_id=ACTOR_ID,
            telegram_user_id=TELEGRAM_USER_ID,
            callback=shift_callback,
        )
        await handle_reception_cash_callback(
            pool,
            bot_id=BOT_ID,
            business_id=BUSINESS_ID,
            shop_id=SHOP_ID,
            actor_id=ACTOR_ID,
            telegram_user_id=TELEGRAM_USER_ID,
            callback="v1.cashclose",
        )
        closed = await handle_reception_cash_input(
            pool,
            bot_id=BOT_ID,
            business_id=BUSINESS_ID,
            shop_id=SHOP_ID,
            actor_id=ACTOR_ID,
            telegram_user_id=TELEGRAM_USER_ID,
            text="150.00",
            request_id="telegram:test:cash-close",
        )
        assert "variance AED 0.00" in closed.text

        report = await handle_reception_eod_callback(
            pool,
            business_id=BUSINESS_ID,
            shop_id=SHOP_ID,
            actor_id=ACTOR_ID,
            telegram_user_id=TELEGRAM_USER_ID,
        )
        assert "EOD report" in report.text
        assert "Closed-shift variance: AED" in report.text
    finally:
        await pool.close()
