import asyncio
import hashlib
import io
import os
import zipfile
from uuid import UUID

import pytest

from app.core.authorization import resolve_actor_context
from app.core.config import Settings
from app.core.database import create_database_pool
from app.services.export_service import (
    ExportExpiredError,
    ExportSubjectRequest,
    OffboardingRequest,
    OffboardingStateConflictError,
    archive_offboarding,
    begin_offboarding,
    confirm_export_delivery,
    create_export_download,
    process_next_export,
    purge_expired_exports,
    request_tenant_export,
)
from app.services.platform_operations import PlatformAdminRequiredError

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_DATABASE_INTEGRATION") != "1",
    reason="requires reconstructed Phase 1 PostgreSQL test database",
)

ADMIN_ID = UUID("00000000-0000-0000-0000-000000000001")
OWNER_A_ID = UUID("00000000-0000-0000-0000-000000000002")
STAFF_A_ID = UUID("00000000-0000-0000-0000-000000000003")
BUSINESS_A_ID = UUID("10000000-0000-0000-0000-000000000001")
SHOP_A1_ID = UUID("20000000-0000-0000-0000-000000000001")
SHOP_A2_ID = UUID("20000000-0000-0000-0000-000000000002")


class _Storage:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.deleted: list[str] = []
        self.ttls: list[int] = []

    def upload(self, object_key: str, content: bytes) -> None:
        self.objects[object_key] = content

    def create_download_url(self, object_key: str, ttl_seconds: int) -> str:
        assert object_key in self.objects
        self.ttls.append(ttl_seconds)
        return f"https://storage.example.invalid/{object_key}?signed=test"

    def delete(self, object_key: str) -> None:
        self.objects.pop(object_key, None)
        self.deleted.append(object_key)


async def test_export_is_private_checksummed_expiring_and_does_not_freeze() -> None:
    settings = Settings(_env_file=None)
    pool = create_database_pool(settings)
    storage = _Storage()
    await pool.open()
    try:
        requested = await request_tenant_export(
            pool,
            actor_id=ADMIN_ID,
            idempotency_key="phase1-export-request-0001",
            request_id="phase1-export-request",
            payload=ExportSubjectRequest(
                business_id=BUSINESS_A_ID,
                scope="business",
            ),
        )
        replay = await request_tenant_export(
            pool,
            actor_id=ADMIN_ID,
            idempotency_key="phase1-export-request-0001",
            request_id="phase1-export-request-replay",
            payload=ExportSubjectRequest(
                business_id=BUSINESS_A_ID,
                scope="business",
            ),
        )
        assert replay == requested
        assert await process_next_export(pool, storage, retention_hours=72) is True

        object_key = f"tenant-exports/{requested.export_id}.zip"
        content = storage.objects[object_key]
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            manifest = archive.read("manifest.json").decode()
            bot_export = archive.read("bots.json").decode()
            shop_export = archive.read("shops.json").decode()
        assert '"schema_version": "2026-07-26.v1"' in manifest
        assert "token_ciphertext" not in bot_export
        assert "webhook_secret_hash" not in bot_export
        assert "public_queue_token_hash" not in shop_export

        async with pool.connection(timeout=5) as connection:
            cursor = await connection.execute(
                """
                select status::text, sha256, size_bytes, content_type,
                       expires_at > ready_at
                from public.tenant_exports
                where id = %s
                """,
                (requested.export_id,),
            )
            row = await cursor.fetchone()
        assert row == (
            "ready",
            hashlib.sha256(content).hexdigest(),
            len(content),
            "application/zip",
            True,
        )

        with pytest.raises(PlatformAdminRequiredError):
            await create_export_download(
                pool,
                storage,
                settings,
                actor_id=STAFF_A_ID,
                export_id=requested.export_id,
                request_id="denied-download",
            )
        download = await create_export_download(
            pool,
            storage,
            settings,
            actor_id=ADMIN_ID,
            export_id=requested.export_id,
            request_id="authorized-download",
        )
        assert download.download_url.startswith("https://storage.example.invalid/")
        assert storage.ttls == [settings.export_download_ttl_seconds]

        delivered = await confirm_export_delivery(
            pool,
            actor_id=ADMIN_ID,
            export_id=requested.export_id,
            idempotency_key="phase1-export-delivery-0001",
            request_id="confirm-export-delivery",
        )
        assert delivered.status == "delivered"
        context = await resolve_actor_context(pool, OWNER_A_ID)
        assert {access.shop_id for access in context.shop_access} == {
            SHOP_A1_ID,
            SHOP_A2_ID,
        }

        async with pool.connection(timeout=5) as connection, connection.transaction():
            await connection.execute(
                """
                update public.tenant_exports
                set ready_at = now() - interval '2 hours',
                    expires_at = now() - interval '1 hour'
                where id = %s
                """,
                (requested.export_id,),
            )
        assert await purge_expired_exports(pool, storage) == 1
        assert storage.deleted == [object_key]
        with pytest.raises(ExportExpiredError):
            await create_export_download(
                pool,
                storage,
                settings,
                actor_id=ADMIN_ID,
                export_id=requested.export_id,
                request_id="expired-download",
            )
    finally:
        await pool.close()


