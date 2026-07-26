import asyncio
import sys
from typing import Any

from celery import shared_task

from app.core.config import get_settings
from app.core.database import create_database_pool
from app.services.booking_service import expire_booking_holds, promote_due_appointments

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


async def _maintain_bookings() -> dict[str, int]:
    pool = create_database_pool(get_settings())
    await pool.open()
    try:
        return {
            "expired_holds": await expire_booking_holds(pool),
            "promoted_appointments": await promote_due_appointments(pool),
        }
    finally:
        await pool.close()


@shared_task(name="workers.bookings.maintain")  # type: ignore[untyped-decorator]
def maintain() -> dict[str, Any]:
    return asyncio.run(_maintain_bookings())
