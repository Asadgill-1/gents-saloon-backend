import asyncio
import sys
from typing import Any

from celery import shared_task

from app.core.config import get_settings
from app.core.database import create_database_pool
from app.services.subscription_service import expire_due_subscriptions

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


async def _expire() -> int:
    pool = create_database_pool(get_settings())
    await pool.open()
    try:
        total = 0
        for _ in range(100):
            expired = await expire_due_subscriptions(pool)
            total += expired
            if expired < 100:
                break
        return total
    finally:
        await pool.close()


@shared_task(name="workers.subscriptions.expire_due")  # type: ignore[untyped-decorator]
def expire_due() -> dict[str, Any]:
    return {"expired": asyncio.run(_expire())}