async def test_shop_offboarding_is_export_first_and_preserves_sibling_shop() -> None:
    settings = Settings(_env_file=None)
    pool = create_database_pool(settings)
    storage = _Storage()
    await pool.open()
    payload = OffboardingRequest(
        business_id=BUSINESS_A_ID,
        shop_id=SHOP_A2_ID,
        scope="shop",
        reason="Owner closed this location",
    )
    try:
        attempts = await asyncio.gather(
            begin_offboarding(
                pool,
                actor_id=ADMIN_ID,
                idempotency_key="phase1-offboard-shop-0001",
                request_id="offboard-shop-1",
                payload=payload,
            ),
            begin_offboarding(
                pool,
                actor_id=ADMIN_ID,
                idempotency_key="phase1-offboard-shop-0002",
                request_id="offboard-shop-2",
                payload=payload,
            ),
            return_exceptions=True,
        )
        completed = [value for value in attempts if not isinstance(value, Exception)]
        conflicts = [
            value for value in attempts if isinstance(value, OffboardingStateConflictError)
        ]
        assert len(completed) == 1
        assert len(conflicts) == 1
        offboarding = completed[0]

        with pytest.raises(OffboardingStateConflictError):
            await archive_offboarding(
                pool,
                actor_id=ADMIN_ID,
                case_id=offboarding.case_id,
                idempotency_key="phase1-offboard-early-archive",
                request_id="early-archive",
            )

        assert await process_next_export(pool, storage, retention_hours=72) is True
        await confirm_export_delivery(
            pool,
            actor_id=ADMIN_ID,
            export_id=offboarding.export_id,
            idempotency_key="phase1-offboard-delivery-0001",
            request_id="offboard-delivery",
        )
        archived = await archive_offboarding(
            pool,
            actor_id=ADMIN_ID,
            case_id=offboarding.case_id,
            idempotency_key="phase1-offboard-archive-0001",
            request_id="offboard-archive",
        )
        replay = await archive_offboarding(
            pool,
            actor_id=ADMIN_ID,
            case_id=offboarding.case_id,
            idempotency_key="phase1-offboard-archive-0001",
            request_id="offboard-archive-replay",
        )
        assert replay == archived
        assert archived.state == "archived"

        async with pool.connection(timeout=5) as connection:
            cursor = await connection.execute(
                """
                select
                  (select status::text from public.businesses where id = %s),
                  (select status::text from public.shops where id = %s),
                  (select status::text from public.shops where id = %s),
                  (select status::text
                   from public.subscriptions
                   where business_id = %s and scope = 'business' and status <> 'archived'),
                  (select count(*) from public.shops where business_id = %s),
                  (select count(*) from public.subscription_cash_receipts
                   where business_id = %s)
                """,
                (
                    BUSINESS_A_ID,
                    SHOP_A1_ID,
                    SHOP_A2_ID,
                    BUSINESS_A_ID,
                    BUSINESS_A_ID,
                    BUSINESS_A_ID,
                ),
            )
            state = await cursor.fetchone()
        assert state[0:4] == ("active", "active", "archived", "active")
        assert state[4] == 2
        assert state[5] > 0

        owner_context = await resolve_actor_context(pool, OWNER_A_ID)
        assert [access.shop_id for access in owner_context.shop_access] == [SHOP_A1_ID]
    finally:
        await pool.close()
