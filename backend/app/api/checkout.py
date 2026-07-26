from typing import Annotated, NoReturn

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status

from app.api.tenant import TenantRequestContext, rate_limited_tenant_request_context
from app.core.entitlements import SubscriptionSuspendedError
from app.services.checkout_service import (
    CheckoutAccessDeniedError,
    CheckoutConflictError,
    CheckoutInputError,
    CheckoutNotFoundError,
    CheckoutRequest,
    CheckoutResponse,
    checkout,
)
from app.services.platform_operations import (
    IdempotencyConflictError,
    IdempotencyInProgressError,
)

router = APIRouter(
    prefix="/api/v1/businesses/{business_id}/shops/{shop_id}/pos",
    tags=["pos"],
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
    if isinstance(exc, CheckoutAccessDeniedError):
        raise HTTPException(status_code=403, detail="checkout_access_denied") from exc
    if isinstance(exc, CheckoutNotFoundError):
        raise HTTPException(status_code=404, detail="checkout_source_not_found") from exc
    if isinstance(exc, CheckoutInputError):
        raise HTTPException(status_code=422, detail="checkout_input_invalid") from exc
    if isinstance(exc, IdempotencyConflictError):
        raise HTTPException(status_code=409, detail="idempotency_key_reused") from exc
    if isinstance(exc, IdempotencyInProgressError):
        raise HTTPException(status_code=409, detail="idempotency_request_in_progress") from exc
    if isinstance(exc, CheckoutConflictError):
        raise HTTPException(status_code=409, detail="checkout_conflict") from exc
    if isinstance(exc, SubscriptionSuspendedError):
        raise exc
    raise HTTPException(status_code=503, detail="checkout_service_unavailable") from exc


@router.post(
    "/checkout",
    response_model=CheckoutResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_checkout(
    request: Request,
    payload: CheckoutRequest,
    idempotency_key: IdempotencyKey,
    tenant: TenantContext,
) -> CheckoutResponse:
    try:
        return await checkout(
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
