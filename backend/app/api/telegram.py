from typing import Annotated
from uuid import UUID

from aiogram.types import Update
from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, ValidationError

from app.core.telegram import (
    TelegramSecurityError,
    decode_base64_key,
    encrypt_envelope,
    update_associated_data,
    verify_telegram_webhook_secret,
)
from app.services.bot_service import serialize_update, store_received_update

router = APIRouter(prefix="/api/v1/telegram", tags=["telegram"])


class WebhookResponse(BaseModel):
    status: str


async def _bounded_body(request: Request, *, maximum: int) -> bytes:
    declared = request.headers.get("content-length")
    if declared is not None:
        try:
            if int(declared) > maximum:
                raise HTTPException(status_code=413, detail="telegram_update_too_large")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="telegram_content_length_invalid") from exc
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > maximum:
            raise HTTPException(status_code=413, detail="telegram_update_too_large")
    return bytes(body)


def _require_private_update(update: Update) -> None:
    if update.message is not None:
        if (
            update.message.from_user is None
            or str(update.message.chat.type) != "private"
            or update.message.chat.id != update.message.from_user.id
        ):
            raise HTTPException(status_code=400, detail="telegram_private_chat_required")
        return
    if update.callback_query is not None and update.callback_query.message is not None:
        message = update.callback_query.message
        if (
            str(message.chat.type) != "private"
            or message.chat.id != update.callback_query.from_user.id
        ):
            raise HTTPException(status_code=400, detail="telegram_private_chat_required")
        return
    raise HTTPException(status_code=400, detail="telegram_update_unsupported")


@router.post("/webhook/{bot_id}", response_model=WebhookResponse)
async def handle_telegram_webhook(
    bot_id: UUID,
    request: Request,
    x_telegram_bot_api_secret_token: Annotated[str | None, Header()] = None,
) -> WebhookResponse:
    settings = request.app.state.settings
    try:
        encryption_key = decode_base64_key(settings.token_encryption_key.get_secret_value())
        webhook_key = decode_base64_key(settings.telegram_webhook_hmac_key.get_secret_value())
    except TelegramSecurityError as exc:
        raise HTTPException(status_code=503, detail="telegram_configuration_unavailable") from exc

    async with request.app.state.database_pool.connection(timeout=5) as connection:
        cursor = await connection.execute(
            """
            select webhook_secret_hash
            from public.bots
            where id = %s and active
            """,
            (bot_id,),
        )
        bot = await cursor.fetchone()
    if bot is None:
        raise HTTPException(status_code=404, detail="telegram_bot_not_found")
    if not verify_telegram_webhook_secret(
        x_telegram_bot_api_secret_token,
        str(bot[0]),
        key=webhook_key,
        bot_id=bot_id,
    ):
        raise HTTPException(status_code=401, detail="telegram_webhook_unauthorized")

    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type != "application/json":
        raise HTTPException(status_code=415, detail="telegram_content_type_invalid")
    body = await _bounded_body(request, maximum=settings.telegram_webhook_max_body_bytes)
    try:
        update = Update.model_validate_json(body)
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail="telegram_update_invalid") from exc
    _require_private_update(update)

    normalized = serialize_update(update)
    envelope = encrypt_envelope(
        normalized,
        key=encryption_key,
        associated_data=update_associated_data(bot_id=bot_id, update_id=update.update_id),
    )
    inserted = await store_received_update(
        request.app.state.database_pool,
        bot_id=bot_id,
        update_id=update.update_id,
        payload_envelope=envelope,
    )
    return WebhookResponse(status="accepted" if inserted else "duplicate")
