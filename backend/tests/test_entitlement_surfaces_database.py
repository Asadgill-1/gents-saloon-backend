import hashlib
import os
from uuid import UUID

import pytest
from fastapi import HTTPException, Request

from app.api.public import resolve_public_availability
from app.api.tenant import tenant_request_context
from app.core.auth import VerifiedIdentity
from app.core.config import Settings
from app.core.database import create_database_pool
from app.core.entitlements import SubscriptionSuspendedError
from app.main import create_app
from telegram_bot.subscription_gate import SubscriptionGateMiddleware, TrustedBotScope

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_DATABASE_INTEGRATION") != "1",
    reason="requires reconstructed Phase 1 PostgreSQL test database",
)

ADMIN_ID = UUID("00000000-0000-0000-0000-000000000001")
OWNER_A_ID = UUID("00000000-0000-0000-0000-000000000002")
STAFF_A_ID = UUID("00000000-0000-0000-0000-000000000003")
OWNER_B_ID = UUID("00000000-0000-0000-0000-000000000004")
BUSINESS_A_ID = UUID("10000000-0000-0000-0000-000000000001")
SHOP_A1_ID = UUID("20000000-0000-0000-0000-000000000001")
SHOP_A2_ID = UUID("20000000-0000-0000-0000-000000000002")
PUBLIC_TOKEN = "phase1-public-queue-token-a1"


def _request(pool: object) -> Request:
    app = create_app(Settings(_env_file=None))
    app.state.database_pool = pool
    return Request({"type": "http", "app": app})


async def _set_subscription_state(pool: object, status: str) -> None:
    async with pool.connection(timeout=5) as connection, connection.transaction():  # type: ignore[attr-defined]
        if status == "suspended":
            await connection.execute(
                """
                update public.subscriptions
                set status = 'suspended',
                    suspended_reason = 'manual',
                    suspended_at = now(),
                    updated_at = now()
                where business_id = %s and status <> 'archived'
                """,
                (BUSINESS_A_ID,),
            )
        else:
            await connection.execute(
                """
                update public.subscriptions
                set status = 'active',
                    suspended_reason = null,
                    suspended_at = null,
                    updated_at = now()
                where business_id = %s and status <> 'archived'
                """,
                (BUSINESS_A_ID,),
            )
        await connection.execute(
            "update public.businesses set status = %s, updated_at = now() where id = %s",
            (status, BUSINESS_A_ID),
        )


async def test_api_bot_dashboard_contract_and_public_surface_agree() -> None:
    pool = create_database_pool(Settings(_env_file=None))
    await pool.open()
    request = _request(pool)
    scope = TrustedBotScope(BUSINESS_A_ID, SHOP_A1_ID)
    operations: list[str] = []
    replies: list[str] = []

    async def operation() -> None:
        operations.append("operation")

    async def reply(message: str) -> None:
        replies.append(message)

    try:
        async with pool.connection(timeout=5) as connection, connection.transaction():
            await connection.execute(
                """
                update public.shops
                set public_queue_token_hash = %s
                where id = %s
                """,
                (hashlib.sha256(PUBLIC_TOKEN.encode()).hexdigest(), SHOP_A1_ID),
            )

        owner = await tenant_request_context(
            request,
            BUSINESS_A_ID,
            SHOP_A1_ID,
            VerifiedIdentity(OWNER_A_ID),
        )
        staff = await tenant_request_context(
            request,
            BUSINESS_A_ID,
            SHOP_A1_ID,
            VerifiedIdentity(STAFF_A_ID),
        )
        admin = await tenant_request_context(
            request,
            BUSINESS_A_ID,
            SHOP_A1_ID,
            VerifiedIdentity(ADMIN_ID),
        )
        assert owner.roles == ("owner",)
        assert staff.roles == ("receptionist",)
        assert admin.roles == ("platform_admin",)
        assert (await resolve_public_availability(pool, PUBLIC_TOKEN)).status == "available"
        active_bot = await SubscriptionGateMiddleware(pool).handle(
            scope,
            operation=operation,
            unavailable_reply=reply,
        )
        assert active_bot.operation_performed is True

        for actor_id in (STAFF_A_ID, OWNER_B_ID):
            with pytest.raises(HTTPException) as denied:
                await tenant_request_context(
                    request,
                    BUSINESS_A_ID,
                    SHOP_A2_ID if actor_id == STAFF_A_ID else SHOP_A1_ID,
                    VerifiedIdentity(actor_id),
                )
            assert denied.value.status_code == 403
            assert denied.value.detail == "tenant_access_denied"

        await _set_subscription_state(pool, "suspended")
        with pytest.raises(SubscriptionSuspendedError):
            await tenant_request_context(
                request,
                BUSINESS_A_ID,
                SHOP_A1_ID,
                VerifiedIdentity(OWNER_A_ID),
            )

        admin_during_suspension = await tenant_request_context(
            request,
            BUSINESS_A_ID,
            SHOP_A1_ID,
            VerifiedIdentity(ADMIN_ID),
        )
        assert admin_during_suspension.is_platform_admin is True
        assert (await resolve_public_availability(pool, PUBLIC_TOKEN)).status == "unavailable"
        blocked_bot = await SubscriptionGateMiddleware(pool).handle(
            scope,
            operation=operation,
            unavailable_reply=reply,
        )
        assert blocked_bot.acknowledged is True
        assert blocked_bot.operation_performed is False
        assert operations == ["operation"]
        assert len(replies) == 1
        assert "payment" not in replies[0].lower()
        assert "subscription" not in replies[0].lower()

        assert (
            await resolve_public_availability(pool, "invalid-but-long-enough-token")
        ).status == "unavailable"
        assert (await resolve_public_availability(pool, "short")).status == "unavailable"
    finally:
        await _set_subscription_state(pool, "active")
        await pool.close()


def test_surface_routes_are_registered() -> None:
    paths = create_app(Settings(_env_file=None)).openapi()["paths"]

    assert "/api/v1/businesses/{business_id}/shops/{shop_id}/session" in paths
    assert "/api/v1/public/shops/{public_queue_token}/availability" in paths
