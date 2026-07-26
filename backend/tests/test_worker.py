from app.core.celery_app import celery_app
from workers.health import ping


def test_celery_accepts_json_only() -> None:
    assert celery_app.conf.accept_content == ["json"]
    assert celery_app.conf.task_serializer == "json"
    assert celery_app.conf.result_serializer == "json"
    assert celery_app.conf.task_soft_time_limit == 60
    assert celery_app.conf.task_time_limit == 90
    assert celery_app.conf.result_expires == 3600


def test_health_task_is_discoverable() -> None:
    result = ping.run()

    assert result["status"] == "ok"


def test_subscription_expiry_runs_at_dubai_0005() -> None:
    schedule = celery_app.conf.beat_schedule["expire-subscriptions-at-dubai-0005"]

    assert schedule["task"] == "workers.subscriptions.expire_due"
    assert str(schedule["schedule"]) == "<crontab: 5 0 * * * (m/h/dM/MY/d)>"
