from datetime import UTC, date, datetime

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.main import create_app
from app.services.subscription_service import (
    CashReceiptRequest,
    ResumeSubscriptionRequest,
)


def test_cash_receipt_rejects_invalid_coverage_and_naive_clock() -> None:
    base = {
        "subscription_id": "30000000-0000-0000-0000-000000000001",
        "amount": "500.00",
        "receipt_reference": "CASH-001",
        "collected_at": datetime(2026, 7, 25, 10, tzinfo=UTC),
        "coverage_from": date(2026, 7, 1),
        "coverage_until": date(2026, 7, 31),
    }
    with pytest.raises(ValidationError, match="coverage_until"):
        CashReceiptRequest.model_validate({**base, "coverage_until": date(2026, 6, 30)})
    with pytest.raises(ValidationError, match="timezone"):
        CashReceiptRequest.model_validate({**base, "collected_at": datetime(2026, 7, 25, 10)})


def test_manual_override_requires_reason_expiry_pair() -> None:
    with pytest.raises(ValidationError, match="supplied together"):
        ResumeSubscriptionRequest(
            explanation="approved",
            manual_override_until=datetime(2026, 8, 1, tzinfo=UTC),
        )


def test_subscription_mutations_require_idempotency_header() -> None:
    paths = create_app(Settings(_env_file=None)).openapi()["paths"]
    operation = paths["/api/v1/platform/subscriptions/cash-receipts"]["post"]
    parameters = {parameter["name"]: parameter for parameter in operation["parameters"]}

    assert parameters["Idempotency-Key"]["required"] is True
