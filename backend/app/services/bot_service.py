import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Update
from psycopg.types.json import Jsonb

from app.core.entitlements import resolve_entitlement
from app.core.telegram import callback_data, decrypt_envelope, update_associated_data
from app.services.ai_service import handle_ai_customer_chat
from app.services.customer_bot_flow import MESSAGES as CUSTOMER_MESSAGES
from app.services.customer_bot_flow import (
    CustomerMenuExpiredError,
    customer_menu,
    handle_customer_callback,
    language_menu,
)

UPDATE_FLOOD_SCRIPT = """
local current = redis.call('INCR', KEYS[1])
if current == 1 then redis.call('EXPIRE', KEYS[1], 60) end
return current
"""

TRANSLATIONS: dict[str, dict[str, str]] = {
    "en": {
        "welcome": "Welcome to Gents Saloon. Choose an option:",
        "unavailable": "Service is temporarily unavailable.",
        "expired": "This menu expired. Start again.",
    },
    "ar": {
        "welcome": "أهلاً بك في صالون الرجال. اختر خدمة:",
        "unavailable": "الخدمة غير متوفرة مؤقتاً.",
        "expired": "انتهت صلاحية هذه القائمة. ابدأ من جديد.",
    },
    "hi": {
        "welcome": "जेंट्स सैलून में आपका स्वागत है। एक विकल्प चुनें:",
        "unavailable": "सेवा अस्थायी रूप से उपलब्ध नहीं है।",
        "expired": "यह मेनू समाप्त हो गया है। फिर से शुरू करें।",
    },
    "ur": {
        "welcome": "جینٹس سیلون میں خوش آمدید۔ ایک آپشن منتخب کریں:",
        "unavailable": "سروس عارضی طور پر دستیاب نہیں ہے۔",
        "expired": "اس مینو کی میعاد ختم ہو گئی۔ دوبارہ شروع کریں۔",
    },
}

ROLE_MENUS: dict[str, tuple[tuple[tuple[str, str], ...], ...]] = {
    "customer": (
        (("Book now", "c01"), ("Book appointment", "c02")),
        (("My booking", "c03"), ("Live queue", "c04")),
        (("Services & prices", "c05"), ("Talk to reception", "c06")),
    ),
    "receptionist": (
        (("Queue", "r01"), ("Appointments", "r02")),
        (("Walk-in", "r03"), ("Checkout", "r04")),
        (("Cash shift", "r05"), ("Advance", "r06")),
        (("EOD report", "r07"),),
    ),
    "barber_crew": (
        (("My queue today", "b01"), ("My earnings", "b02")),
        (("My payouts", "b03"), ("My advances", "b04")),
    ),
    "owner": (
        (("Business today", "o01"), ("Choose shop", "o02")),
        (("Shop today", "o03"), ("This month", "o04")),
        (("Barber performance", "o05"), ("Advances & payouts", "o06")),
        (("Audit", "o07"), ("Subscription status", "o08")),
    ),
    "master": (
        (("Businesses", "m01"), ("Onboard business", "m02")),
        (("Cash subscriptions", "m03"), ("Due / suspended", "m04")),
        (("Exports / offboarding", "m05"), ("Bot health", "m06")),
        (("Escalations", "m07"), ("Global analytics", "m08")),
        (("Blocked users", "m09"), ("System health", "m10")),
    ),
}


class TelegramUpdateError(Exception):
    code = "telegram_update_failed"
    retryable = True


class TelegramAuthorizationError(TelegramUpdateError):
    code = "telegram_actor_unauthorized"
    retryable = False


class TelegramRateLimitError(TelegramUpdateError):
    code = "telegram_rate_limited"
    retryable = False


class TelegramRateLimitUnavailableError(TelegramUpdateError):
    code = "telegram_rate_limit_unavailable"


@dataclass(frozen=True)
class BotScope:
    bot_id: UUID
    business_id: UUID | None
    shop_id: UUID | None
    role: str


@dataclass(frozen=True)
class TelegramActor:
    telegram_user_id: int
    actor_id: UUID | None
    customer_id: UUID | None
    language: str


@dataclass(frozen=True)
class ClaimedUpdate:
    row_id: UUID
    scope: BotScope
    update_id: int
    payload_envelope: str
    attempt_count: int


def _keyboard_for(role: str) -> InlineKeyboardMarkup:
    rows = ROLE_MENUS[role]
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=label, callback_data=callback_data(code))
                for label, code in row
            ]
            for row in rows
        ]
    )


