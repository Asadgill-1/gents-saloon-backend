from typing import Annotated, NoReturn
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status

from app.api.tenant import TenantRequestContext, rate_limited_tenant_request_context
from app.core.entitlements import SubscriptionSuspendedError
from app.services.payout_service import (
    AdvanceRequest,
    AdvanceResponse,
    FinanceAccessDeniedError,
    FinanceConflictError,
    FinanceInputError,
    FinanceNotFoundError,
    PayoutActionRequest,
    PayoutPayRequest,
    PayoutRunRequest,
    PayoutRunResponse,
    approve_payout_run,
    cancel_payout_run,
    create_payout_run,
    grant_advance,
    pay_payout_run,
)
from app.services.platform_operations import (
    IdempotencyConflictError,
    IdempotencyInProgressError,
)

router = APIRouter(
    prefix="/api/v1/businesses/{business_id}/shops/{shop_id}",
    tags=["payouts"],
)

IdempotencyKey = Annotated[
    str,
    Header(
        alias="Idempotency-Key",
        min_length=16,
        max_length=128,
        pattern=r"^[A-Za-z0-9._:-]+$",
    ),
]
TenantContext = Annotated[
    TenantRequestContext,
    Depends(rate_limited_tenant_request_context),
]


def _raise_service_error(exc: Exception) -> NoReturn:
    if isinstance(exc, FinanceAccessDeniedError):
        raise HTTPException(status_code=403, detail="finance_access_denied") from exc
    if isinstance(exc, FinanceNotFoundError):
        raise HTTPException(status_code=404, detail="finance_record_not_found") from exc
    if isinstance(exc, FinanceInputError):
        raise HTTPException(status_code=422, detail="finance_input_invalid") from exc
    if isinstance(exc, IdempotencyConflictError):
        raise HTTPException(status_code=409, detail="idempotency_key_reused") from exc
    if isinstance(exc, IdempotencyInProgressError):
        raise HTTPException(status_code=409, detail="idempotency_request_in_progress") from exc
    if isinstance(exc, FinanceConflictError):
        raise HTTPException(status_code=409, detail="finance_conflict") from exc
    if isinstance(exc, SubscriptionSuspendedError):
        raise exc
    raise HTTPException(status_code=503, detail="finance_service_unavailable") from exc


@router.post(
    "/advances",
    response_model=AdvanceResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_advance(
    request: Request,
    payload: AdvanceRequest,
    idempotency_key: IdempotencyKey,
    tenant: TenantContext,
) -> AdvanceResponse:
    try:
        return await grant_advance(
            request.app.state.database_pool,
            actor_id=tenant.auth_user_id,
            business_id=tenant.business_id,
            shop_id=tenant.shop_id,
            idempotency_key=idempotency_key,
            request_id=request.state.request_id,
            payload=payload,
        )
    except Exception as exc:
        _raise_service_error(exc)


@router.post(
    "/payout-runs",
    response_model=PayoutRunResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_run(
    request: Request,
    payload: PayoutRunRequest,
    idempotency_key: IdempotencyKey,
    tenant: TenantContext,
) -> PayoutRunResponse:
    try:
        return await create_payout_run(
            request.app.state.database_pool,
            actor_id=tenant.auth_user_id,
            business_id=tenant.business_id,
            shop_id=tenant.shop_id,
            idempotency_key=idempotency_key,
            request_id=request.state.request_id,
            payload=payload,
        )
    except Exception as exc:
        _raise_service_error(exc)


@router.post(
    "/payout-runs/{payout_run_id}/approve",
    response_model=PayoutRunResponse,
)
async def approve_run(
    payout_run_id: UUID,
    request: Request,
    payload: PayoutActionRequest,
    idempotency_key: IdempotencyKey,
    tenant: TenantContext,
) -> PayoutRunResponse:
    try:
        return await approve_payout_run(
            request.app.state.database_pool,
            actor_id=tenant.auth_user_id,
            business_id=tenant.business_id,
            shop_id=tenant.shop_id,
            payout_run_id=payout_run_id,
            idempotency_key=idempotency_key,
            request_id=request.state.request_id,
            payload=payload,
        )
    except Exception as exc:
        _raise_service_error(exc)


@router.post(
    "/payout-runs/{payout_run_id}/pay",
    response_model=PayoutRunResponse,
)
async def pay_run(
    payout_run_id: UUID,
    request: Request,
    payload: PayoutPayRequest,
    idempotency_key: IdempotencyKey,
    tenant: TenantContext,
) -> PayoutRunResponse:
    try:
        return await pay_payout_run(
            request.app.state.database_pool,
            actor_id=tenant.auth_user_id,
            business_id=tenant.business_id,
            shop_id=tenant.shop_id,
            payout_run_id=payout_run_id,
            idempotency_key=idempotency_key,
            request_id=request.state.request_id,
            payload=payload,
        )
    except Exception as exc:
        _raise_service_error(exc)


@router.post(
    "/payout-runs/{payout_run_id}/cancel",
    response_model=PayoutRunResponse,
)
async def cancel_run(
    payout_run_id: UUID,
    request: Request,
    payload: PayoutActionRequest,
    idempotency_key: IdempotencyKey,
    tenant: TenantContext,
) -> PayoutRunResponse:
    try:
        return await cancel_payout_run(
            request.app.state.database_pool,
            actor_id=tenant.auth_user_id,
            business_id=tenant.business_id,
            shop_id=tenant.shop_id,
            payout_run_id=payout_run_id,
            idempotency_key=idempotency_key,
            request_id=request.state.request_id,
            payload=payload,
        )
    except Exception as exc:
        _raise_service_error(exc)
