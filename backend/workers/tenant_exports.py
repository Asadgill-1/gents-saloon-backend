import asyncio
import sys
from typing import Any

from celery import shared_task

from app.core.config import get_settings
from app.core.database import create_database_pool
from app.services.export_service import process_next_export, purge_expired_exports
from app.services.export_storage import create_export_storage

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


async def _process_exports() -> dict[str, int]:
    settings = get_settings()
    pool = create_database_pool(settings)
    await pool.open()
    try:
        storage = create_export_storage(settings)
        processed = 0
        for _ in range(25):
            if not await process_next_export(
                pool,
                storage,
                retention_hours=settings.export_retention_hours,
            ):
                break
            processed += 1
        purged = await purge_expired_exports(pool, storage)
        return {"processed": processed, "purged": purged}
    finally:
        await pool.close()


@shared_task(  # type: ignore[untyped-decorator]
    name="workers.tenant_exports.process",
    soft_time_limit=300,
    time_limit=330,
)
def process_exports() -> dict[str, Any]:
    return asyncio.run(_process_exports())
