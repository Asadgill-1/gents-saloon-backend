from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI, HTTPException, Request

from app.api.rate_limit import (
    WINDOW_SECONDS,
    enforce_authenticated_rate_limit,
    enforce_public_rate_limit,
)
from app.core.config import Settings


class _Redis:
    def __init__(self, result: int | Exception) -> None:
        self.result = result
        self.calls: list[tuple[object, ...]] = []

    async def eval(self, *args: object) -> int:
        self.calls.append(args)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def _request(
    redis: _Redis,
    *,
    path: str = "/api/v1/platform/exports",
    route_path: str = "/api/v1/platform/exports",
    method: str = "POST",
) -> Request:
    app = FastAPI()
    app.state.redis = redis
    app.state.settings = Settings(_env_file=None)
    request = Request(
        {
            "type": "http",
            "app": app,
            "method": method,
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "headers": [],
            "scheme": "https",
            "server": ("api.example.test", 443),
            "client": ("203.0.113.10", 1234),
        }
    )
    request.scope["route"] = SimpleNamespace(path=route_path)
    return request


async def test_platform_mutation_rate_limit_uses_hashed_stable_route_and_actor() -> None:
    redis = _Redis(1)
    actor_id = uuid4()
    request = _request(
        redis,
        path=f"/api/v1/platform/exports/{uuid4()}/confirm-delivery",
        route_path="/api/v1/platform/exports/{export_id}/confirm-delivery",
    )

    await enforce_authenticated_rate_limit(request, actor_id)

    key = str(redis.calls[0][2])
    assert str(actor_id) not in key
    assert "export_id" not in key
    assert redis.calls[0][-1] == WINDOW_SECONDS


async def test_rate_limit_returns_retry_after_and_fails_closed() -> None:
    limited_request = _request(_Redis(61))
    with pytest.raises(HTTPException) as limited:
        await enforce_authenticated_rate_limit(limited_request, uuid4())
    assert limited.value.status_code == 429
    assert limited.value.headers == {"Retry-After": str(WINDOW_SECONDS)}

    unavailable_request = _request(_Redis(ConnectionError()))
    with pytest.raises(HTTPException) as unavailable:
        await enforce_authenticated_rate_limit(unavailable_request, uuid4())
    assert unavailable.value.status_code == 503
    assert unavailable.value.detail == "rate_limit_unavailable"


async def test_public_rate_limit_uses_server_observed_client_address() -> None:
    redis = _Redis(1)

    await enforce_public_rate_limit(
        _request(
            redis,
            path="/api/v1/public/shops/token/availability",
            route_path="/api/v1/public/shops/{public_queue_token}/availability",
            method="GET",
        )
    )

    key = str(redis.calls[0][2])
    assert "203.0.113.10" not in key
    assert ":public:" in key