def _user_and_chat(update: Update) -> tuple[int, int, str, str | None]:
    if update.message is not None and update.message.from_user is not None:
        return (
            update.message.from_user.id,
            update.message.chat.id,
            str(update.message.chat.type),
            update.message.text,
        )
    if update.callback_query is not None:
        sender = update.callback_query.from_user
        message = update.callback_query.message
        if message is None:
            raise TelegramAuthorizationError
        return sender.id, message.chat.id, str(message.chat.type), None
    raise TelegramAuthorizationError


async def store_received_update(
    pool: Any,
    *,
    bot_id: UUID,
    update_id: int,
    payload_envelope: str,
) -> bool:
    async with pool.connection(timeout=5) as connection, connection.transaction():
        cursor = await connection.execute(
            """
            insert into public.telegram_updates (bot_id, update_id, payload_ciphertext)
            select b.id, %s, %s
            from public.bots b
            where b.id = %s and b.active
            on conflict (bot_id, update_id) do nothing
            returning id
            """,
            (update_id, payload_envelope, bot_id),
        )
        return await cursor.fetchone() is not None


async def claim_next_update(
    pool: Any,
    *,
    worker_id: str,
    stale_after_seconds: int,
    max_attempts: int,
) -> ClaimedUpdate | None:
    async with pool.connection(timeout=5) as connection, connection.transaction():
        await connection.execute("set local statement_timeout = '5s'")
        cursor = await connection.execute(
            """
            select u.id, u.bot_id, b.business_id, b.shop_id, b.role::text,
                   u.update_id, u.payload_ciphertext, u.attempt_count
            from public.telegram_updates u
            join public.bots b on b.id = u.bot_id and b.active
            where u.attempt_count < %s
              and u.exhausted_at is null
              and (
                (u.status in ('received', 'failed') and u.available_at <= now())
                or (u.status = 'processing'
                    and u.claimed_at < now() - (%s * interval '1 second'))
              )
            order by u.received_at, u.id
            limit 1
            for update of u skip locked
            """,
            (max_attempts, stale_after_seconds),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        await connection.execute(
            """
            update public.telegram_updates
            set status = 'processing', attempt_count = attempt_count + 1,
                claimed_at = now(), claimed_by = %s, last_error_code = null
            where id = %s
            """,
            (worker_id[:200], row[0]),
        )
        return ClaimedUpdate(
            row_id=UUID(str(row[0])),
            scope=BotScope(
                bot_id=UUID(str(row[1])),
                business_id=UUID(str(row[2])) if row[2] is not None else None,
                shop_id=UUID(str(row[3])) if row[3] is not None else None,
                role=str(row[4]),
            ),
            update_id=int(row[5]),
            payload_envelope=str(row[6]),
            attempt_count=int(row[7]) + 1,
        )


async def _authorize_actor(
    connection: Any,
    *,
    scope: BotScope,
    telegram_user_id: int,
) -> TelegramActor:
    blocked = await connection.execute(
        """
        select 1 from public.telegram_user_blocks
        where telegram_user_id = %s
          and (expires_at is null or expires_at > now())
        """,
        (telegram_user_id,),
    )
    if await blocked.fetchone() is not None:
        raise TelegramAuthorizationError

    if scope.role == "master":
        cursor = await connection.execute(
            """
            select pa.auth_user_id
            from public.platform_admins pa
            join public.user_profiles up on up.auth_user_id = pa.auth_user_id and up.active
            where pa.telegram_user_id = %s and pa.active
            """,
            (telegram_user_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            raise TelegramAuthorizationError
        return TelegramActor(telegram_user_id, UUID(str(row[0])), None, "en")

    if scope.business_id is None or scope.shop_id is None:
        raise TelegramAuthorizationError

    if scope.role == "owner":
        cursor = await connection.execute(
            """
            select bo.auth_user_id
            from public.business_owners bo
            join public.user_profiles up on up.auth_user_id = bo.auth_user_id and up.active
            join public.businesses b on b.id = bo.business_id and b.status = 'active'
            where bo.business_id = %s and bo.telegram_user_id = %s and bo.active
            """,
            (scope.business_id, telegram_user_id),
        )
        row = await cursor.fetchone()
        if row is None:
            raise TelegramAuthorizationError
        return TelegramActor(telegram_user_id, UUID(str(row[0])), None, "en")

    if scope.role in {"receptionist", "barber_crew"}:
        allowed_roles = ["manager", "receptionist"] if scope.role == "receptionist" else ["barber"]
        cursor = await connection.execute(
            """
            select sm.auth_user_id
            from public.shop_memberships sm
            join public.shops sh
              on sh.id = sm.shop_id and sh.business_id = sm.business_id and sh.status = 'active'
            where sm.business_id = %s and sm.shop_id = %s
              and sm.telegram_user_id = %s and sm.active
              and sm.role::text = any(%s)
            """,
            (scope.business_id, scope.shop_id, telegram_user_id, allowed_roles),
        )
        row = await cursor.fetchone()
        if row is None:
            raise TelegramAuthorizationError
        return TelegramActor(
            telegram_user_id,
            UUID(str(row[0])) if row[0] is not None else None,
            None,
            "en",
        )

    if scope.role != "customer":
        raise TelegramAuthorizationError
    cursor = await connection.execute(
        """
        select id, language::text, blocked_at
        from public.customers
        where business_id = %s and shop_id = %s
          and telegram_user_id = %s and anonymized_at is null
        for update
        """,
        (scope.business_id, scope.shop_id, telegram_user_id),
    )
    row = await cursor.fetchone()
    if row is None:
        cursor = await connection.execute(
            """
            insert into public.customers (business_id, shop_id, telegram_user_id)
            values (%s, %s, %s)
            returning id, language::text, blocked_at
            """,
            (scope.business_id, scope.shop_id, telegram_user_id),
        )
        row = await cursor.fetchone()
    if row is None or row[2] is not None:
        raise TelegramAuthorizationError
    return TelegramActor(telegram_user_id, None, UUID(str(row[0])), str(row[1]))


async def _enforce_flood_limit(
    redis: Any,
    *,
    scope: BotScope,
    telegram_user_id: int,
    limit: int,
) -> None:
    actor_hash = hashlib.sha256(f"{scope.bot_id}:{telegram_user_id}".encode()).hexdigest()[:32]
    try:
        count = int(await redis.eval(UPDATE_FLOOD_SCRIPT, 1, f"telegram:flood:v1:{actor_hash}"))
    except Exception as exc:
        raise TelegramRateLimitUnavailableError from exc
    if count > limit:
        raise TelegramRateLimitError


def _outbound_payload(
    *, bot_id: UUID, chat_id: int, text: str, keyboard: InlineKeyboardMarkup | None
) -> dict[str, Any]:
    return {
        "version": 1,
        "kind": "message",
        "bot_id": str(bot_id),
        "chat_id": chat_id,
        "text": text,
        "keyboard": keyboard.model_dump(exclude_none=True) if keyboard else None,
    }


async def _queue_message(
    connection: Any,
    *,
    scope: BotScope,
    update_id: int,
    chat_id: int,
    text: str,
    keyboard: InlineKeyboardMarkup | None = None,
) -> None:
    await connection.execute(
        """
        insert into public.outbox_events (
          business_id, shop_id, topic, dedupe_key, payload
        ) values (%s, %s, 'telegram.send_message', %s, %s)
        on conflict (dedupe_key) do nothing
        """,
        (
            scope.business_id,
            scope.shop_id,
            f"telegram:update:{scope.bot_id}:{update_id}:message",
            Jsonb(
                _outbound_payload(
                    bot_id=scope.bot_id,
                    chat_id=chat_id,
                    text=text,
                    keyboard=keyboard,
                )
            ),
        ),
    )


async def process_claimed_update(
    pool: Any,
    redis: Any,
    claimed: ClaimedUpdate,
    *,
    encryption_key: bytes,
    flood_limit: int,
    ai_client: Any = None,
    ai_hourly_budget: int = 20,
    ai_daily_budget: int = 5000,
) -> None:
    payload = decrypt_envelope(
        claimed.payload_envelope,
        key=encryption_key,
        associated_data=update_associated_data(
            bot_id=claimed.scope.bot_id,
            update_id=claimed.update_id,
        ),
    )
    update = Update.model_validate_json(payload)
    telegram_user_id, chat_id, chat_type, message_text = _user_and_chat(update)
    if chat_type != "private" or chat_id != telegram_user_id:
        raise TelegramAuthorizationError

    async with pool.connection(timeout=5) as connection, connection.transaction():
        await connection.execute("set local statement_timeout = '5s'")
        actor = await _authorize_actor(
            connection,
            scope=claimed.scope,
            telegram_user_id=telegram_user_id,
        )

    await _enforce_flood_limit(
        redis,
        scope=claimed.scope,
        telegram_user_id=telegram_user_id,
        limit=flood_limit,
    )

    if claimed.scope.business_id is not None and claimed.scope.shop_id is not None:
        async with pool.connection(timeout=5) as connection, connection.transaction():
            entitlement = await resolve_entitlement(
                connection,
                business_id=claimed.scope.business_id,
                shop_id=claimed.scope.shop_id,
                at=datetime.now(UTC),
            )
            if not entitlement.active:
                await _queue_message(
                    connection,
                    scope=claimed.scope,
                    update_id=claimed.update_id,
                    chat_id=chat_id,
                    text=TRANSLATIONS[actor.language]["unavailable"],
                )
                await connection.execute(
                    """
                    update public.telegram_updates
                    set status = 'completed', completed_at = now(), claimed_by = null
                    where id = %s and status = 'processing'
                    """,
                    (claimed.row_id,),
                )
                return

    callback = update.callback_query.data if update.callback_query is not None else None
    language = actor.language if claimed.scope.role == "customer" else "en"
    menu: InlineKeyboardMarkup | None = (
        customer_menu(language)
        if claimed.scope.role == "customer"
        else _keyboard_for(claimed.scope.role)
    )
    if callback is not None and claimed.scope.role == "customer":
        assert claimed.scope.business_id is not None
        assert claimed.scope.shop_id is not None
        assert actor.customer_id is not None
        try:
            flow_response = await handle_customer_callback(
                pool,
                bot_id=claimed.scope.bot_id,
                business_id=claimed.scope.business_id,
                shop_id=claimed.scope.shop_id,
                customer_id=actor.customer_id,
                telegram_user_id=telegram_user_id,
                callback=callback,
                request_id=f"telegram:{claimed.scope.bot_id}:{claimed.update_id}",
            )
            response_text = flow_response.text
            menu = flow_response.keyboard
        except CustomerMenuExpiredError:
            response_text = CUSTOMER_MESSAGES[language]["expired"]
    elif callback is not None:
        allowed = {callback_data(code) for row in ROLE_MENUS[claimed.scope.role] for _, code in row}
        response_text = (
            "Selection received. Continue with the displayed secure form."
            if callback in allowed
            else TRANSLATIONS[language]["expired"]
        )
    elif message_text == "/start" and claimed.scope.role == "customer":
        response_text = CUSTOMER_MESSAGES[language]["choose_language"]
        menu = language_menu()
    elif claimed.scope.role != "customer":
        response_text = TRANSLATIONS[language]["welcome"]
    else:
        assert claimed.scope.business_id is not None
        assert claimed.scope.shop_id is not None
        response_text = await handle_ai_customer_chat(
            pool,
            ai_client,
            message_text or "",
            business_id=claimed.scope.business_id,
            shop_id=claimed.scope.shop_id,
            customer_id=actor.customer_id,
            telegram_user_id=telegram_user_id,
            request_id=f"telegram:{claimed.scope.bot_id}:{claimed.update_id}",
            redis=redis,
            hourly_budget=ai_hourly_budget,
            daily_budget=ai_daily_budget,
        )

    async with pool.connection(timeout=5) as connection, connection.transaction():
        await _queue_message(
            connection,
            scope=claimed.scope,
            update_id=claimed.update_id,
            chat_id=chat_id,
            text=response_text,
            keyboard=menu,
        )
        await connection.execute(
            """
            update public.telegram_updates
            set status = 'completed', completed_at = now(), claimed_by = null
            where id = %s and status = 'processing'
            """,
            (claimed.row_id,),
        )


async def fail_claimed_update(
    pool: Any,
    claimed: ClaimedUpdate,
    *,
    error_code: str,
    retryable: bool,
    max_attempts: int,
) -> None:
    exhausted = not retryable or claimed.attempt_count >= max_attempts
    delay_seconds = min(300, 2 ** min(claimed.attempt_count, 8))
    async with pool.connection(timeout=5) as connection, connection.transaction():
        await connection.execute(
            """
            update public.telegram_updates
            set status = 'failed', last_failure_at = now(), last_error_code = %s,
                available_at = now() + (%s * interval '1 second'),
                exhausted_at = case when %s then now() else null end,
                claimed_by = null
            where id = %s and status = 'processing'
            """,
            (error_code[:100], delay_seconds, exhausted, claimed.row_id),
        )


async def purge_telegram_retention(pool: Any, *, retention_hours: int) -> dict[str, int]:
    cutoff = datetime.now(UTC) - timedelta(hours=retention_hours)
    async with pool.connection(timeout=5) as connection, connection.transaction():
        update_cursor = await connection.execute(
            """
            delete from public.telegram_updates
            where received_at < %s and status in ('completed', 'failed')
            """,
            (cutoff,),
        )
        chat_cursor = await connection.execute(
            "delete from public.chat_messages where expires_at <= now()",
        )
        return {"updates": update_cursor.rowcount, "chat_messages": chat_cursor.rowcount}


def serialize_update(update: Update) -> bytes:
    return json.dumps(
        update.model_dump(mode="json", exclude_none=True),
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode()


__all__ = [
    "BotScope",
    "ClaimedUpdate",
    "ROLE_MENUS",
    "TRANSLATIONS",
    "TelegramAuthorizationError",
    "TelegramRateLimitError",
    "TelegramRateLimitUnavailableError",
    "TelegramUpdateError",
    "claim_next_update",
    "fail_claimed_update",
    "process_claimed_update",
    "purge_telegram_retention",
    "serialize_update",
    "store_received_update",
]
