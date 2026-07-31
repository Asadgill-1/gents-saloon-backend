import base64
from uuid import UUID

import pytest

from app.core.telegram import (
    TelegramSecurityError,
    callback_data,
    decode_base64_key,
    decrypt_bot_token,
    digest_webhook_secret,
    encrypt_bot_token,
    verify_telegram_webhook_secret,
)
from app.services.bot_service import ROLE_MENUS, TRANSLATIONS

BOT_ID = UUID("60000000-0000-0000-0000-000000000001")
BUSINESS_ID = UUID("10000000-0000-0000-0000-000000000001")
SHOP_ID = UUID("20000000-0000-0000-0000-000000000001")
KEY_TEXT = base64.b64encode(b"k" * 32).decode()


def test_bot_token_envelope_round_trip_and_scope_binding() -> None:
    key = decode_base64_key(KEY_TEXT)
    envelope = encrypt_bot_token(
        "123456:test-token",
        key=key,
        bot_id=BOT_ID,
        role="customer",
        business_id=BUSINESS_ID,
        shop_id=SHOP_ID,
    )
    assert "123456:test-token" not in envelope
    assert (
        decrypt_bot_token(
            envelope,
            key=key,
            bot_id=BOT_ID,
            role="customer",
            business_id=BUSINESS_ID,
            shop_id=SHOP_ID,
        )
        == "123456:test-token"
    )
    with pytest.raises(TelegramSecurityError, match="authentication_failed"):
        decrypt_bot_token(
            envelope,
            key=key,
            bot_id=BOT_ID,
            role="owner",
            business_id=BUSINESS_ID,
            shop_id=SHOP_ID,
        )


def test_webhook_secret_uses_keyed_digest_and_constant_time_verification() -> None:
    key = decode_base64_key(KEY_TEXT)
    digest = digest_webhook_secret("secret_123", key=key, bot_id=BOT_ID)
    assert digest.startswith("v1:")
    assert "secret_123" not in digest
    assert verify_telegram_webhook_secret("secret_123", digest, key=key, bot_id=BOT_ID)
    assert not verify_telegram_webhook_secret("wrong", digest, key=key, bot_id=BOT_ID)
    assert not verify_telegram_webhook_secret(None, digest, key=key, bot_id=BOT_ID)


def test_key_must_decode_to_exactly_32_bytes() -> None:
    with pytest.raises(TelegramSecurityError, match="32_bytes"):
        decode_base64_key(base64.b64encode(b"short").decode())


def test_callback_payloads_are_versioned_bounded_and_contain_no_ids() -> None:
    value = callback_data("c01")
    assert value == "v1.c01"
    assert len(value.encode()) <= 64
    assert str(BUSINESS_ID) not in value
    with pytest.raises(ValueError, match="invalid_callback_action"):
        callback_data("x" * 60)


def test_role_menus_and_customer_locales_are_complete() -> None:
    assert set(ROLE_MENUS) == {"customer", "receptionist", "barber_crew", "owner", "master"}
    for rows in ROLE_MENUS.values():
        assert rows
        for row in rows:
            for _label, action in row:
                assert callback_data(action).startswith("v1.")
    for language in ("en", "ar", "hi", "ur"):
        assert set(TRANSLATIONS[language]) == {"welcome", "unavailable", "expired"}
