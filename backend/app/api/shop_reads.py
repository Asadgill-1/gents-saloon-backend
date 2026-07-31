from datetime import date
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.api.tenant import TenantRequestContext, rate_limited_tenant_request_context
from app.services.shop_read_service import (
    BarberListResponse,
    BookingListResponse,
    CashShiftListResponse,
    ServiceListResponse,
    list_barbers,
    list_bookings,
    list_cash_shifts,
    list_services,
)

router = APIRouter(
    prefix="/api/v1/businesses/{business_id}/shops/{shop_id}",
    tags=["shop-reads"],
)

TenantContext = Annotated[
    TenantRequestContext,
    Depends(rate_limited_tenant_request_context),
]
PageCursor = Annotated[UUID | None, Query()]
PageLimit = Annotated[int, Query(ge=1, le=100)]


@router.get("/bookings", response_model=BookingListResponse)
async def bookings(
    request: Request,
    tenant: TenantContext,
    cursor: PageCursor = None,
    limit: PageLimit = 50,
    status: Annotated[
        Literal[
            "held",
            "requested",
            "confirmed",
            "in_service",
            "completed",
            "cancelled",
            "no_show",
        ]
        | None,
        Query(),
    ] = None,
    date_from: Annotated[date | None, Query()] = None,
    date_to: Annotated[date | None, Query()] = None,
) -> BookingListResponse:
    if date_from is not None and date_to is not None and date_to < date_from:
        raise HTTPException(status_code=422, detail="booking_date_range_invalid")
    return await list_bookings(
        request.app.state.database_pool,
        business_id=tenant.business_id,
        shop_id=tenant.shop_id,
        cursor=cursor,
        limit=limit,
        status=status,
        date_from=date_from,
        date_to=date_to,
    )


@router.get("/services", response_model=ServiceListResponse)
async def services(
    request: Request,
    tenant: TenantContext,
    cursor: PageCursor = None,
    limit: PageLimit = 50,
) -> ServiceListResponse:
    return await list_services(
        request.app.state.database_pool,
        business_id=tenant.business_id,
        shop_id=tenant.shop_id,
        cursor=cursor,
        limit=limit,
    )


@router.get("/barbers", response_model=BarberListResponse)
async def barbers(
    request: Request,
    tenant: TenantContext,
    cursor: PageCursor = None,
    limit: PageLimit = 50,
) -> BarberListResponse:
    return await list_barbers(
        request.app.state.database_pool,
        business_id=tenant.business_id,
        shop_id=tenant.shop_id,
        cursor=cursor,
        limit=limit,
    )


@router.get("/cash-shifts", response_model=CashShiftListResponse)
async def cash_shifts(
    request: Request,
    tenant: TenantContext,
    cursor: PageCursor = None,
    limit: PageLimit = 50,
    state: Annotated[Literal["open", "closed"] | None, Query()] = None,
    register: Annotated[str | None, Query(min_length=1, max_length=64)] = None,
) -> CashShiftListResponse:
    return await list_cash_shifts(
        request.app.state.database_pool,
        business_id=tenant.business_id,
        shop_id=tenant.shop_id,
        cursor=cursor,
        limit=limit,
        state=state,
        register=register,
    )
