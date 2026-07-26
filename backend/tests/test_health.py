from fastapi import Request, Response, status

from app.api.health import liveness, readiness
from app.core.config import Settings
from app.main import create_app


async def test_liveness_does_not_require_dependencies() -> None:
    app = create_app(Settings(_env_file=None))

    assert "/health/live" in app.openapi()["paths"]
    assert await liveness() == {"status": "ok"}


class _FailingConnection:
    async def __aenter__(self) -> None:
        raise ConnectionError

    async def __aexit__(self, *args: object) -> None:
        return None


class _FailingPool:
    def connection(self, timeout: int) -> _FailingConnection:
        return _FailingConnection()


class _FailingRedis:
    async def ping(self) -> bool:
        return False


async def test_readiness_fails_closed_without_dependency_details() -> None:
    app = create_app(Settings(_env_file=None))
    app.state.database_pool = _FailingPool()
    app.state.redis = _FailingRedis()
    request = Request({"type": "http", "app": app})
    response = Response()

    result = await readiness(request, response)

    assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    assert result == {"status": "unavailable"}
