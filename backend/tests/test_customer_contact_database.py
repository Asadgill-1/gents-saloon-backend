import os
from typing import Any
from uuid import UUID

import pytest
from aiogram.types import Update

from app.core.config import Settings
from app.core.database import create_database_pool
from app.core.telegram import encrypt_envelope, update_associated_data
from app.services.bot_service import (
    BotScope,
    ClaimedUpdate,
    process_claimed_update,
    serialize_update,
)

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_DATABASE_INTEGRATION") != "1",
    reason="requires reconstructed Phase 3 PostgreSQL test database",
)

BOT_ID = UUID("60000000-0000-0000-0000-000000000001")
BUSINESS_ID = UUID("10000000-0000-0000-0000-000000000001")
SHOP_ID = UUID("20000000-0000-0000-0000-000000000001")
TELEGRAM_USER_ID = 999009


class _Redis:
    async def eval(self, *_args: Any) -> int:
        return 1


def _message(update_id: int, *, first_name: str, contact: dict[str, Any] | None = None) -> Update:
    message: dict[str, Any] = {
        "message_id": update_id,
        "date": 0,
        "chat": {"id": TELEGRAM_USER_ID, "type": "private"},
        "from": {
            "id": TELEGRAM_USER_ID,
            "is_bot": False,
            "first_name": first_name,
            "last_name": "Customer",
        },
    }
    if contact is None:
        message["text"] = "/start"
    else:
        message["contact"] = contact
    return Update.model_validate({"update_id": update_id, "message": message})


def _language_callback(update_id: int) -> Update:
    return Update.model_validate(
        {
            "update_id": update_id,
            "callback_query": {
                "id": f"callback-{update_id}",
                "from": {
                    "id": TELEGRAM_USER_ID,
                    "is_bot": False,
                    "first_name": "Test",
                    "last_name": "Customer",
                },
                "chat_instance": "customer-contact-test",
                "data": "v1.lar",
                "message": {
                    "message_id": update_id,
                    "date": 0,
                    "chat": {"id": TELEGRAM_USER_ID, "type": "private"},
                },
            },
        }
    )


async def _process(pool: Any, key: bytes, update: Update) -> dict[str, Any]:
    update_id = update.update_id
    envelope = encrypt_envelope(
        serialize_update(update),
        key=key,
        associated_data=update_associated_data(bot_id=BOT_ID, update_id=update_id),
    )
    async with pool.connection(timeout=5) as connection, connection.transaction():
        cursor = await connection.execute(
            """
            insert into public.telegram_updates (
              bot_id, update_id, payload_ciphertext, status, attempt_count,
              claimed_at, claimed_by
            ) values (%s, %s, %s, 'processing', 1, now(), 'contact-test')
            returning id
            """,
            (BOT_ID, update_id, envelope),
        )
        row = await cursor.fetchone()
        assert row is not None
        row_id = UUID(str(row[0]))
    await process_claimed_update(
        pool,
        _Redis(),
        ClaimedUpdate(
            row_id=row_id,
            scope=BotScope(BOT_ID, BUSINESS_ID, SHOP_ID, "customer"),
            update_id=update_id,
            payload_envelope=envelope,
            attempt_count=1,
        ),
        encryption_key=key,
        flood_limit=20,
    )
    async with pool.connection(timeout=5) as connection:
        cursor = await connection.execute(
            """
            select payload from public.outbox_events
            where dedupe_key = %s
            """,
            (f"telegram:update:{BOT_ID}:{update_id}:message",),
        )
        payload = await cursor.fetchone()
        assert payload is not None
        return dict(payload[0])


async def test_customer_profile_capture_requires_a_self_shared_contact() -> None:
    settings = Settings(_env_file=None)
    key = b"k" * 32
    pool = create_database_pool(settings)
    await pool.open()
    try:
        start = await _process(pool, key, _message(991001, first_name="Test"))
        assert "inline_keyboard" in start["keyboard"]

        language = await _process(pool, key, _language_callback(991002))
        assert language["keyboard"]["keyboard"][0][0]["request_contact"] is True

        foreign = await _process(
            pool,
            key,
            _message(
                991003,
                first_name="Changed",
                contact={
                    "phone_number": "+971501112222",
                    "first_name": "Other",
                    "user_id": TELEGRAM_USER_ID + 1,
                },
            ),
        )
        assert foreign["keyboard"]["keyboard"][0][0]["request_contact"] is True
        async with pool.connection(timeout=5) as connection:
            cursor = await connection.execute(
                """
                select display_name, phone_e164, language::text
                from public.customers
                where business_id = %s and shop_id = %s and telegram_user_id = %s
                """,
                (BUSINESS_ID, SHOP_ID, TELEGRAM_USER_ID),
            )
            assert await cursor.fetchone() == ("Test Customer", None, "ar")

        own = await _process(
            pool,
            key,
            _message(
                991004,
                first_name="Changed",
                contact={
                    "phone_number": "971 (50) 111-3333",
                    "first_name": "Test",
                    "user_id": TELEGRAM_USER_ID,
                },
            ),
        )
        assert "inline_keyboard" in own["keyboard"]

        await _process(
            pool,
            key,
            _message(
                991005,
                first_name="Overwrite",
                contact={
                    "phone_number": "+971501119999",
                    "first_name": "Overwrite",
                    "user_id": TELEGRAM_USER_ID,
                },
            ),
        )
        async with pool.connection(timeout=5) as connection:
            cursor = await connection.execute(
                """
                select display_name, phone_e164, language::text
                from public.customers
                where business_id = %s and shop_id = %s and telegram_user_id = %s
                """,
                (BUSINESS_ID, SHOP_ID, TELEGRAM_USER_ID),
            )
            assert await cursor.fetchone() == ("Test Customer", "+971501113333", "ar")
    finally:
        await pool.close()
