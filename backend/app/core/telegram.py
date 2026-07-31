import base64
import hashlib
import hmac
import os
import re
from collections.abc import Mapping
from typing import Protocol
from uuid import UUID

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import InlineKeyboardMarkup
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

ENVELOPE_VERSION = 1
NONCE_BYTES = 12
CALLBACK_PATTERN = re.compile(r"^v1\.[a-z0-9_]{1,48}$")


class TelegramSecurityError(ValueError):
    """A Telegram credential or encrypted envelope is invalid."""


class TelegramTransport(Protocol):
    async def send_message(
        self,
        chat_id: int,
        text: str,
        *,
        reply_markup: InlineKeyboardMarkup | None = None,
    ) -> int: ...

    async def answer_callback_query(
        self,
        callback_query_id: str,
        *,
        text: str | None = None,
        show_alert: bool = False,
    ) -> None: ...

    async def set_webhook(self, url: str, *, secret_token: str) -> None: ...


def _b64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64url_decode(value: str) -> bytes:
    try:
        padding = "=" * (-len(value) % 4)
        return base64.b64decode(value + padding, altchars=b"-_", validate=True)
    except (ValueError, TypeError) as exc:
        raise TelegramSecurityError("invalid_envelope_encoding") from exc


def decode_base64_key(encoded_key: str) -> bytes:
    key = _b64url_decode(encoded_key)
    if len(key) != 32:
        raise TelegramSecurityError("key_must_decode_to_32_bytes")
    return key


def encrypt_envelope(plaintext: bytes, *, key: bytes, associated_data: bytes) -> str:
    if len(key) != 32:
        raise TelegramSecurityError("key_must_be_32_bytes")
    nonce = os.urandom(NONCE_BYTES)
    ciphertext = AESGCM(key).encrypt(nonce, plaintext, associated_data)
    return f"v{ENVELOPE_VERSION}.{_b64url_encode(nonce)}.{_b64url_encode(ciphertext)}"


def decrypt_envelope(envelope: str, *, key: bytes, associated_data: bytes) -> bytes:
    if len(key) != 32:
        raise TelegramSecurityError("key_must_be_32_bytes")
    parts = envelope.split(".")
    if len(parts) != 3 or parts[0] != f"v{ENVELOPE_VERSION}":
        raise TelegramSecurityError("unsupported_envelope_version")
    nonce = _b64url_decode(parts[1])
    if len(nonce) != NONCE_BYTES:
        raise TelegramSecurityError("invalid_envelope_nonce")
    try:
        return AESGCM(key).decrypt(nonce, _b64url_decode(parts[2]), associated_data)
    except Exception as exc:
        raise TelegramSecurityError("envelope_authentication_failed") from exc


def bot_token_associated_data(
    *,
    bot_id: UUID,
    role: str,
    business_id: UUID | None,
    shop_id: UUID | None,
) -> bytes:
    scope = f"{business_id or '-'}:{shop_id or '-'}"
    return f"telegram-bot-token:v1:{bot_id}:{role}:{scope}".encode()


def update_associated_data(*, bot_id: UUID, update_id: int) -> bytes:
    return f"telegram-update:v1:{bot_id}:{update_id}".encode()


def webhook_secret_associated_data(*, bot_id: UUID) -> bytes:
    return f"telegram-webhook-secret:v1:{bot_id}".encode()


def encrypt_bot_token(
    token: str,
    *,
    key: bytes,
    bot_id: UUID,
    role: str,
    business_id: UUID | None,
    shop_id: UUID | None,
) -> str:
    if not token or len(token) > 256:
        raise TelegramSecurityError("invalid_bot_token")
    return encrypt_envelope(
        token.encode(),
        key=key,
        associated_data=bot_token_associated_data(
            bot_id=bot_id,
            role=role,
            business_id=business_id,
            shop_id=shop_id,
        ),
    )


def decrypt_bot_token(
    envelope: str,
    *,
    key: bytes,
    bot_id: UUID,
    role: str,
    business_id: UUID | None,
    shop_id: UUID | None,
) -> str:
    plaintext = decrypt_envelope(
        envelope,
        key=key,
        associated_data=bot_token_associated_data(
            bot_id=bot_id,
            role=role,
            business_id=business_id,
            shop_id=shop_id,
        ),
    )
    try:
        return plaintext.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise TelegramSecurityError("invalid_bot_token_encoding") from exc


def digest_webhook_secret(secret: str, *, key: bytes, bot_id: UUID) -> str:
    if not secret or len(secret) > 256:
        raise TelegramSecurityError("invalid_webhook_secret")
    message = b"telegram-webhook:v1:" + bot_id.bytes + b":" + secret.encode()
    return "v1:" + hmac.new(key, message, hashlib.sha256).hexdigest()


def verify_telegram_webhook_secret(
    supplied_secret: str | None,
    expected_digest: str,
    *,
    key: bytes,
    bot_id: UUID,
) -> bool:
    if supplied_secret is None or not expected_digest.startswith("v1:"):
        return False
    try:
        supplied_digest = digest_webhook_secret(supplied_secret, key=key, bot_id=bot_id)
    except TelegramSecurityError:
        return False
    return hmac.compare_digest(supplied_digest, expected_digest)


def callback_data(action: str) -> str:
    value = f"v1.{action}"
    if len(value.encode()) > 64 or CALLBACK_PATTERN.fullmatch(value) is None:
        raise ValueError("invalid_callback_action")
    return value


class AiogramTelegramTransport:
    """Small aiogram adapter that never retains a decrypted token after use."""

    def __init__(self, token: str) -> None:
        self._bot = Bot(token=token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))

    async def __aenter__(self) -> "AiogramTelegramTransport":
        return self

    async def __aexit__(self, *_args: object) -> None:
        await self._bot.session.close()

    async def send_message(
        self,
        chat_id: int,
        text: str,
        *,
        reply_markup: InlineKeyboardMarkup | None = None,
    ) -> int:
        message = await self._bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=reply_markup,
        )
        return message.message_id

    async def answer_callback_query(
        self,
        callback_query_id: str,
        *,
        text: str | None = None,
        show_alert: bool = False,
    ) -> None:
        await self._bot.answer_callback_query(
            callback_query_id=callback_query_id,
            text=text,
            show_alert=show_alert,
        )

    async def set_webhook(self, url: str, *, secret_token: str) -> None:
        accepted = await self._bot.set_webhook(
            url=url,
            secret_token=secret_token,
            allowed_updates=["message", "callback_query"],
            drop_pending_updates=False,
        )
        if not accepted:
            raise RuntimeError("telegram_webhook_rejected")


def safe_telegram_error_code(exc: BaseException) -> str:
    name = type(exc).__name__.lower()
    known: Mapping[str, str] = {
        "telegramunauthorizederror": "telegram_unauthorized",
        "telegramforbiddenerror": "telegram_forbidden",
        "telegrambadrequest": "telegram_bad_request",
        "telegramretryafter": "telegram_retry_after",
        "telegramnetworkerror": "telegram_network",
        "timeouterror": "telegram_timeout",
    }
    return known.get(name, "telegram_delivery_failed")


__all__ = [
    "AiogramTelegramTransport",
    "TelegramSecurityError",
    "TelegramTransport",
    "bot_token_associated_data",
    "callback_data",
    "decode_base64_key",
    "decrypt_bot_token",
    "decrypt_envelope",
    "digest_webhook_secret",
    "encrypt_bot_token",
    "encrypt_envelope",
    "safe_telegram_error_code",
    "update_associated_data",
    "verify_telegram_webhook_secret",
    "webhook_secret_associated_data",
]
