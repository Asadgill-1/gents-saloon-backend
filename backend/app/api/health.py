import asyncio
from typing import Any

from fastapi import APIRouter, Request, Response, status

router = APIRouter(prefix="/health", tags=["health"])


@router.get("/live")
async def liveness() -> dict[str, str]:
    return {"status": "ok"}


async def _database_ready(pool: Any) -> None:
    async with pool.connection(timeout=2) as connection:
        await connection.execute("SELECT 1")


async def _redis_ready(client: Any) -> None:
    if not await client.ping():
        raise RuntimeError("Redis ping failed")


@router.get("/ready")
async def readiness(request: Request, response: Response) -> dict[str, str]:
    checks = await asyncio.gather(
        _database_ready(request.app.state.database_pool),
        _redis_ready(request.app.state.redis),
        return_exceptions=True,
    )
    if any(isinstance(result, BaseException) for result in checks):
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "unavailable"}
    return {"status": "ok"}
