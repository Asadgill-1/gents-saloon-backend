from datetime import datetime
from typing import Annotated, NoReturn
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.api.context import verified_identity
from app.api.rate_limit import enforce_authenticated_rate_limit
from app.api.tenant import TenantRequestContext, rate_limited_tenant_request_context
from app.core.auth import VerifiedIdentity
from app.core.entitlements import SubscriptionSuspendedError
from app.services.report_service import (
    BusinessOverviewResponse,
    ReportAccessDeniedError,
    ReportInputError,
    ShopReportResponse,
    get_business_overview,
    get_shop_report,
)

router = APIRouter(tags=["reports"])


def _raise_report_error(exc: Exception) -> NoReturn:
    if isinstance(exc, ReportAccessDeniedError):
        raise HTTPException(status_code=403, detail="report_access_denied") from exc
    if isinstance(exc, ReportInputError):
        raise HTTPException(status_code=422, detail="report_input_invalid") from exc
    if isinstance(exc, SubscriptionSuspendedError):
        raise exc
    raise HTTPException(status_code=503, detail="report_service_unavailable") from exc


@router.get(
    "/api/v1/businesses/{business_id}/shops/{shop_id}/reports",
    response_model=ShopReportResponse,
)
async def shop_report(
    request: Request,
    period_start: Annotated[datetime, Query()],
    period_end: Annotated[datetime, Query()],
    tenant: Annotated[
        TenantRequestContext,
        Depends(rate_limited_tenant_request_context),
    ],
    cursor: Annotated[UUID | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> ShopReportResponse:
    try:
        return await get_shop_report(
            request.app.state.database_pool,
            actor_id=tenant.auth_user_id,
            business_id=tenant.business_id,
            shop_id=tenant.shop_id,
            period_start=period_start,
            period_end=period_end,
            cursor=cursor,
            limit=limit,
        )
    except Exception as exc:
        _raise_report_error(exc)


@router.get(
    "/api/v1/businesses/{business_id}/overview",
    response_model=BusinessOverviewResponse,
)
async def business_overview(
    request: Request,
    business_id: UUID,
    period_start: Annotated[datetime, Query()],
    period_end: Annotated[datetime, Query()],
    identity: Annotated[VerifiedIdentity, Depends(verified_identity)],
    cursor: Annotated[UUID | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> BusinessOverviewResponse:
    await enforce_authenticated_rate_limit(request, identity.auth_user_id)
    try:
        return await get_business_overview(
            request.app.state.database_pool,
            actor_id=identity.auth_user_id,
            business_id=business_id,
            period_start=period_start,
            period_end=period_end,
            cursor=cursor,
            limit=limit,
        )
    except Exception as exc:
        _raise_report_error(exc)
