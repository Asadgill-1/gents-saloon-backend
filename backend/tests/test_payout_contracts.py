from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.main import create_app
from app.services.payout_service import (
    AdvanceRequest,
    PayoutAdjustment,
    PayoutRunRequest,
)


def test_advance_and_payout_requests_reject_client_financial_authority() -> None:
    barber_id = uuid4()
    shift_id = uuid4()
    advance = AdvanceRequest(
        barber_membership_id=barber_id,
        cash_shift_id=shift_id,
        amount=Decimal("200.00"),
        note="Cash advance",
    )
    assert advance.amount == Decimal("200.00")

    for field in ("outstanding_amount", "status", "business_id"):
        with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
            AdvanceRequest.model_validate(
                {
                    **advance.model_dump(),
                    field: "0.00",
                }
            )

    with pytest.raises(ValidationError, match="cannot be zero"):
        PayoutAdjustment(
            barber_membership_id=barber_id,
            amount=Decimal("0.00"),
            reason="No change",
        )
    with pytest.raises(ValidationError, match="must be trimmed"):
        PayoutAdjustment(
            barber_membership_id=barber_id,
            amount=Decimal("10.00"),
            reason=" untrimmed",
        )


def test_payout_period_is_timezone_aware_and_adjustments_are_unique() -> None:
    barber_id = uuid4()
    start = datetime(2026, 7, 1, tzinfo=UTC)
    end = datetime(2026, 8, 1, tzinfo=UTC)
    request = PayoutRunRequest(
        period_start=start,
        period_end=end,
        adjustments=[
            PayoutAdjustment(
                barber_membership_id=barber_id,
                amount=Decimal("-5.00"),
                reason="Approved uniform charge",
            )
        ],
    )
    assert request.period_start == start

    with pytest.raises(ValidationError, match="include a timezone"):
        PayoutRunRequest(
            period_start=datetime(2026, 7, 1),
            period_end=end,
        )
    with pytest.raises(ValidationError, match="must be positive"):
        PayoutRunRequest(period_start=end, period_end=start)
    with pytest.raises(ValidationError, match="only one adjustment"):
        PayoutRunRequest(
            period_start=start,
            period_end=end,
            adjustments=[
                PayoutAdjustment(
                    barber_membership_id=barber_id,
                    amount=Decimal("5.00"),
                    reason="Approved bonus",
                ),
                PayoutAdjustment(
                    barber_membership_id=barber_id,
                    amount=Decimal("-2.00"),
                    reason="Approved deduction",
                ),
            ],
        )


def test_advance_and_payout_routes_require_idempotency() -> None:
    schema = create_app(Settings(_env_file=None)).openapi()["paths"]
    paths = (
        "/api/v1/businesses/{business_id}/shops/{shop_id}/advances",
        "/api/v1/businesses/{business_id}/shops/{shop_id}/payout-runs",
        ("/api/v1/businesses/{business_id}/shops/{shop_id}/payout-runs/{payout_run_id}/approve"),
        ("/api/v1/businesses/{business_id}/shops/{shop_id}/payout-runs/{payout_run_id}/pay"),
        ("/api/v1/businesses/{business_id}/shops/{shop_id}/payout-runs/{payout_run_id}/cancel"),
    )
    for path in paths:
        parameters = {
            parameter["name"]: parameter for parameter in schema[path]["post"]["parameters"]
        }
        assert parameters["Idempotency-Key"]["required"] is True
