import asyncio
import hashlib
import os
from datetime import UTC, date, datetime, time
from uuid import UUID

import pytest

from app.core.config import Settings
from app.core.database import create_database_pool
from app.services import tenant_service
from app.services.tenant_service import (
    IdempotencyConflictError,
    OwnerIdentityInactiveError,
    OwnerIdentityNotFoundError,
    PlatformAdminRequiredError,
    TenantOnboardingConflictError,
    TenantOnboardingRequest,
    onboard_tenant,
)

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_DATABASE_INTEGRATION") != "1",
    reason="requires reconstructed Phase 1 PostgreSQL test database",
)

ADMIN_ID = UUID("00000000-0000-0000-0000-000000000001")
NON_ADMIN_ID = UUID("00000000-0000-0000-0000-000000000002")
OWNER_A_ID = UUID("00000000-0000-0000-0000-000000000007")
OWNER_B_ID = UUID("00000000-0000-0000-0000-000000000008")
ROLLBACK_OWNER_ID = UUID("00000000-0000-0000-0000-000000000009")
INACTIVE_OWNER_ID = UUID("00000000-0000-0000-0000-000000000006")


def _payload(
    owner_id: UUID,
    *,
    legal_name: str,
    billing_mode: str = "business",
) -> TenantOnboardingRequest:
    return TenantOnboardingRequest.model_validate(
        {
            "legal_name": legal_name,
            "display_name": legal_name.removesuffix(" LLC"),
            "billing_mode": billing_mode,
            "owner_auth_user_id": owner_id,
            "owner_display_name": f"Owner {owner_id.int % 100}",
            "shop_name": f"Shop {owner_id.int % 100}",
            "shop_internal_code": f"SHOP-{owner_id.int % 100}",
            "shop_open_time": time(9),
            "shop_close_time": time(23),
            "shop_eod_time": time(23, 30),
            "default_service_minutes": 30,
            "paid_from": date(2026, 7, 1),
            "paid_until": date(2026, 7, 31),
            "initial_payment_amount": "500.00",
            "initial_receipt_reference": f"INITIAL-{owner_id.int % 100:02d}",
            "initial_collected_at": datetime(2026, 7, 1, 9, tzinfo=UTC),
        }
    )


async def _fetchone(pool: object, query: str, params: tuple[object, ...]) -> tuple[object, ...]:
    async with pool.connection(timeout=5) as connection:  # type: ignore[attr-defined]
        cursor = await connection.execute(query, params)
        row = await cursor.fetchone()
        assert row is not None
        return row


async def test_atomic_idempotent_platform_tenant_onboarding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pool = create_database_pool(Settings(_env_file=None))
    await pool.open()
    try:
        payload_a = _payload(OWNER_A_ID, legal_name="Onboarding A LLC")
        key_a = "onboard-concurrent-0001"
        first, replay = await asyncio.gather(
            onboard_tenant(
                pool,
                actor_id=ADMIN_ID,
                idempotency_key=key_a,
                request_id="request-a1",
                payload=payload_a,
            ),
            onboard_tenant(
                pool,
                actor_id=ADMIN_ID,
                idempotency_key=key_a,
                request_id="request-a2",
                payload=payload_a,
            ),
        )
        assert first == replay

        persisted = await _fetchone(
            pool,
            """
            select
              (select count(*) from public.businesses where id = %s),
              (select count(*) from public.business_owners where business_id = %s),
              (select count(*) from public.shops where business_id = %s),
              (select count(*) from public.subscriptions where business_id = %s),
              (select count(*) from public.subscription_cash_receipts where business_id = %s),
              (select count(*) from public.audit_log
                where business_id = %s and action = 'tenant.onboarded'),
              (select count(*) from public.outbox_events
                where business_id = %s and topic = 'tenant.onboarded'),
              (select count(*) from public.shop_memberships
                where business_id = %s and auth_user_id = %s),
              (select public_queue_token_hash from public.shops where id = %s),
              (select after::text from public.audit_log
                where business_id = %s and action = 'tenant.onboarded'),
              (select payload::text from public.outbox_events
                where business_id = %s and topic = 'tenant.onboarded')
            """,
            (
                first.business_id,
                first.business_id,
                first.business_id,
                first.business_id,
                first.business_id,
                first.business_id,
                first.business_id,
                first.business_id,
                OWNER_A_ID,
                first.shop_id,
                first.business_id,
                first.business_id,
            ),
        )
        assert persisted[:7] == (1, 1, 1, 1, 1, 1, 1)
        assert persisted[7] == 0
        assert persisted[8] == hashlib.sha256(first.public_queue_token.encode()).hexdigest()
        assert first.public_queue_token not in str(persisted[9])
        assert first.public_queue_token not in str(persisted[10])

        with pytest.raises(IdempotencyConflictError):
            await onboard_tenant(
                pool,
                actor_id=ADMIN_ID,
                idempotency_key=key_a,
                request_id="request-a3",
                payload=payload_a.model_copy(update={"display_name": "Changed"}),
            )

        payload_b = _payload(
            OWNER_B_ID,
            legal_name="Onboarding B LLC",
            billing_mode="per_shop",
        )
        per_shop = await onboard_tenant(
            pool,
            actor_id=ADMIN_ID,
            idempotency_key="onboard-per-shop-0002",
            request_id="request-b1",
            payload=payload_b,
        )
        scope = await _fetchone(
            pool,
            """
            select scope::text, shop_id
            from public.subscriptions
            where id = %s
            """,
            (per_shop.subscription_id,),
        )
        assert scope == ("shop", per_shop.shop_id)

        with pytest.raises(PlatformAdminRequiredError):
            await onboard_tenant(
                pool,
                actor_id=NON_ADMIN_ID,
                idempotency_key="onboard-non-admin-003",
                request_id="request-denied",
                payload=_payload(ROLLBACK_OWNER_ID, legal_name="Denied LLC"),
            )

        with pytest.raises(OwnerIdentityInactiveError):
            await onboard_tenant(
                pool,
                actor_id=ADMIN_ID,
                idempotency_key="onboard-inactive-0004",
                request_id="request-inactive",
                payload=_payload(INACTIVE_OWNER_ID, legal_name="Inactive LLC"),
            )

        with pytest.raises(OwnerIdentityNotFoundError):
            await onboard_tenant(
                pool,
                actor_id=ADMIN_ID,
                idempotency_key="onboard-missing-owner-05",
                request_id="request-missing",
                payload=_payload(
                    UUID("ffffffff-ffff-ffff-ffff-ffffffffffff"),
                    legal_name="Missing Owner LLC",
                ),
            )

        monkeypatch.setattr(
            tenant_service.secrets,
            "token_urlsafe",
            lambda _length: first.public_queue_token,
        )
        rollback_key = "onboard-rollback-0006"
        with pytest.raises(TenantOnboardingConflictError):
            await onboard_tenant(
                pool,
                actor_id=ADMIN_ID,
                idempotency_key=rollback_key,
                request_id="request-rollback",
                payload=_payload(ROLLBACK_OWNER_ID, legal_name="Rollback LLC"),
            )

        rolled_back = await _fetchone(
            pool,
            """
            select
              (select count(*) from public.businesses where legal_name = 'Rollback LLC'),
              (select count(*) from public.user_profiles where auth_user_id = %s),
              (select count(*) from public.idempotency_keys
                where scope = %s and key = %s)
            """,
            (
                ROLLBACK_OWNER_ID,
                f"{tenant_service.ONBOARDING_SCOPE}:{ADMIN_ID}",
                rollback_key,
            ),
        )
        assert rolled_back == (0, 0, 0)
    finally:
        await pool.close()
