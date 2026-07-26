from celery import Celery
from celery.schedules import crontab

from app.core.config import get_settings

settings = get_settings()

celery_app = Celery(
    "gents_saloon",
    broker=settings.celery_broker_url.get_secret_value(),
    backend=settings.celery_result_backend.get_secret_value(),
    include=[
        "workers.bookings",
        "workers.health",
        "workers.subscriptions",
        "workers.tenant_exports",
    ],
)
celery_app.conf.update(
    accept_content=["json"],
    task_serializer="json",
    result_serializer="json",
    timezone="Asia/Dubai",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    task_soft_time_limit=60,
    task_time_limit=90,
    worker_prefetch_multiplier=1,
    broker_connection_retry_on_startup=True,
    result_expires=3600,
    beat_schedule={
        "expire-subscriptions-at-dubai-0005": {
            "task": "workers.subscriptions.expire_due",
            "schedule": crontab(hour=0, minute=5),
        },
        "process-tenant-exports": {
            "task": "workers.tenant_exports.process",
            "schedule": crontab(minute="*"),
        },
        "maintain-bookings": {
            "task": "workers.bookings.maintain",
            "schedule": crontab(minute="*"),
        },
    },
)
