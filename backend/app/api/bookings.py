from typing import Annotated, NoReturn
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status

from app.api.tenant import TenantRequestContext, rate_limited_tenant_request_context
from app.core.entitlements import SubscriptionSuspendedError
from app.services.booking_service import (
    BookingAccessDeniedError,
    BookingConflictError,
    BookingCreateRequest,
    BookingInputError,
    BookingNotFoundError,
    BookingRescheduleRequest,
    BookingResponse,
    BookingTransitionRequest,
    IdempotencyConflictError,
    IdempotencyInProgressError,
    create_booking,
    reschedule_booking,
    transition_booking,
)

router = APIRouter(
    prefix="/api/v1/businesses/{business_id}/shops/{shop_id}/bookings",
    tags=["bookings"],
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
    if isinstance(exc, BookingAccessDeniedError):
        raise HTTPException(status_code=403, detail="booking_access_denied") from exc
    if isinstance(exc, BookingNotFoundError):
        raise HTTPException(status_code=404, detail="booking_not_found") from exc
    if isinstance(exc, IdempotencyConflictError):
        raise HTTPException(status_code=409, detail="idempotency_key_reused") from exc
    if isinstance(exc, IdempotencyInProgressError):
        raise HTTPException(status_code=409, detail="idempotency_request_in_progress") from exc
    if isinstance(exc, BookingConflictError):
        raise HTTPException(status_code=409, detail="booking_conflict") from exc
    if isinstance(exc, BookingInputError):
        raise HTTPException(status_code=422, detail="invalid_booking") from exc
    if isinstance(exc, SubscriptionSuspendedError):
        raise exc
    raise HTTPException(status_code=503, detail="booking_service_unavailable") from exc


@router.post("", response_model=BookingResponse, status_code=status.HTTP_201_CREATED)
async def create(
    request: Request,
    payload: BookingCreateRequest,
    idempotency_key: IdempotencyKey,
    tenant: TenantContext,
) -> BookingResponse:
    try:
        return await create_booking(
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


async def _transition(
    request: Request,
    booking_id: UUID,
    payload: BookingTransitionRequest,
    idempotency_key: str,
    tenant: TenantRequestContext,
    target_status: str,
) -> BookingResponse:
    try:
        return await transition_booking(
            request.app.state.database_pool,
            actor_id=tenant.auth_user_id,
            business_id=tenant.business_id,
            shop_id=tenant.shop_id,
            booking_id=booking_id,
            target_status=target_status,  # type: ignore[arg-type]
            idempotency_key=idempotency_key,
            request_id=request.state.request_id,
            payload=payload,
        )
    except Exception as exc:
        _raise_service_error(exc)


@router.post("/{booking_id}/confirm", response_model=BookingResponse)
async def confirm(
    request: Request,
    booking_id: UUID,
    payload: BookingTransitionRequest,
    idempotency_key: IdempotencyKey,
    tenant: TenantContext,
) -> BookingResponse:
    return await _transition(request, booking_id, payload, idempotency_key, tenant, "confirmed")


@router.post(
    "/{booking_id}/reschedule",
    response_model=BookingResponse,
    status_code=status.HTTP_201_CREATED,
)
async def reschedule(
    request: Request,
    booking_id: UUID,
    payload: BookingRescheduleRequest,
    idempotency_key: IdempotencyKey,
    tenant: TenantContext,
) -> BookingResponse:
    try:
        return await reschedule_booking(
            request.app.state.database_pool,
            actor_id=tenant.auth_user_id,
            business_id=tenant.business_id,
            shop_id=tenant.shop_id,
            booking_id=booking_id,
            idempotency_key=idempotency_key,
            request_id=request.state.request_id,
            payload=payload,
        )
    except Exception as exc:
        _raise_service_error(exc)


@router.post("/{booking_id}/start", response_model=BookingResponse)
async def start(
    request: Request,
    booking_id: UUID,
    payload: BookingTransitionRequest,
    idempotency_key: IdempotencyKey,
    tenant: TenantContext,
) -> BookingResponse:
    return await _transition(request, booking_id, payload, idempotency_key, tenant, "in_service")


@router.post("/{booking_id}/complete", response_model=BookingResponse)
async def complete(
    request: Request,
    booking_id: UUID,
    payload: BookingTransitionRequest,
    idempotency_key: IdempotencyKey,
    tenant: TenantContext,
) -> BookingResponse:
    return await _transition(request, booking_id, payload, idempotency_key, tenant, "completed")


@router.post("/{booking_id}/cancel", response_model=BookingResponse)
async def cancel(
    request: Request,
    booking_id: UUID,
    payload: BookingTransitionRequest,
    idempotency_key: IdempotencyKey,
    tenant: TenantContext,
) -> BookingResponse:
    return await _transition(request, booking_id, payload, idempotency_key, tenant, "cancelled")


@router.post("/{booking_id}/no-show", response_model=BookingResponse)
async def no_show(
    request: Request,
    booking_id: UUID,
    payload: BookingTransitionRequest,
    idempotency_key: IdempotencyKey,
    tenant: TenantContext,
) -> BookingResponse:
    return await _transition(request, booking_id, payload, idempotency_key, tenant, "no_show")
