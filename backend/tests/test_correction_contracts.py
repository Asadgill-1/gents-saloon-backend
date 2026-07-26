from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.main import create_app
from app.services.checkout_calculations import (
    MoneyCalculationError,
    proportional_cumulative,
)
from app.services.checkout_service import CheckoutPayment
from app.services.correction_service import (
    CorrectionItemRequest,
    CorrectionRequest,
)


def test_cumulative_proportional_refunds_end_at_exact_originals() -> None:
    assert proportional_cumulative(
        original_output=Decimal("100.00"),
        original_input=Decimal("105.00"),
        cumulative_input=Decimal("52.50"),
    ) == Decimal("50.00")
    assert proportional_cumulative(
        original_output=Decimal("100.00"),
        original_input=Decimal("105.00"),
        cumulative_input=Decimal("105.00"),
    ) == Decimal("100.00")
    assert proportional_cumulative(
        original_output=Decimal("30.00"),
        original_input=Decimal("100.00"),
        cumulative_input=Decimal("50.00"),
    ) == Decimal("15.00")
    with pytest.raises(MoneyCalculationError, match="outside"):
        proportional_cumulative(
            original_output=Decimal("100.00"),
            original_input=Decimal("105.00"),
            cumulative_input=Decimal("105.01"),
        )


def test_correction_request_separates_void_and_refund_authority() -> None:
    item_id = uuid4()
    shift_id = uuid4()
    refund = CorrectionRequest(
        kind="refund",
        items=[
            CorrectionItemRequest(
                transaction_item_id=item_id,
                amount=Decimal("52.50"),
            )
        ],
        payments=[
            CheckoutPayment(
                method="card",
                amount=Decimal("52.50"),
                card_slip_reference="REFUND-0001",
            )
        ],
        reason="Customer requested correction",
    )
    assert refund.items[0].transaction_item_id == item_id

    void = CorrectionRequest(
        kind="void",
        cash_shift_id=shift_id,
        reason="Duplicate sale",
    )
    assert not void.items

    with pytest.raises(ValidationError, match="derives the full correction"):
        CorrectionRequest(
            kind="void",
            cash_shift_id=shift_id,
            payments=[CheckoutPayment(method="cash", amount=Decimal("10.00"))],
            reason="Duplicate sale",
        )
    with pytest.raises(ValidationError, match="cash refund"):
        CorrectionRequest(
            kind="refund",
            tip_refund=Decimal("5.00"),
            payments=[CheckoutPayment(method="cash", amount=Decimal("5.00"))],
            reason="Tip entered incorrectly",
        )
    with pytest.raises(ValidationError, match="unsafe format"):
        CorrectionRequest(
            kind="refund",
            tip_refund=Decimal("5.00"),
            payments=[
                CheckoutPayment(
                    method="card",
                    amount=Decimal("5.00"),
                    card_slip_reference="4111-1111-1111-1111",
                )
            ],
            reason="Tip entered incorrectly",
        )
    with pytest.raises(ValidationError, match="Extra inputs"):
        CorrectionRequest.model_validate(
            {
                "kind": "refund",
                "tip_refund": "5.00",
                "payments": [
                    {
                        "method": "card",
                        "amount": "5.00",
                        "card_slip_reference": "REFUND-0002",
                    }
                ],
                "reason": "Tip entered incorrectly",
                "vat_refund": "0.24",
            }
        )


def test_correction_route_requires_idempotency() -> None:
    path = (
        "/api/v1/businesses/{business_id}/shops/{shop_id}/pos/"
        "transactions/{transaction_id}/corrections"
    )
    operation = create_app(Settings(_env_file=None)).openapi()["paths"][path]["post"]
    parameters = {parameter["name"]: parameter for parameter in operation["parameters"]}
    assert parameters["Idempotency-Key"]["required"] is True
