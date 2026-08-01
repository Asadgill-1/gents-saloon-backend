import os
from uuid import UUID

import pytest

from app.core.config import Settings
from app.core.database import create_database_pool
from app.services.reception_bot_flow import handle_reception_callback

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_DATABASE_INTEGRATION") != "1",
    reason="requires reconstructed Phase 3 PostgreSQL test database",
)

BOT_ID = UUID("60000000-0000-0000-0000-000000000003")
BUSINESS_ID = UUID("10000000-0000-0000-0000-000000000001")
SHOP_ID = UUID("20000000-0000-0000-0000-000000000001")
ACTOR_ID = UUID("00000000-0000-0000-0000-000000000003")
TELEGRAM_USER_ID = 999101


async def test_reception_queue_card_reauthorizes_and_runs_lifecycle() -> None:
    pool = create_database_pool(Settings(_env_file=None))
    await pool.open()
    try:
        async with pool.connection(timeout=5) as connection, connection.transaction():
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
                """,
                (BOT_ID, BUSINESS_ID, SHOP_ID),
            )

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
        async with pool.connection(timeout=5) as connection:
            cursor = await connection.execute(
                """
                select count(*), min(status::text)
                from public.bookings
                where business_id = %s and shop_id = %s and status = 'in_service'
                  and source = 'telegram'
                """,
                (BUSINESS_ID, SHOP_ID),
            )
            assert await cursor.fetchone() == (1, "in_service")
    finally:
        await pool.close()
