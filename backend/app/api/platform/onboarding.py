from typing import Annotated, NoReturn
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status

from app.api.context import verified_identity
from app.api.rate_limit import enforce_authenticated_rate_limit
from app.core.auth import VerifiedIdentity
from app.core.telegram import TelegramSecurityError, decode_base64_key
from app.services.platform_onboarding_service import (
    BotRegistrationRequest,
    BotRegistrationResponse,
    LegalTaxOnboardingRequest,
    LegalTaxOnboardingResponse,
    PlatformOnboardingConflictError,
    ShopCreateRequest,
    ShopCreateResponse,
    StaffInvitationRequest,
    StaffInvitationResponse,
    create_shop,
    invite_staff,
    onboard_legal_tax,
    register_bot,
)
from app.services.platform_operations import (
    IdempotencyConflictError,
    IdempotencyInProgressError,
    PlatformAdminRequiredError,
)

router = APIRouter(prefix="/api/v1/platform/businesses", tags=["platform-onboarding"])

IdempotencyKey = Annotated[
    str,
    Header(
        alias="Idempotency-Key",
        min_length=16,
        max_length=128,
        pattern=r"^[A-Za-z0-9._:-]+$",
    ),
]


def _raise_error(exc: Exception) -> NoReturn:
    if isinstance(exc, PlatformAdminRequiredError):
        raise HTTPException(status_code=403, detail="platform_admin_required") from exc
    if isinstance(exc, IdempotencyConflictError):
        raise HTTPException(status_code=409, detail="idempotency_key_reused") from exc
    if isinstance(exc, IdempotencyInProgressError):
        raise HTTPException(status_code=409, detail="idempotency_request_in_progress") from exc
    if isinstance(exc, PlatformOnboardingConflictError):
        raise HTTPException(status_code=409, detail="platform_onboarding_conflict") from exc
    if isinstance(exc, TelegramSecurityError):
        raise HTTPException(status_code=503, detail="telegram_configuration_unavailable") from exc
    raise HTTPException(status_code=503, detail="platform_onboarding_unavailable") from exc


@router.post("/{business_id}/shops", response_model=ShopCreateResponse, status_code=201)
async def add_shop(
    business_id: UUID,
    request: Request,
    payload: ShopCreateRequest,
    idempotency_key: IdempotencyKey,
    identity: Annotated[VerifiedIdentity, Depends(verified_identity)],
) -> ShopCreateResponse:
    await enforce_authenticated_rate_limit(request, identity.auth_user_id)
    try:
        return await create_shop(
            request.app.state.database_pool,
            actor_id=identity.auth_user_id,
            business_id=business_id,
            idempotency_key=idempotency_key,
            request_id=request.state.request_id,
            payload=payload,
        )
    except Exception as exc:
        _raise_error(exc)


@router.post(
    "/{business_id}/shops/{shop_id}/staff-invitations",
    response_model=StaffInvitationResponse,
    status_code=status.HTTP_201_CREATED,
)
async def add_staff_invitation(
    business_id: UUID,
    shop_id: UUID,
    request: Request,
    payload: StaffInvitationRequest,
    idempotency_key: IdempotencyKey,
    identity: Annotated[VerifiedIdentity, Depends(verified_identity)],
) -> StaffInvitationResponse:
    await enforce_authenticated_rate_limit(request, identity.auth_user_id)
    try:
        return await invite_staff(
            request.app.state.database_pool,
            actor_id=identity.auth_user_id,
            business_id=business_id,
            shop_id=shop_id,
            idempotency_key=idempotency_key,
            request_id=request.state.request_id,
            payload=payload,
        )
    except Exception as exc:
        _raise_error(exc)


@router.post(
    "/{business_id}/shops/{shop_id}/bots",
    response_model=BotRegistrationResponse,
    status_code=status.HTTP_201_CREATED,
)
async def add_bot(
    business_id: UUID,
    shop_id: UUID,
    request: Request,
    payload: BotRegistrationRequest,
    idempotency_key: IdempotencyKey,
    identity: Annotated[VerifiedIdentity, Depends(verified_identity)],
) -> BotRegistrationResponse:
    await enforce_authenticated_rate_limit(request, identity.auth_user_id)
    settings = request.app.state.settings
    try:
        return await register_bot(
            request.app.state.database_pool,
            actor_id=identity.auth_user_id,
            business_id=business_id,
            shop_id=shop_id,
            idempotency_key=idempotency_key,
            request_id=request.state.request_id,
            payload=payload,
            encryption_key=decode_base64_key(settings.token_encryption_key.get_secret_value()),
            webhook_hmac_key=decode_base64_key(
                settings.telegram_webhook_hmac_key.get_secret_value()
            ),
            webhook_base_url=settings.webhook_base_url,
        )
    except Exception as exc:
        _raise_error(exc)


@router.post(
    "/{business_id}/shops/{shop_id}/legal-tax",
    response_model=LegalTaxOnboardingResponse,
    status_code=status.HTTP_201_CREATED,
)
async def add_legal_tax(
    business_id: UUID,
    shop_id: UUID,
    request: Request,
    payload: LegalTaxOnboardingRequest,
    idempotency_key: IdempotencyKey,
    identity: Annotated[VerifiedIdentity, Depends(verified_identity)],
) -> LegalTaxOnboardingResponse:
    await enforce_authenticated_rate_limit(request, identity.auth_user_id)
    try:
        return await onboard_legal_tax(
            request.app.state.database_pool,
            actor_id=identity.auth_user_id,
            business_id=business_id,
            shop_id=shop_id,
            idempotency_key=idempotency_key,
            request_id=request.state.request_id,
            payload=payload,
        )
    except Exception as exc:
        _raise_error(exc)
