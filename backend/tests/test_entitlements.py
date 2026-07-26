from datetime import UTC, date, datetime

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core.entitlements import (
    SubscriptionSuspendedError,
    coverage_deadline,
    has_current_coverage,
)
from app.main import create_app


def test_paid_until_is_inclusive_until_dubai_0005_next_day() -> None:
    paid_until = date(2026, 7, 31)

    assert coverage_deadline(paid_until).isoformat() == "2026-08-01T00:05:00+04:00"
    assert has_current_coverage(
        date(2026, 7, 1),
        paid_until,
        at=datetime(2026, 7, 31, 20, 4, 59, tzinfo=UTC),
    )
    assert not has_current_coverage(
        date(2026, 7, 1),
        paid_until,
        at=datetime(2026, 7, 31, 20, 5, tzinfo=UTC),
    )


def test_coverage_does_not_start_before_dubai_paid_from_day() -> None:
    assert not has_current_coverage(
        date(2026, 7, 2),
        date(2026, 7, 31),
        at=datetime(2026, 7, 1, 19, 59, 59, tzinfo=UTC),
    )
    assert has_current_coverage(
        date(2026, 7, 2),
        date(2026, 7, 31),
        at=datetime(2026, 7, 1, 20, 0, tzinfo=UTC),
    )


def test_subscription_suspension_response_is_generic_http_423() -> None:
    app: FastAPI = create_app(Settings(_env_file=None))

    @app.get("/test/subscription-gate")
    async def blocked() -> None:
        raise SubscriptionSuspendedError

    response = TestClient(app).get("/test/subscription-gate")

    assert response.status_code == 423
    assert response.json() == {"detail": "subscription_suspended"}
