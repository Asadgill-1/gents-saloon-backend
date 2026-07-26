from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.main import create_app
from app.services.legal_cash_service import (
    CashMovementRecord,
    CashMovementRequest,
    CashShiftOpenRequest,
)


def test_cash_contracts_reject_untrusted_or_inexact_inputs() -> None:
    with pytest.raises(ValidationError, match="greater than or equal to 0"):
        CashShiftOpenRequest(register_label="Front", opening_float=Decimal("-0.01"))
    with pytest.raises(ValidationError, match="decimal places"):
        CashMovementRequest(
            movement_type="pay_in",
            amount=Decimal("1.001"),
            reason="Float",
        )
    with pytest.raises(ValidationError, match="Input should be 'pay_in' or 'pay_out'"):
        CashMovementRequest(
            movement_type="card",  # type: ignore[arg-type]
            amount=Decimal("1.00"),
            reason="Card must never affect physical cash",
        )
    with pytest.raises(ValidationError, match="surrounding whitespace"):
        CashShiftOpenRequest(register_label=" Front ", opening_float=Decimal("0"))
    with pytest.raises(ValidationError, match="source_entity_id"):
        CashMovementRecord(movement_type="refund", amount=Decimal("10.00"))
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        CashShiftOpenRequest(
            register_label="Front",
            opening_float=Decimal("0"),
            client_expected_cash=Decimal("999"),  # type: ignore[call-arg]
        )


def test_legal_cash_routes_require_idempotency_for_mutations() -> None:
    paths = create_app(Settings(_env_file=None)).openapi()["paths"]
    mutation_paths = (
        "/api/v1/businesses/{business_id}/shops/{shop_id}/cash-shifts/open",
        "/api/v1/businesses/{business_id}/shops/{shop_id}/cash-shifts/{cash_shift_id}/movements",
        "/api/v1/businesses/{business_id}/shops/{shop_id}/cash-shifts/{cash_shift_id}/close",
    )
    for path in mutation_paths:
        parameters = {
            parameter["name"]: parameter for parameter in paths[path]["post"]["parameters"]
        }
        assert parameters["Idempotency-Key"]["required"] is True

    assert "/api/v1/businesses/{business_id}/shops/{shop_id}/legal-document-profile" in paths
