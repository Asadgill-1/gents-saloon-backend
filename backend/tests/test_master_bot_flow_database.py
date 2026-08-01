import os
from uuid import UUID

import pytest

from app.core.config import Settings
from app.core.database import create_database_pool
from app.services.master_bot_flow import MasterMenuExpiredError, handle_master_callback

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_DATABASE_INTEGRATION") != "1",
    reason="requires reconstructed Phase 3 PostgreSQL test database",
)

PLATFORM_ID = UUID("00000000-0000-0000-0000-000000000001")
MASTER_BOT_ID = UUID("60000000-0000-0000-0000-000000000005")
TELEGRAM_USER_ID = 999101


async def test_master_read_views_repeat_platform_admin_authorization() -> None:
    pool = create_database_pool(Settings(_env_file=None))
    await pool.open()
    try:
        async with pool.connection(timeout=5) as connection, connection.transaction():
            await connection.execute(
                """
                update public.platform_admins
                set telegram_user_id = %s
                where auth_user_id = %s and active
                """,
                (TELEGRAM_USER_ID, PLATFORM_ID),
            )
            await connection.execute(
                """
                insert into public.bots (
                  id, role, token_ciphertext, bot_username, webhook_secret_hash
                ) values (
                  %s, 'master', 'v1.AAAAAAAAAAAAAAAA.RRRRRRRRRRRRRRRRRRRRRR',
                  'test_master_bot', 'v1:' || repeat('e', 64)
                )
                on conflict do nothing
                """,
                (MASTER_BOT_ID,),
            )

        expected_headings = {
            "m01": "Businesses",
            "m02": "Onboarding readiness",
            "m03": "Cash subscriptions",
            "m04": "Due or suspended subscriptions",
            "m05": "Exports and offboarding",
            "m06": "Bot health",
            "m07": "Sanitized escalations",
            "m08": "Global analytics",
            "m09": "Blocked Telegram users",
            "m10": "Database-visible system health",
        }
        for action, heading in expected_headings.items():
            response = await handle_master_callback(
                pool,
                actor_id=PLATFORM_ID,
                telegram_user_id=TELEGRAM_USER_ID,
                callback=f"v1.{action}",
            )
            assert heading in response.text

        with pytest.raises(MasterMenuExpiredError):
            await handle_master_callback(
                pool,
                actor_id=PLATFORM_ID,
                telegram_user_id=TELEGRAM_USER_ID + 1,
                callback="v1.m01",
            )
    finally:
        await pool.close()
