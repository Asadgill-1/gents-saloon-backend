import hashlib
import re
import secrets
from datetime import date, datetime, time
from typing import Any, Literal
from uuid import UUID, uuid4

from aiogram import Bot
from psycopg.errors import ExclusionViolation, UniqueViolation
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

from app.core.telegram import (
    TelegramSecurityError,
    digest_webhook_secret,
    encrypt_bot_token,
    encrypt_envelope,
    webhook_secret_associated_data,
)
from app.services.platform_operations import (
    complete_idempotency,
    require_platform_admin,
    reserve_idempotency,
    write_platform_event,
)


class PlatformOnboardingConflictError(Exception):
    """The requested onboarding state conflicts with current durable state."""


class ShopCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    internal_code: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    timezone: str = Field(default="Asia/Dubai", min_length=1, max_length=100)
    open_time: time
    close_time: time
    default_service_minutes: int = Field(ge=1, le=1440)
    eod_time: time
    paid_from: date | None = None
    paid_until: date | None = None

    @model_validator(mode="after")
    def valid_coverage(self) -> "ShopCreateRequest":
        if (self.paid_from is None) != (self.paid_until is None):
            raise ValueError("paid_from and paid_until must be supplied together")
        if (
            self.paid_from is not None
            and self.paid_until is not None
            and self.paid_until < self.paid_from
        ):
            raise ValueError("paid_until cannot precede paid_from")
        return self


class ShopCreateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    shop_id: UUID
    business_id: UUID
    subscription_id: UUID | None
    public_queue_token: str


class StaffInvitationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=3, max_length=320)
    role: Literal["manager", "receptionist", "barber"]

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        normalized = value.strip().lower()
        if re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", normalized) is None:
            raise ValueError("invalid email")
        return normalized


class StaffInvitationResponse(BaseModel):
    invitation_id: UUID
    business_id: UUID
    shop_id: UUID
    status: str
    expires_at: datetime


class BotRegistrationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["owner", "receptionist", "barber_crew", "customer"]
    token: SecretStr


class BotRegistrationFingerprint(BaseModel):
    role: str
    token_sha256: str


class BotRegistrationResponse(BaseModel):
    bot_id: UUID
    business_id: UUID
    shop_id: UUID
    role: str
    bot_username: str
    registration_status: str


class LegalTaxOnboardingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    legal_name: str = Field(min_length=1, max_length=300)
    address: str = Field(min_length=1, max_length=1000)
    trade_license_number: str | None = Field(default=None, min_length=1, max_length=200)
    trade_license_expiry: date | None = None
    vat_registered: bool
    trn: str | None = Field(default=None, pattern=r"^[0-9]{15}$")
    pricing_mode: Literal["vat_inclusive", "vat_exclusive"]
    effective_from: datetime

    @model_validator(mode="after")
    def tax_shape(self) -> "LegalTaxOnboardingRequest":
        if self.effective_from.tzinfo is None:
            raise ValueError("effective_from must include timezone")
        if self.vat_registered != (self.trn is not None):
            raise ValueError("TRN is required only for VAT-registered businesses")
        return self


class LegalTaxOnboardingResponse(BaseModel):
    business_id: UUID
    shop_id: UUID
    legal_profile_id: UUID
    vat_registered: bool
    effective_from: datetime


async def create_shop(
    pool: Any,
    *,
    actor_id: UUID,
    business_id: UUID,
    idempotency_key: str,
    request_id: str,
    payload: ShopCreateRequest,
) -> ShopCreateResponse:
    public_token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(public_token.encode()).hexdigest()
    async with pool.connection(timeout=5) as connection, connection.transaction():
        await require_platform_admin(connection, actor_id)
        replay = await reserve_idempotency(
            connection,
            scope=f"platform.shop.create:{business_id}",
            actor_id=actor_id,
            key=idempotency_key,
            payload=payload,
            expected_status=201,
        )
        if replay is not None:
            return ShopCreateResponse.model_validate(replay)
        cursor = await connection.execute(
            "select billing_mode::text from public.businesses where id = %s for update",
            (business_id,),
        )
        business = await cursor.fetchone()
        if business is None:
            raise PlatformOnboardingConflictError
        try:
            cursor = await connection.execute(
                """
                insert into public.shops (
                  business_id, name, internal_code, timezone, public_queue_token_hash,
                  open_time, close_time, default_service_minutes, eod_time
                ) values (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                returning id
                """,
                (
                    business_id,
                    payload.name.strip(),
                    payload.internal_code,
                    payload.timezone,
                    token_hash,
                    payload.open_time,
                    payload.close_time,
                    payload.default_service_minutes,
                    payload.eod_time,
                ),
            )
            shop_id = UUID(str((await cursor.fetchone())[0]))
            subscription_id = None
            if str(business[0]) == "per_shop":
                if payload.paid_from is None or payload.paid_until is None:
                    raise PlatformOnboardingConflictError
                cursor = await connection.execute(
                    """
                    insert into public.subscriptions (
                      business_id, shop_id, scope, status, paid_from, paid_until
                    ) values (%s, %s, 'shop', 'active', %s, %s)
                    returning id
                    """,
                    (business_id, shop_id, payload.paid_from, payload.paid_until),
                )
                subscription_id = UUID(str((await cursor.fetchone())[0]))
        except UniqueViolation as exc:
            raise PlatformOnboardingConflictError from exc
        response = ShopCreateResponse(
            shop_id=shop_id,
            business_id=business_id,
            subscription_id=subscription_id,
            public_queue_token=public_token,
        )
        await write_platform_event(
            connection,
            business_id=business_id,
            shop_id=shop_id,
            actor_id=actor_id,
            action="shop.created",
            entity_type="shop",
            entity_id=shop_id,
            request_id=request_id,
            details={"shop_id": str(shop_id), "business_id": str(business_id)},
        )
        await complete_idempotency(
            connection,
            scope=f"platform.shop.create:{business_id}",
            actor_id=actor_id,
            key=idempotency_key,
            response_status=201,
            response=response,
        )
        return response


