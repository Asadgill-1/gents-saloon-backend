from typing import Annotated, NoReturn
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status

from app.api.tenant import TenantRequestContext, rate_limited_tenant_request_context
from app.core.entitlements import SubscriptionSuspendedError
from app.services.legal_cash_service import (
    CashAccessDeniedError,
    CashMovementRequest,
    CashMovementResponse,
    CashShiftCloseRequest,
    CashShiftConflictError,
    CashShiftNotFoundError,
    CashShiftOpenRequest,
    CashShiftResponse,
    IdempotencyConflictError,
    IdempotencyInProgressError,
    LegalDocumentProfile,
    LegalProfileNotFoundError,
    close_cash_shift,
    get_cash_shift,
    get_current_legal_document_profile,
    open_cash_shift,
    record_manual_cash_movement,
)

router = APIRouter(
    prefix="/api/v1/businesses/{business_id}/shops/{shop_id}",
    tags=["legal-and-cash"],
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
    if isinstance(exc, CashAccessDeniedError):
        raise HTTPException(status_code=403, detail="cash_access_denied") from exc
    if isinstance(exc, (CashShiftNotFoundError, LegalProfileNotFoundError)):
        raise HTTPException(status_code=404, detail="financial_resource_not_found") from exc
    if isinstance(exc, IdempotencyConflictError):
        raise HTTPException(status_code=409, detail="idempotency_key_reused") from exc
    if isinstance(exc, IdempotencyInProgressError):
        raise HTTPException(status_code=409, detail="idempotency_request_in_progress") from exc
    if isinstance(exc, CashShiftConflictError):
        raise HTTPException(status_code=409, detail="cash_shift_conflict") from exc
    if isinstance(exc, SubscriptionSuspendedError):
        raise exc
    raise HTTPException(status_code=503, detail="financial_service_unavailable") from exc


@router.get("/legal-document-profile", response_model=LegalDocumentProfile)
async def legal_document_profile(
    request: Request,
    tenant: TenantContext,
) -> LegalDocumentProfile:
    try:
        return await get_current_legal_document_profile(
            request.app.state.database_pool,
            business_id=tenant.business_id,
            shop_id=tenant.shop_id,
        )
    except Exception as exc:
        _raise_service_error(exc)


@router.post(
    "/cash-shifts/open",
    response_model=CashShiftResponse,
    status_code=status.HTTP_201_CREATED,
)
async def open_shift(
    request: Request,
    payload: CashShiftOpenRequest,
    idempotency_key: IdempotencyKey,
    tenant: TenantContext,
) -> CashShiftResponse:
    try:
        return await open_cash_shift(
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


@router.get("/cash-shifts/{cash_shift_id}", response_model=CashShiftResponse)
async def shift(
    request: Request,
    cash_shift_id: UUID,
    tenant: TenantContext,
) -> CashShiftResponse:
    try:
        return await get_cash_shift(
            request.app.state.database_pool,
            business_id=tenant.business_id,
            shop_id=tenant.shop_id,
            cash_shift_id=cash_shift_id,
        )
    except Exception as exc:
        _raise_service_error(exc)


@router.post(
    "/cash-shifts/{cash_shift_id}/movements",
    response_model=CashMovementResponse,
    status_code=status.HTTP_201_CREATED,
)
async def movement(
    request: Request,
    cash_shift_id: UUID,
    payload: CashMovementRequest,
    idempotency_key: IdempotencyKey,
    tenant: TenantContext,
) -> CashMovementResponse:
    try:
        return await record_manual_cash_movement(
            request.app.state.database_pool,
            actor_id=tenant.auth_user_id,
            business_id=tenant.business_id,
            shop_id=tenant.shop_id,
            cash_shift_id=cash_shift_id,
            idempotency_key=idempotency_key,
            request_id=request.state.request_id,
            payload=payload,
        )
    except Exception as exc:
        _raise_service_error(exc)


@router.post("/cash-shifts/{cash_shift_id}/close", response_model=CashShiftResponse)
async def close_shift(
    request: Request,
    cash_shift_id: UUID,
    payload: CashShiftCloseRequest,
    idempotency_key: IdempotencyKey,
    tenant: TenantContext,
) -> CashShiftResponse:
    try:
        return await close_cash_shift(
            request.app.state.database_pool,
            actor_id=tenant.auth_user_id,
            business_id=tenant.business_id,
            shop_id=tenant.shop_id,
            cash_shift_id=cash_shift_id,
            idempotency_key=idempotency_key,
            request_id=request.state.request_id,
            payload=payload,
        )
    except Exception as exc:
        _raise_service_error(exc)
