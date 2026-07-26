from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

from app.api.context import verified_identity
from app.api.rate_limit import enforce_authenticated_rate_limit
from app.core.auth import VerifiedIdentity
from app.core.authorization import InactiveIdentityError, resolve_actor_context
from app.core.entitlements import SubscriptionSuspendedError, resolve_entitlement

router = APIRouter(
    prefix="/api/v1/businesses/{business_id}/shops/{shop_id}",
    tags=["tenant"],
)


@dataclass(frozen=True)
class TenantRequestContext:
    auth_user_id: UUID
    business_id: UUID
    business_name: str | None
    shop_id: UUID
    shop_name: str | None
    roles: tuple[str, ...]
    is_owner: bool
    is_platform_admin: bool


class ShopSessionResponse(BaseModel):
    business_id: UUID
    business_name: str | None
    shop_id: UUID
    shop_name: str | None
    roles: list[str]
    is_owner: bool
    is_platform_admin: bool


async def tenant_request_context(
    request: Request,
    business_id: UUID,
    shop_id: UUID,
    identity: Annotated[VerifiedIdentity, Depends(verified_identity)],
) -> TenantRequestContext:
    try:
        actor = await resolve_actor_context(
            request.app.state.database_pool,
            identity.auth_user_id,
        )
    except InactiveIdentityError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="tenant_access_denied",
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="authorization_unavailable",
        ) from exc

    matches = [
        access
        for access in actor.shop_access
        if access.business_id == business_id and access.shop_id == shop_id
    ]
    if not actor.is_platform_admin and not matches:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="tenant_access_denied",
        )

    if not actor.is_platform_admin:
        try:
            async with request.app.state.database_pool.connection(timeout=5) as connection:
                entitlement = await resolve_entitlement(
                    connection,
                    business_id=business_id,
                    shop_id=shop_id,
                )
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="entitlement_unavailable",
            ) from exc
        if not entitlement.active:
            raise SubscriptionSuspendedError

    first = matches[0] if matches else None
    return TenantRequestContext(
        auth_user_id=identity.auth_user_id,
        business_id=business_id,
        business_name=first.business_name if first else None,
        shop_id=shop_id,
        shop_name=first.shop_name if first else None,
        roles=(
            tuple(sorted({access.role for access in matches})) if matches else ("platform_admin",)
        ),
        is_owner=any(access.is_owner for access in matches),
        is_platform_admin=actor.is_platform_admin,
    )


async def rate_limited_tenant_request_context(
    request: Request,
    business_id: UUID,
    shop_id: UUID,
    identity: Annotated[VerifiedIdentity, Depends(verified_identity)],
) -> TenantRequestContext:
    await enforce_authenticated_rate_limit(request, identity.auth_user_id)
    return await tenant_request_context(
        request,
        business_id,
        shop_id,
        identity,
    )


@router.get("/session", response_model=ShopSessionResponse)
async def get_shop_session(
    tenant: Annotated[
        TenantRequestContext,
        Depends(rate_limited_tenant_request_context),
    ],
) -> ShopSessionResponse:
    return ShopSessionResponse(
        business_id=tenant.business_id,
        business_name=tenant.business_name,
        shop_id=tenant.shop_id,
        shop_name=tenant.shop_name,
        roles=list(tenant.roles),
        is_owner=tenant.is_owner,
        is_platform_admin=tenant.is_platform_admin,
    )
