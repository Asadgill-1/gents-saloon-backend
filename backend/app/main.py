from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import RequestResponseEndpoint
from starlette.middleware.cors import CORSMiddleware
from starlette.responses import Response

from app.api.bookings import router as bookings_router
from app.api.checkout import router as checkout_router
from app.api.context import router as context_router
from app.api.corrections import router as corrections_router
from app.api.health import router as health_router
from app.api.legal_cash import router as legal_cash_router
from app.api.payouts import router as payouts_router
from app.api.platform.exports import router as platform_exports_router
from app.api.platform.subscriptions import router as platform_subscriptions_router
from app.api.platform.tenants import router as platform_tenants_router
from app.api.public import router as public_router
from app.api.reports import router as reports_router
from app.api.tenant import router as tenant_router
from app.core.auth import JwtVerifier
from app.core.config import Settings, get_settings
from app.core.database import create_database_pool
from app.core.entitlements import SubscriptionSuspendedError
from app.core.logging import configure_logging
from app.core.redis import create_redis_client


def create_app(settings: Settings | None = None) -> FastAPI:
    runtime_settings = settings or get_settings()
    configure_logging(runtime_settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.database_pool = create_database_pool(runtime_settings)
        app.state.redis = create_redis_client(runtime_settings)
        await app.state.database_pool.open(wait=False)
        try:
            yield
        finally:
            await app.state.redis.aclose()
            await app.state.database_pool.close()

    app = FastAPI(
        title="Gents Saloon API",
        version="0.1.0",
        docs_url=None if runtime_settings.env == "production" else "/docs",
        redoc_url=None,
        openapi_url=None if runtime_settings.env == "production" else "/openapi.json",
        lifespan=lifespan,
    )
    app.state.jwt_verifier = JwtVerifier(runtime_settings)
    app.state.settings = runtime_settings
    app.add_middleware(
        CORSMiddleware,
        allow_origins=runtime_settings.allowed_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "Idempotency-Key", "X-Request-ID"],
    )

    @app.middleware("http")
    async def add_request_id(request: Request, call_next: RequestResponseEndpoint) -> Response:
        request_id = uuid4().hex
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response

    @app.exception_handler(SubscriptionSuspendedError)
    async def subscription_suspended_handler(
        _request: Request,
        _exc: SubscriptionSuspendedError,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=423,
            content={"detail": "subscription_suspended"},
        )

    app.include_router(health_router)
    app.include_router(context_router)
    app.include_router(bookings_router)
    app.include_router(checkout_router)
    app.include_router(corrections_router)
    app.include_router(payouts_router)
    app.include_router(reports_router)
    app.include_router(legal_cash_router)
    app.include_router(platform_tenants_router)
    app.include_router(platform_subscriptions_router)
    app.include_router(platform_exports_router)
    app.include_router(tenant_router)
    app.include_router(public_router)
    return app


app = create_app()
