from typing import Annotated, NoReturn
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status

from app.api.context import verified_identity
from app.api.rate_limit import enforce_authenticated_rate_limit
from app.core.auth import VerifiedIdentity
from app.services.subscription_service import (
    BillingModeTransitionRequest,
    BillingModeTransitionResponse,
    BillingTransitionConflictError,
    CashReceiptRequest,
    CashReceiptResponse,
    IdempotencyConflictError,
    IdempotencyInProgressError,
    PaidCoverageRequiredError,
    PlatformAdminRequiredError,
    ReceiptReversalRequest,
    ResumeSubscriptionRequest,
    SubscriptionNotFoundError,
    SubscriptionStateConflictError,
    SubscriptionStateResponse,
    SuspendSubscriptionRequest,
    record_cash_receipt,
    resume_subscription,
    reverse_cash_receipt,
    suspend_subscription,
    transition_billing_mode,
)

router = APIRouter(prefix="/api/v1/platform", tags=["platform-subscriptions"])

IdempotencyKey = Annotated[
    str,
    Header(
        alias="Idempotency-Key",
        min_length=16,
        max_length=128,
        pattern=r"^[A-Za-z0-9._:-]+$",
    ),
]


def _raise_service_error(exc: Exception) -> NoReturn:
    if isinstance(exc, PlatformAdminRequiredError):
        raise HTTPException(status_code=403, detail="platform_admin_required") from exc
    if isinstance(exc, SubscriptionNotFoundError):
        raise HTTPException(status_code=404, detail="subscription_not_found") from exc
    if isinstance(exc, IdempotencyConflictError):
        raise HTTPException(status_code=409, detail="idempotency_key_reused") from exc
    if isinstance(exc, IdempotencyInProgressError):
        raise HTTPException(status_code=409, detail="idempotency_request_in_progress") from exc
    if isinstance(exc, PaidCoverageRequiredError):
        raise HTTPException(status_code=422, detail="current_paid_coverage_required") from exc
    if isinstance(exc, BillingTransitionConflictError):
        raise HTTPException(status_code=409, detail="billing_mode_transition_conflict") from exc
    if isinstance(exc, SubscriptionStateConflictError):
        raise HTTPException(status_code=409, detail="subscription_state_conflict") from exc
    raise HTTPException(status_code=503, detail="subscription_service_unavailable") from exc


@router.post(
    "/subscriptions/cash-receipts",
    response_model=CashReceiptResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_cash_receipt(
    request: Request,
    payload: CashReceiptRequest,
    idempotency_key: IdempotencyKey,
    identity: Annotated[VerifiedIdentity, Depends(verified_identity)],
) -> CashReceiptResponse:
    await enforce_authenticated_rate_limit(request, identity.auth_user_id)
    try:
        return await record_cash_receipt(
            request.app.state.database_pool,
            actor_id=identity.auth_user_id,
            idempotency_key=idempotency_key,
            request_id=request.state.request_id,
            payload=payload,
        )
    except Exception as exc:
        _raise_service_error(exc)


@router.post(
    "/subscriptions/cash-receipts/{receipt_id}/reversal",
    response_model=CashReceiptResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_cash_receipt_reversal(
    request: Request,
    receipt_id: UUID,
    payload: ReceiptReversalRequest,
    idempotency_key: IdempotencyKey,
    identity: Annotated[VerifiedIdentity, Depends(verified_identity)],
) -> CashReceiptResponse:
    await enforce_authenticated_rate_limit(request, identity.auth_user_id)
    try:
        return await reverse_cash_receipt(
            request.app.state.database_pool,
            actor_id=identity.auth_user_id,
            receipt_id=receipt_id,
            idempotency_key=idempotency_key,
            request_id=request.state.request_id,
            payload=payload,
        )
    except Exception as exc:
        _raise_service_error(exc)


@router.post(
    "/subscriptions/{subscription_id}/suspend",
    response_model=SubscriptionStateResponse,
)
async def suspend(
    request: Request,
    subscription_id: UUID,
    payload: SuspendSubscriptionRequest,
    idempotency_key: IdempotencyKey,
    identity: Annotated[VerifiedIdentity, Depends(verified_identity)],
) -> SubscriptionStateResponse:
    await enforce_authenticated_rate_limit(request, identity.auth_user_id)
    try:
        return await suspend_subscription(
            request.app.state.database_pool,
            actor_id=identity.auth_user_id,
            subscription_id=subscription_id,
            idempotency_key=idempotency_key,
            request_id=request.state.request_id,
            payload=payload,
        )
    except Exception as exc:
        _raise_service_error(exc)


@router.post(
    "/subscriptions/{subscription_id}/resume",
    response_model=SubscriptionStateResponse,
)
async def resume(
    request: Request,
    subscription_id: UUID,
    payload: ResumeSubscriptionRequest,
    idempotency_key: IdempotencyKey,
    identity: Annotated[VerifiedIdentity, Depends(verified_identity)],
) -> SubscriptionStateResponse:
    await enforce_authenticated_rate_limit(request, identity.auth_user_id)
    try:
        return await resume_subscription(
            request.app.state.database_pool,
            actor_id=identity.auth_user_id,
            subscription_id=subscription_id,
            idempotency_key=idempotency_key,
            request_id=request.state.request_id,
            payload=payload,
        )
    except Exception as exc:
        _raise_service_error(exc)


@router.post(
    "/businesses/{business_id}/billing-mode",
    response_model=BillingModeTransitionResponse,
)
async def change_billing_mode(
    request: Request,
    business_id: UUID,
    payload: BillingModeTransitionRequest,
    idempotency_key: IdempotencyKey,
    identity: Annotated[VerifiedIdentity, Depends(verified_identity)],
) -> BillingModeTransitionResponse:
    await enforce_authenticated_rate_limit(request, identity.auth_user_id)
    try:
        return await transition_billing_mode(
            request.app.state.database_pool,
            actor_id=identity.auth_user_id,
            business_id=business_id,
            idempotency_key=idempotency_key,
            request_id=request.state.request_id,
            payload=payload,
        )
    except Exception as exc:
        _raise_service_error(exc)
