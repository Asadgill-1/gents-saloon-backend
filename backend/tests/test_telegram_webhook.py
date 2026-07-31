import base64
from types import SimpleNamespace
from uuid import UUID

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.api.telegram import router
from app.core.telegram import decode_base64_key, digest_webhook_secret

BOT_ID = UUID("60000000-0000-0000-0000-000000000001")
KEY_TEXT = base64.b64encode(b"k" * 32).decode()


class Cursor:
    def __init__(self, row: tuple[object, ...] | None) -> None:
        self.row = row

    async def fetchone(self) -> tuple[object, ...] | None:
        return self.row


class Context:
    def __init__(self, value: object) -> None:
        self.value = value

    async def __aenter__(self) -> object:
        return self.value

    async def __aexit__(self, *_args: object) -> None:
        return None


class Connection:
    def __init__(self, digest: str) -> None:
        self.digest = digest
        self.inserted = False

    def transaction(self) -> Context:
        return Context(self)

    async def execute(self, query: str, _params: object = None) -> Cursor:
        if "select webhook_secret_hash" in query:
            return Cursor((self.digest,))
        if "insert into public.telegram_updates" in query:
            if self.inserted:
                return Cursor(None)
            self.inserted = True
            return Cursor((UUID("70000000-0000-0000-0000-000000000001"),))
        raise AssertionError("unexpected query")


class Pool:
    def __init__(self, connection: Connection) -> None:
        self.connection_value = connection

    def connection(self, **_kwargs: object) -> Context:
        return Context(self.connection_value)


def _client() -> TestClient:
    key = decode_base64_key(KEY_TEXT)
    digest = digest_webhook_secret("webhook-secret", key=key, bot_id=BOT_ID)
    app = FastAPI()
    app.include_router(router)
    app.state.settings = SimpleNamespace(
        token_encryption_key=SecretStr(KEY_TEXT),
        telegram_webhook_hmac_key=SecretStr(KEY_TEXT),
        telegram_webhook_max_body_bytes=1024,
    )
    app.state.database_pool = Pool(Connection(digest))
    return TestClient(app)


def _update(*, chat_type: str = "private") -> dict[str, object]:
    return {
        "update_id": 1001,
        "message": {
            "message_id": 10,
            "date": 1_700_000_000,
            "from": {
                "id": 999001,
                "is_bot": False,
                "first_name": "Test",
            },
            "chat": {
                "id": 999001 if chat_type == "private" else -999001,
                "type": chat_type,
            },
            "text": "/start",
        },
    }


def test_webhook_validates_secret_and_deduplicates_durably() -> None:
    client = _client()
    headers = {"X-Telegram-Bot-Api-Secret-Token": "webhook-secret"}
    first = client.post(f"/api/v1/telegram/webhook/{BOT_ID}", json=_update(), headers=headers)
    second = client.post(f"/api/v1/telegram/webhook/{BOT_ID}", json=_update(), headers=headers)
    assert first.status_code == 200
    assert first.json() == {"status": "accepted"}
    assert second.status_code == 200
    assert second.json() == {"status": "duplicate"}


def test_webhook_rejects_wrong_secret_malformed_oversized_and_group_updates() -> None:
    client = _client()
    path = f"/api/v1/telegram/webhook/{BOT_ID}"
    assert (
        client.post(
            path,
            json=_update(),
            headers={
                "X-Telegram-Bot-Api-Secret-Token": "wrong",
            },
        ).status_code
        == 401
    )
    headers = {"X-Telegram-Bot-Api-Secret-Token": "webhook-secret"}
    malformed = client.post(
        path,
        content=b"{not-json",
        headers={**headers, "Content-Type": "application/json"},
    )
    assert malformed.status_code == 400
    oversized = client.post(
        path,
        content=b"x" * 1025,
        headers={**headers, "Content-Type": "application/json"},
    )
    assert oversized.status_code == 413
    group = client.post(path, json=_update(chat_type="group"), headers=headers)
    assert group.status_code == 400
    assert group.json()["detail"] == "telegram_private_chat_required"
