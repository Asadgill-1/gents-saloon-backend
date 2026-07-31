from collections.abc import Awaitable, Callable
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.api.context import verified_identity
from app.api.rate_limit import enforce_authenticated_rate_limit
from app.core.auth import VerifiedIdentity
from app.services.platform_operations import PlatformAdminRequiredError
from app.services.platform_read_service import (
    AnalyticsListResponse,
    BotHealthListResponse,
    CashReceiptListResponse,
    OffboardingListResponse,
    SubscriptionListResponse,
    TenantListResponse,
    list_analytics,
    list_bot_health,
    list_cash_receipts,
    list_offboarding_cases,
    list_subscriptions,
    list_tenants,
)

router = APIRouter(prefix="/api/v1/platform", tags=["platform-reads"])

PageCursor = Annotated[UUID | None, Query()]
PageLimit = Annotated[int, Query(ge=1, le=100)]


async def _read[ResponseT](
    request: Request,
    identity: VerifiedIdentity,
    reader: Callable[..., Awaitable[ResponseT]],
    *,
    cursor: UUID | None,
    limit: int,
) -> ResponseT:
    await enforce_authenticated_rate_limit(request, identity.auth_user_id)
    try:
        return await reader(
            request.app.state.database_pool,
            actor_id=identity.auth_user_id,
            cursor=cursor,
            limit=limit,
        )
    except PlatformAdminRequiredError as exc:
        raise HTTPException(status_code=403, detail="platform_admin_required") from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail="platform_read_unavailable") from exc


@router.get("/tenants", response_model=TenantListResponse)
async def tenants(
    request: Request,
    identity: Annotated[VerifiedIdentity, Depends(verified_identity)],
    cursor: PageCursor = None,
    limit: PageLimit = 50,
) -> TenantListResponse:
    return await _read(request, identity, list_tenants, cursor=cursor, limit=limit)


@router.get("/subscriptions", response_model=SubscriptionListResponse)
async def subscriptions(
    request: Request,
    identity: Annotated[VerifiedIdentity, Depends(verified_identity)],
    cursor: PageCursor = None,
    limit: PageLimit = 50,
) -> SubscriptionListResponse:
    return await _read(request, identity, list_subscriptions, cursor=cursor, limit=limit)


@router.get("/subscriptions/cash-receipts", response_model=CashReceiptListResponse)
async def cash_receipts(
    request: Request,
    identity: Annotated[VerifiedIdentity, Depends(verified_identity)],
    cursor: PageCursor = None,
    limit: PageLimit = 50,
) -> CashReceiptListResponse:
    return await _read(request, identity, list_cash_receipts, cursor=cursor, limit=limit)


@router.get("/offboarding-cases", response_model=OffboardingListResponse)
async def offboarding_cases(
    request: Request,
    identity: Annotated[VerifiedIdentity, Depends(verified_identity)],
    cursor: PageCursor = None,
    limit: PageLimit = 50,
) -> OffboardingListResponse:
    return await _read(request, identity, list_offboarding_cases, cursor=cursor, limit=limit)


@router.get("/bots/health", response_model=BotHealthListResponse)
async def bot_health(
    request: Request,
    identity: Annotated[VerifiedIdentity, Depends(verified_identity)],
    cursor: PageCursor = None,
    limit: PageLimit = 50,
) -> BotHealthListResponse:
    return await _read(request, identity, list_bot_health, cursor=cursor, limit=limit)


@router.get("/analytics", response_model=AnalyticsListResponse)
async def analytics(
    request: Request,
    identity: Annotated[VerifiedIdentity, Depends(verified_identity)],
    cursor: PageCursor = None,
    limit: PageLimit = 50,
) -> AnalyticsListResponse:
    return await _read(request, identity, list_analytics, cursor=cursor, limit=limit)
