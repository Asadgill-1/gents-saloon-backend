from datetime import UTC, datetime

from celery import shared_task


@shared_task(name="workers.health.ping")  # type: ignore[untyped-decorator]
def ping() -> dict[str, str]:
    return {"status": "ok", "timestamp": datetime.now(UTC).isoformat()}
