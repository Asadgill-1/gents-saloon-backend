from datetime import UTC, datetime, timedelta

import pytest

from app.core.config import Settings
from app.main import create_app
from app.services.report_service import ReportInputError, _validated_period


def test_report_period_requires_timezone_and_is_bounded() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    end = datetime(2026, 1, 2, tzinfo=UTC)
    assert _validated_period(start, end) == (start, end)

    with pytest.raises(ReportInputError, match="timezone"):
        _validated_period(datetime(2026, 1, 1), end)
    with pytest.raises(ReportInputError, match="positive"):
        _validated_period(end, start)
    with pytest.raises(ReportInputError, match="366"):
        _validated_period(start, start + timedelta(days=366, microseconds=1))


def test_report_routes_require_period_and_bound_page_size() -> None:
    paths = create_app(Settings(_env_file=None)).openapi()["paths"]
    report_paths = (
        "/api/v1/businesses/{business_id}/shops/{shop_id}/reports",
        "/api/v1/businesses/{business_id}/overview",
    )
    for path in report_paths:
        operation = paths[path]["get"]
        parameters = {parameter["name"]: parameter for parameter in operation["parameters"]}
        assert parameters["period_start"]["required"] is True
        assert parameters["period_end"]["required"] is True
        assert parameters["limit"]["schema"]["minimum"] == 1
        assert parameters["limit"]["schema"]["maximum"] == 100
        assert "Idempotency-Key" not in parameters
