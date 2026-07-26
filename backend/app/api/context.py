from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel

from app.api.rate_limit import enforce_authenticated_rate_limit
from app.core.auth import AuthenticationError, VerifiedIdentity
from app.core.authorization import ActorContext, InactiveIdentityError, resolve_actor_context

router = APIRouter(prefix="/api/v1/me", tags=["identity"])


class ShopContextResponse(BaseModel):
    id: UUID
    name: str
    internal_code: str
    roles: list[str]


class BusinessContextResponse(BaseModel):
    id: UUID
    name: str
    is_owner: bool
    shops: list[ShopContextResponse]


class ActorContextResponse(BaseModel):
    auth_user_id: UUID
    display_name: str
    is_platform_admin: bool
    businesses: list[BusinessContextResponse]


def _unauthorized() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="invalid_authentication",
        headers={"WWW-Authenticate": "Bearer"},
    )


async def verified_identity(
    request: Request,
    authorization: str | None = Header(default=None),
) -> VerifiedIdentity:
    if authorization is None:
        raise _unauthorized()
    scheme, separator, token = authorization.partition(" ")
    if not separator or scheme.lower() != "bearer" or not token or " " in token:
        raise _unauthorized()

    try:
        return cast(VerifiedIdentity, await request.app.state.jwt_verifier.verify(token))
    except AuthenticationError as exc:
        raise _unauthorized() from exc


def context_response(context: ActorContext) -> ActorContextResponse:
    businesses: dict[UUID, dict[str, object]] = {}
    shops: dict[tuple[UUID, UUID], dict[str, object]] = {}

    for access in context.shop_access:
        business = businesses.setdefault(
            access.business_id,
            {
                "id": access.business_id,
                "name": access.business_name,
                "is_owner": False,
                "shops": [],
            },
        )
        business["is_owner"] = bool(business["is_owner"]) or access.is_owner

        shop_key = (access.business_id, access.shop_id)
        shop = shops.get(shop_key)
        if shop is None:
            shop = {
                "id": access.shop_id,
                "name": access.shop_name,
                "internal_code": access.internal_code,
                "roles": set(),
            }
            shops[shop_key] = shop
            shop_list = business["shops"]
            assert isinstance(shop_list, list)
            shop_list.append(shop)
        roles = shop["roles"]
        assert isinstance(roles, set)
        roles.add(access.role)

    normalized = []
    for business in businesses.values():
        business_shops = business["shops"]
        assert isinstance(business_shops, list)
        normalized_shops = [
            {
                **shop,
                "roles": sorted(shop["roles"]),
            }
            for shop in business_shops
        ]
        normalized_shops.sort(key=lambda shop: (str(shop["name"]), str(shop["id"])))
        normalized.append({**business, "shops": normalized_shops})
    normalized.sort(key=lambda business: (str(business["name"]), str(business["id"])))

    return ActorContextResponse.model_validate(
        {
            "auth_user_id": context.auth_user_id,
            "display_name": context.display_name,
            "is_platform_admin": context.is_platform_admin,
            "businesses": normalized,
        }
    )


@router.get("/context", response_model=ActorContextResponse)
async def get_context(
    request: Request,
    identity: Annotated[VerifiedIdentity, Depends(verified_identity)],
) -> ActorContextResponse:
    await enforce_authenticated_rate_limit(request, identity.auth_user_id)
    try:
        context = await resolve_actor_context(
            request.app.state.database_pool,
            identity.auth_user_id,
        )
    except InactiveIdentityError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="identity_inactive",
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="authorization_unavailable",
        ) from exc
    return context_response(context)
