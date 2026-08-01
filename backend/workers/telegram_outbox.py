import asyncio
import sys
from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID, uuid4

from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramRetryAfter,
    TelegramUnauthorizedError,
)
from celery import shared_task
from pydantic import BaseModel, ConfigDict, Field

from app.core.ai_client import MoonshotAIClient
from app.core.config import get_settings
from app.core.database import create_database_pool
from app.core.redis import create_redis_client
from app.core.telegram import (
    AiogramTelegramTransport,
    TelegramReplyMarkup,
    decode_base64_key,
    decrypt_bot_token,
    decrypt_envelope,
    safe_telegram_error_code,
    webhook_secret_associated_data,
)
from app.services.bot_service import (
    TelegramUpdateError,
    claim_next_update,
    fail_claimed_update,
    process_claimed_update,
    purge_telegram_retention,
)

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

OUTBOX_MAX_ATTEMPTS = 5
OUTBOX_STALE_SECONDS = 120


class TelegramOutboundPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1]
    kind: Literal["message"]
    bot_id: UUID
    chat_id: int = Field(gt=0)
    text: str = Field(min_length=1, max_length=4096)
    keyboard: TelegramReplyMarkup | None = None


class TelegramRegistrationPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bot_id: UUID
    webhook_url: str = Field(min_length=12, max_length=2048, pattern=r"^https://")


@dataclass(frozen=True)
class ClaimedOutboxEvent:
    event_id: UUID
    bot_id: UUID
    business_id: UUID | None
    shop_id: UUID | None
    role: str
    token_envelope: str
    payload: TelegramOutboundPayload
    attempt_count: int


@dataclass(frozen=True)
class ClaimedRegistrationEvent:
    event_id: UUID
    bot_id: UUID
    business_id: UUID | None
    shop_id: UUID | None
    role: str
    token_envelope: str
    webhook_secret_envelope: str
    payload: TelegramRegistrationPayload
    attempt_count: int


