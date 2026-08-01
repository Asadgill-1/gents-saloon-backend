import base64
from uuid import UUID

import pytest
from aiogram.types import ReplyKeyboardMarkup, Update

from app.core.telegram import (
    TelegramSecurityError,
    callback_data,
    decode_base64_key,
    decrypt_bot_token,
    digest_webhook_secret,
    encrypt_bot_token,
    verify_telegram_webhook_secret,
)
from app.services.bot_service import ROLE_MENUS, TRANSLATIONS, extract_incoming_update
from app.services.customer_bot_flow import (
    BUTTONS,
    MENU_LABELS,
    MESSAGES,
    customer_menu,
    language_menu,
)

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
        assert set(MESSAGES[language]) == set(MESSAGES["en"])
        assert set(BUTTONS[language]) == set(BUTTONS["en"])
        assert MENU_LABELS[language]
        menu = customer_menu(language)
        assert all(
            (button.callback_data or "").startswith("v1.")
            for row in menu.inline_keyboard
            for button in row
        )
    assert len(language_menu().inline_keyboard) == 2


def _contact_update(*, contact_user_id: int, phone: str) -> Update:
    return Update.model_validate(
        {
            "update_id": 41,
            "message": {
                "message_id": 7,
                "date": 0,
                "chat": {"id": 9001, "type": "private"},
                "from": {
                    "id": 9001,
                    "is_bot": False,
                    "first_name": "  Test\u0000",
                    "last_name": "Customer  ",
                },
                "contact": {
                    "phone_number": phone,
                    "first_name": "Test",
                    "user_id": contact_user_id,
                },
            },
        }
    )


def test_customer_contact_capture_accepts_only_the_senders_contact() -> None:
    own = extract_incoming_update(_contact_update(contact_user_id=9001, phone="971 (50) 123-4567"))
    foreign = extract_incoming_update(_contact_update(contact_user_id=9002, phone="+971501234568"))

    assert own.profile_name == "Test Customer"
    assert own.contact_phone == "+971501234567"
    assert foreign.contact_phone is None


def test_outbound_contact_keyboard_schema_is_supported() -> None:
    from workers.telegram_outbox import TelegramOutboundPayload

    payload = TelegramOutboundPayload.model_validate(
        {
            "version": 1,
            "kind": "message",
            "bot_id": str(BOT_ID),
            "chat_id": 9001,
            "text": "Share contact",
            "keyboard": {
                "keyboard": [[{"text": "Share", "request_contact": True}]],
                "resize_keyboard": True,
                "one_time_keyboard": True,
            },
        }
    )

    assert isinstance(payload.keyboard, ReplyKeyboardMarkup)
    assert payload.keyboard.keyboard[0][0].request_contact is True