async def invite_staff(
    pool: Any,
    *,
    actor_id: UUID,
    business_id: UUID,
    shop_id: UUID,
    idempotency_key: str,
    request_id: str,
    payload: StaffInvitationRequest,
) -> StaffInvitationResponse:
    async with pool.connection(timeout=5) as connection, connection.transaction():
        await require_platform_admin(connection, actor_id)
        replay = await reserve_idempotency(
            connection,
            scope=f"platform.staff.invite:{shop_id}",
            actor_id=actor_id,
            key=idempotency_key,
            payload=payload,
            expected_status=201,
        )
        if replay is not None:
            return StaffInvitationResponse.model_validate(replay)
        try:
            cursor = await connection.execute(
                """
                insert into public.staff_invitations (
                  business_id, shop_id, email, role, invited_by_auth_user_id
                ) values (%s, %s, %s, %s, %s)
                returning id, expires_at
                """,
                (business_id, shop_id, payload.email, payload.role, actor_id),
            )
            row = await cursor.fetchone()
        except UniqueViolation as exc:
            raise PlatformOnboardingConflictError from exc
        if row is None:
            raise PlatformOnboardingConflictError
        response = StaffInvitationResponse(
            invitation_id=UUID(str(row[0])),
            business_id=business_id,
            shop_id=shop_id,
            status="pending",
            expires_at=row[1],
        )
        await write_platform_event(
            connection,
            business_id=business_id,
            shop_id=shop_id,
            actor_id=actor_id,
            action="staff.invitation_requested",
            entity_type="staff_invitation",
            entity_id=response.invitation_id,
            request_id=request_id,
            details={
                "invitation_id": str(response.invitation_id),
                "shop_id": str(shop_id),
                "role": payload.role,
            },
        )
        await complete_idempotency(
            connection,
            scope=f"platform.staff.invite:{shop_id}",
            actor_id=actor_id,
            key=idempotency_key,
            response_status=201,
            response=response,
        )
        return response


async def _telegram_identity(token: str) -> str:
    bot = Bot(token=token)
    try:
        identity = await bot.get_me()
    except Exception as exc:
        raise PlatformOnboardingConflictError from exc
    finally:
        await bot.session.close()
    if identity.username is None:
        raise PlatformOnboardingConflictError
    return identity.username


