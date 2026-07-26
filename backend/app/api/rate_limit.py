import hashlib
from uuid import UUID

from fastapi import HTTPException, Request, status

WINDOW_SECONDS = 60
RATE_LIMIT_SCRIPT = """
local current = redis.call('INCR', KEYS[1])
if current == 1 then
  redis.call('EXPIRE', KEYS[1], ARGV[1])
end
return current
"""


def _route_bucket(request: Request) -> str:
    route = request.scope.get("route")
    route_path = str(getattr(route, "path", request.scope.get("path", "unknown")))
    method = str(request.scope.get("method", "GET"))
    return hashlib.sha256(f"{method}:{route_path}".encode()).hexdigest()[:16]


async def _enforce(
    request: Request,
    *,
    actor: str,
    category: str,
    limit: int,
) -> None:
    actor_hash = hashlib.sha256(actor.encode()).hexdigest()[:24]
    key = f"rate:v1:{category}:{_route_bucket(request)}:{actor_hash}"
    try:
        count = int(
            await request.app.state.redis.eval(
                RATE_LIMIT_SCRIPT,
                1,
                key,
                WINDOW_SECONDS,
            )
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="rate_limit_unavailable",
        ) from exc
    if count > limit:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="rate_limit_exceeded",
            headers={"Retry-After": str(WINDOW_SECONDS)},
        )


async def enforce_authenticated_rate_limit(
    request: Request,
    actor_id: UUID,
) -> None:
    path = str(request.scope.get("path", ""))
    method = str(request.scope.get("method", "GET"))
    privileged_mutation = path.startswith("/api/v1/platform") and (method != "GET")
    settings = request.app.state.settings
    await _enforce(
        request,
        actor=str(actor_id),
        category="platform" if privileged_mutation else "authenticated",
        limit=(
            settings.platform_mutation_rate_limit_per_minute
            if privileged_mutation
            else settings.authenticated_rate_limit_per_minute
        ),
    )


async def enforce_public_rate_limit(request: Request) -> None:
    client_host = request.client.host if request.client is not None else "unknown"
    await _enforce(
        request,
        actor=client_host,
        category="public",
        limit=request.app.state.settings.public_rate_limit_per_minute,
    )