async def _claim_next_outbox(pool: Any) -> ClaimedOutboxEvent | None:
    async with pool.connection(timeout=5) as connection, connection.transaction():
        await connection.execute("set local statement_timeout = '5s'")
        cursor = await connection.execute(
            """
            select e.id, b.id, b.business_id, b.shop_id, b.role::text,
                   b.token_ciphertext, e.payload, e.attempt_count
            from public.outbox_events e
            join public.bots b
              on b.id = (e.payload->>'bot_id')::uuid and b.active
            where e.topic = 'telegram.send_message'
              and e.dead_at is null
              and e.attempt_count < %s
              and (
                (e.status in ('pending', 'failed') and e.available_at <= now())
                or (e.status = 'processing'
                    and e.locked_at < now() - (%s * interval '1 second'))
              )
            order by e.created_at, e.id
            limit 1
            for update of e skip locked
            """,
            (OUTBOX_MAX_ATTEMPTS, OUTBOX_STALE_SECONDS),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        payload = TelegramOutboundPayload.model_validate(row[6])
        if payload.bot_id != UUID(str(row[1])):
            raise ValueError("telegram_outbox_bot_mismatch")
        await connection.execute(
            """
            update public.outbox_events
            set status = 'processing', locked_at = now(),
                attempt_count = attempt_count + 1, last_error_code = null
            where id = %s
            """,
            (row[0],),
        )
        return ClaimedOutboxEvent(
            event_id=UUID(str(row[0])),
            bot_id=UUID(str(row[1])),
            business_id=UUID(str(row[2])) if row[2] is not None else None,
            shop_id=UUID(str(row[3])) if row[3] is not None else None,
            role=str(row[4]),
            token_envelope=str(row[5]),
            payload=payload,
            attempt_count=int(row[7]) + 1,
        )


async def _claim_next_registration(pool: Any) -> ClaimedRegistrationEvent | None:
    async with pool.connection(timeout=5) as connection, connection.transaction():
        await connection.execute("set local statement_timeout = '5s'")
        cursor = await connection.execute(
            """
            select e.id, b.id, b.business_id, b.shop_id, b.role::text,
                   b.token_ciphertext, b.webhook_secret_ciphertext,
                   e.payload, e.attempt_count
            from public.outbox_events e
            join public.bots b
              on b.id = (e.payload->>'bot_id')::uuid and b.active
            where e.topic = 'telegram.register_webhook'
              and b.webhook_secret_ciphertext is not null
              and e.dead_at is null
              and e.attempt_count < %s
              and (
                (e.status in ('pending', 'failed') and e.available_at <= now())
                or (e.status = 'processing'
                    and e.locked_at < now() - (%s * interval '1 second'))
              )
            order by e.created_at, e.id
            limit 1
            for update of e skip locked
            """,
            (OUTBOX_MAX_ATTEMPTS, OUTBOX_STALE_SECONDS),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        payload = TelegramRegistrationPayload.model_validate(row[7])
        if payload.bot_id != UUID(str(row[1])):
            raise ValueError("telegram_registration_bot_mismatch")
        await connection.execute(
            """
            update public.outbox_events
            set status = 'processing', locked_at = now(),
                attempt_count = attempt_count + 1, last_error_code = null
            where id = %s
            """,
            (row[0],),
        )
        return ClaimedRegistrationEvent(
            event_id=UUID(str(row[0])),
            bot_id=UUID(str(row[1])),
            business_id=UUID(str(row[2])) if row[2] is not None else None,
            shop_id=UUID(str(row[3])) if row[3] is not None else None,
            role=str(row[4]),
            token_envelope=str(row[5]),
            webhook_secret_envelope=str(row[6]),
            payload=payload,
            attempt_count=int(row[8]) + 1,
        )


async def _mark_outbox_delivered(
    pool: Any,
    event: ClaimedOutboxEvent | ClaimedRegistrationEvent,
    message_id: int | None,
) -> None:
    async with pool.connection(timeout=5) as connection, connection.transaction():
        await connection.execute(
            """
            update public.outbox_events
            set status = 'delivered', delivered_at = now(), telegram_message_id = %s,
                locked_at = null, last_error_code = null
            where id = %s and status = 'processing'
            """,
            (message_id, event.event_id),
        )


async def _mark_outbox_failed(
    pool: Any,
    event: ClaimedOutboxEvent | ClaimedRegistrationEvent,
    *,
    error_code: str,
    permanent: bool,
    retry_after: int | None = None,
) -> None:
    exhausted = permanent or event.attempt_count >= OUTBOX_MAX_ATTEMPTS
    delay = retry_after or min(300, 2 ** min(event.attempt_count, 8))
    async with pool.connection(timeout=5) as connection, connection.transaction():
        await connection.execute(
            """
            update public.outbox_events
            set status = 'failed', last_error_code = %s,
                available_at = now() + (%s * interval '1 second'),
                dead_at = case when %s then now() else null end,
                locked_at = null
            where id = %s and status = 'processing'
            """,
            (error_code[:100], min(delay, 300), exhausted, event.event_id),
        )


async def deliver_next_outbox(pool: Any, *, encryption_key: bytes) -> bool:
    event = await _claim_next_outbox(pool)
    if event is None:
        return False
    try:
        token = decrypt_bot_token(
            event.token_envelope,
            key=encryption_key,
            bot_id=event.bot_id,
            role=event.role,
            business_id=event.business_id,
            shop_id=event.shop_id,
        )
        async with AiogramTelegramTransport(token) as transport:
            message_id = await transport.send_message(
                event.payload.chat_id,
                event.payload.text,
                reply_markup=event.payload.keyboard,
            )
        await _mark_outbox_delivered(pool, event, message_id)
    except TelegramRetryAfter as exc:
        await _mark_outbox_failed(
            pool,
            event,
            error_code="telegram_retry_after",
            permanent=False,
            retry_after=int(exc.retry_after),
        )
    except (TelegramUnauthorizedError, TelegramForbiddenError, TelegramBadRequest) as exc:
        await _mark_outbox_failed(
            pool,
            event,
            error_code=safe_telegram_error_code(exc),
            permanent=True,
        )
    except Exception as exc:
        await _mark_outbox_failed(
            pool,
            event,
            error_code=safe_telegram_error_code(exc),
            permanent=False,
        )
    return True


async def register_next_webhook(pool: Any, *, encryption_key: bytes) -> bool:
    event = await _claim_next_registration(pool)
    if event is None:
        return False
    try:
        token = decrypt_bot_token(
            event.token_envelope,
            key=encryption_key,
            bot_id=event.bot_id,
            role=event.role,
            business_id=event.business_id,
            shop_id=event.shop_id,
        )
        secret = decrypt_envelope(
            event.webhook_secret_envelope,
            key=encryption_key,
            associated_data=webhook_secret_associated_data(bot_id=event.bot_id),
        ).decode()
        async with AiogramTelegramTransport(token) as transport:
            await transport.set_webhook(event.payload.webhook_url, secret_token=secret)
        async with pool.connection(timeout=5) as connection, connection.transaction():
            await connection.execute(
                """
                update public.bots
                set healthy = true, registered_at = now(), last_health_at = now()
                where id = %s and active
                """,
                (event.bot_id,),
            )
        await _mark_outbox_delivered(pool, event, None)
    except TelegramRetryAfter as exc:
        await _mark_outbox_failed(
            pool,
            event,
            error_code="telegram_retry_after",
            permanent=False,
            retry_after=int(exc.retry_after),
        )
    except (TelegramUnauthorizedError, TelegramForbiddenError, TelegramBadRequest) as exc:
        await _mark_outbox_failed(
            pool,
            event,
            error_code=safe_telegram_error_code(exc),
            permanent=True,
        )
    except Exception as exc:
        await _mark_outbox_failed(
            pool,
            event,
            error_code=safe_telegram_error_code(exc),
            permanent=False,
        )
    return True


async def _process_telegram() -> dict[str, int]:
    settings = get_settings()
    encryption_key = decode_base64_key(settings.token_encryption_key.get_secret_value())
    pool = create_database_pool(settings)
    redis = create_redis_client(settings)
    await pool.open()
    worker_id = f"telegram-{uuid4()}"
    ai_client = (
        MoonshotAIClient(
            api_key=settings.moonshot_api_key.get_secret_value(),
            base_url=settings.moonshot_base_url,
            model=settings.moonshot_model,
            timeout=settings.moonshot_timeout_seconds,
        )
        if settings.moonshot_api_key.get_secret_value()
        and settings.moonshot_base_url
        and settings.moonshot_model
        else None
    )
    processed_updates = 0
    delivered_events = 0
    registered_webhooks = 0
    try:
        for _ in range(50):
            claimed = await claim_next_update(
                pool,
                worker_id=worker_id,
                stale_after_seconds=settings.telegram_update_stale_seconds,
                max_attempts=settings.telegram_update_max_attempts,
            )
            if claimed is None:
                break
            try:
                await process_claimed_update(
                    pool,
                    redis,
                    claimed,
                    encryption_key=encryption_key,
                    flood_limit=settings.telegram_flood_limit_per_minute,
                    ai_client=ai_client,
                    ai_hourly_budget=settings.ai_user_shop_hourly_budget,
                    ai_daily_budget=settings.ai_platform_daily_budget,
                )
            except TelegramUpdateError as exc:
                await fail_claimed_update(
                    pool,
                    claimed,
                    error_code=exc.code,
                    retryable=exc.retryable,
                    max_attempts=settings.telegram_update_max_attempts,
                )
            except Exception:
                await fail_claimed_update(
                    pool,
                    claimed,
                    error_code="telegram_update_failed",
                    retryable=True,
                    max_attempts=settings.telegram_update_max_attempts,
                )
            processed_updates += 1

        for _ in range(25):
            if not await register_next_webhook(pool, encryption_key=encryption_key):
                break
            registered_webhooks += 1
        for _ in range(100):
            if not await deliver_next_outbox(pool, encryption_key=encryption_key):
                break
            delivered_events += 1
        retention = await purge_telegram_retention(
            pool,
            retention_hours=settings.telegram_update_retention_hours,
        )
        return {
            "processed_updates": processed_updates,
            "delivered_events": delivered_events,
            "registered_webhooks": registered_webhooks,
            "purged_updates": retention["updates"],
            "purged_chat_messages": retention["chat_messages"],
        }
    finally:
        await redis.aclose()
        await pool.close()


@shared_task(  # type: ignore[untyped-decorator]
    name="workers.telegram.process",
    soft_time_limit=55,
    time_limit=60,
)
def process_telegram() -> dict[str, Any]:
    return asyncio.run(_process_telegram())


__all__ = [
    "TelegramOutboundPayload",
    "deliver_next_outbox",
    "process_telegram",
    "register_next_webhook",
]
