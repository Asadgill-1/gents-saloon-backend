from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.main import create_app
from app.services.booking_service import BookingCreateRequest, BookingTransitionRequest


def test_booking_contract_rejects_invalid_shapes() -> None:
    with pytest.raises(ValidationError, match="appointments require scheduled_start"):
        BookingCreateRequest(
            booking_type="appointment",
            service_ids=["50000000-0000-0000-0000-000000000001"],
        )
    with pytest.raises(ValidationError, match="cannot supply scheduled_start"):
        BookingCreateRequest(
            booking_type="queue",
            service_ids=["50000000-0000-0000-0000-000000000001"],
            scheduled_start=datetime(2030, 1, 1, tzinfo=UTC),
        )
    with pytest.raises(ValidationError, match="service_ids must be unique"):
        BookingCreateRequest(
            booking_type="queue",
            service_ids=[
                "50000000-0000-0000-0000-000000000001",
                "50000000-0000-0000-0000-000000000001",
            ],
        )
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        BookingTransitionRequest(reason="valid reason", untrusted_status="completed")  # type: ignore[call-arg]


def test_booking_routes_require_idempotency_header() -> None:
    paths = create_app(Settings(_env_file=None)).openapi()["paths"]
    route = paths["/api/v1/businesses/{business_id}/shops/{shop_id}/bookings"]["post"]
    parameters = {parameter["name"]: parameter for parameter in route["parameters"]}

    assert parameters["Idempotency-Key"]["required"] is True
    assert "/api/v1/businesses/{business_id}/shops/{shop_id}/bookings/{booking_id}/confirm" in paths
    assert "/api/v1/businesses/{business_id}/shops/{shop_id}/bookings/{booking_id}/start" in paths
    assert "/api/v1/businesses/{business_id}/shops/{shop_id}/bookings/{booking_id}/cancel" in paths
    assert (
        "/api/v1/businesses/{business_id}/shops/{shop_id}/bookings/{booking_id}/reschedule" in paths
    )
