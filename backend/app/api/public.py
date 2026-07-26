import hashlib
import re
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel

from app.api.rate_limit import enforce_public_rate_limit
from app.core.entitlements import resolve_entitlement

router = APIRouter(prefix="/api/v1/public", tags=["public"])

PUBLIC_QUEUE_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]{20,128}$")


class PublicAvailabilityResponse(BaseModel):
    status: Literal["available", "unavailable"]


async def resolve_public_availability(
    pool: Any,
    public_queue_token: str,
) -> PublicAvailabilityResponse:
    if PUBLIC_QUEUE_TOKEN_PATTERN.fullmatch(public_queue_token) is None:
        return PublicAvailabilityResponse(status="unavailable")

    token_hash = hashlib.sha256(public_queue_token.encode()).hexdigest()
    async with pool.connection(timeout=5) as connection:
        cursor = await connection.execute(
            """
            select business_id, id
            from public.shops
            where public_queue_token_hash = %s
            """,
            (token_hash,),
        )
        shop = await cursor.fetchone()
        if shop is None:
            return PublicAvailabilityResponse(status="unavailable")

        entitlement = await resolve_entitlement(
            connection,
            business_id=shop[0],
            shop_id=shop[1],
        )
        return PublicAvailabilityResponse(
            status="available" if entitlement.active else "unavailable"
        )


@router.get("/shops/{public_queue_token}/availability", response_model=PublicAvailabilityResponse)
async def get_public_availability(
    request: Request,
    public_queue_token: str,
) -> PublicAvailabilityResponse:
    await enforce_public_rate_limit(request)
    try:
        return await resolve_public_availability(
            request.app.state.database_pool,
            public_queue_token,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="public_availability_unavailable",
        ) from exc