async def register_bot(
    pool: Any,
    *,
    actor_id: UUID,
    business_id: UUID,
    shop_id: UUID,
    idempotency_key: str,
    request_id: str,
    payload: BotRegistrationRequest,
    encryption_key: bytes,
    webhook_hmac_key: bytes,
    webhook_base_url: str,
) -> BotRegistrationResponse:
    if not webhook_base_url.startswith("https://"):
        raise TelegramSecurityError("webhook_base_url_must_use_https")
    token = payload.token.get_secret_value()
    username = await _telegram_identity(token)
    bot_id = uuid4()
    webhook_secret = secrets.token_urlsafe(32)
    token_envelope = encrypt_bot_token(
        token,
        key=encryption_key,
        bot_id=bot_id,
        role=payload.role,
        business_id=business_id,
        shop_id=shop_id,
    )
    secret_envelope = encrypt_envelope(
        webhook_secret.encode(),
        key=encryption_key,
        associated_data=webhook_secret_associated_data(bot_id=bot_id),
    )
    fingerprint = BotRegistrationFingerprint(
        role=payload.role,
        token_sha256=hashlib.sha256(token.encode()).hexdigest(),
    )
    async with pool.connection(timeout=5) as connection, connection.transaction():
        await require_platform_admin(connection, actor_id)
        replay = await reserve_idempotency(
            connection,
            scope=f"platform.bot.register:{shop_id}:{payload.role}",
            actor_id=actor_id,
            key=idempotency_key,
            payload=fingerprint,
            expected_status=201,
        )
        if replay is not None:
            return BotRegistrationResponse.model_validate(replay)
        try:
            await connection.execute(
                """
                insert into public.bots (
                  id, business_id, shop_id, role, token_ciphertext,
                  webhook_secret_ciphertext, bot_username, webhook_secret_hash,
                  healthy, registered_at
                ) values (%s, %s, %s, %s, %s, %s, %s, %s, false, null)
                """,
                (
                    bot_id,
                    business_id,
                    shop_id,
                    payload.role,
                    token_envelope,
                    secret_envelope,
                    username,
                    digest_webhook_secret(webhook_secret, key=webhook_hmac_key, bot_id=bot_id),
                ),
            )
        except UniqueViolation as exc:
            raise PlatformOnboardingConflictError from exc
        response = BotRegistrationResponse(
            bot_id=bot_id,
            business_id=business_id,
            shop_id=shop_id,
            role=payload.role,
            bot_username=username,
            registration_status="pending",
        )
        await write_platform_event(
            connection,
            business_id=business_id,
            shop_id=shop_id,
            actor_id=actor_id,
            action="telegram.register_webhook",
            entity_type="bot",
            entity_id=bot_id,
            request_id=request_id,
            details={
                "bot_id": str(bot_id),
                "webhook_url": f"{webhook_base_url.rstrip('/')}/api/v1/telegram/webhook/{bot_id}",
            },
        )
        await complete_idempotency(
            connection,
            scope=f"platform.bot.register:{shop_id}:{payload.role}",
            actor_id=actor_id,
            key=idempotency_key,
            response_status=201,
            response=response,
        )
        return response


async def onboard_legal_tax(
    pool: Any,
    *,
    actor_id: UUID,
    business_id: UUID,
    shop_id: UUID,
    idempotency_key: str,
    request_id: str,
    payload: LegalTaxOnboardingRequest,
) -> LegalTaxOnboardingResponse:
    async with pool.connection(timeout=5) as connection, connection.transaction():
        await require_platform_admin(connection, actor_id)
        replay = await reserve_idempotency(
            connection,
            scope=f"platform.legal-tax:{shop_id}",
            actor_id=actor_id,
            key=idempotency_key,
            payload=payload,
            expected_status=201,
        )
        if replay is not None:
            return LegalTaxOnboardingResponse.model_validate(replay)
        await connection.execute(
            """
            update public.businesses
            set legal_name = %s, trade_license_number = %s,
                trade_license_expiry = %s, vat_registered = %s, trn = %s,
                invoice_address = %s, updated_at = now()
            where id = %s
            """,
            (
                payload.legal_name,
                payload.trade_license_number,
                payload.trade_license_expiry,
                payload.vat_registered,
                payload.trn,
                payload.address,
                business_id,
            ),
        )
        try:
            cursor = await connection.execute(
                """
                insert into public.shop_legal_profiles (
                  business_id, shop_id, legal_name, address, vat_registered, trn,
                  pricing_mode, invoice_document_type, effective_from,
                  created_by_auth_user_id
                ) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                returning id
                """,
                (
                    business_id,
                    shop_id,
                    payload.legal_name,
                    payload.address,
                    payload.vat_registered,
                    payload.trn,
                    payload.pricing_mode,
                    "tax_invoice" if payload.vat_registered else "receipt",
                    payload.effective_from,
                    actor_id,
                ),
            )
            profile_id = UUID(str((await cursor.fetchone())[0]))
        except (UniqueViolation, ExclusionViolation) as exc:
            raise PlatformOnboardingConflictError from exc
        response = LegalTaxOnboardingResponse(
            business_id=business_id,
            shop_id=shop_id,
            legal_profile_id=profile_id,
            vat_registered=payload.vat_registered,
            effective_from=payload.effective_from,
        )
        await write_platform_event(
            connection,
            business_id=business_id,
            shop_id=shop_id,
            actor_id=actor_id,
            action="legal_tax.onboarded",
            entity_type="shop_legal_profile",
            entity_id=profile_id,
            request_id=request_id,
            details={"legal_profile_id": str(profile_id), "shop_id": str(shop_id)},
        )
        await complete_idempotency(
            connection,
            scope=f"platform.legal-tax:{shop_id}",
            actor_id=actor_id,
            key=idempotency_key,
            response_status=201,
            response=response,
        )
        return response


__all__ = [
    "BotRegistrationRequest",
    "BotRegistrationResponse",
    "LegalTaxOnboardingRequest",
    "LegalTaxOnboardingResponse",
    "PlatformOnboardingConflictError",
    "ShopCreateRequest",
    "ShopCreateResponse",
    "StaffInvitationRequest",
    "StaffInvitationResponse",
    "create_shop",
    "invite_staff",
    "onboard_legal_tax",
    "register_bot",
]
