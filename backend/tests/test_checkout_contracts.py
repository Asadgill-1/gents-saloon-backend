from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.main import create_app
from app.services.checkout_calculations import (
    MoneyCalculationError,
    calculate_commission,
    calculate_line,
)
from app.services.checkout_service import CheckoutRequest


def test_vat_discount_and_commission_golden_calculations() -> None:
    inclusive = calculate_line(
        unit_amount=Decimal("105.00"),
        discount_input=Decimal("0.00"),
        vat_rate=Decimal("5.00"),
        pricing_mode="vat_inclusive",
    )
    assert inclusive.line_net == Decimal("100.00")
    assert inclusive.line_vat == Decimal("5.00")
    assert inclusive.line_gross == Decimal("105.00")

    exclusive = calculate_line(
        unit_amount=Decimal("100.00"),
        discount_input=Decimal("10.00"),
        vat_rate=Decimal("5.00"),
        pricing_mode="vat_exclusive",
    )
    assert exclusive.pre_discount_gross == Decimal("105.00")
    assert exclusive.discount_gross == Decimal("10.50")
    assert exclusive.line_net == Decimal("90.00")
    assert exclusive.line_vat == Decimal("4.50")
    assert exclusive.line_gross == Decimal("94.50")

    tier = calculate_commission(
        commission_base=Decimal("120.00"),
        rule_type="tier",
        barber_pct=None,
        tiers=[
            {"min_base": 0, "max_base": 120, "barber_pct": 20},
            {"min_base": 120, "barber_flat": 25},
        ],
    )
    assert tier.barber_commission == Decimal("25.00")
    assert tier.shop_share == Decimal("95.00")


def test_money_calculations_fail_closed_on_invalid_configuration() -> None:
    with pytest.raises(MoneyCalculationError, match="discount exceeds"):
        calculate_line(
            unit_amount=Decimal("50.00"),
            discount_input=Decimal("50.01"),
            vat_rate=Decimal("5.00"),
            pricing_mode="vat_inclusive",
        )
    with pytest.raises(MoneyCalculationError, match="exceeds"):
        calculate_commission(
            commission_base=Decimal("20.00"),
            rule_type="tier",
            barber_pct=None,
            tiers=[{"min_base": 0, "barber_flat": 25}],
        )


def test_money_rounding_invariants_reconcile_across_fils_range() -> None:
    for cents in range(0, 50_001, 137):
        amount = Decimal(cents) / 100
        inclusive = calculate_line(
            unit_amount=amount,
            discount_input=Decimal("0.00"),
            vat_rate=Decimal("5.00"),
            pricing_mode="vat_inclusive",
        )
        exclusive = calculate_line(
            unit_amount=amount,
            discount_input=Decimal("0.00"),
            vat_rate=Decimal("5.00"),
            pricing_mode="vat_exclusive",
        )
        commission = calculate_commission(
            commission_base=inclusive.line_net,
            rule_type="fixed_percentage",
            barber_pct=Decimal("33.33"),
            tiers=None,
        )

        assert inclusive.line_net + inclusive.line_vat == inclusive.line_gross
        assert exclusive.line_net + exclusive.line_vat == exclusive.line_gross
        assert commission.barber_commission + commission.shop_share == inclusive.line_net


def test_checkout_request_rejects_financial_authority_and_card_data() -> None:
    booking_id = uuid4()
    cash_shift_id = uuid4()
    valid = {
        "booking_id": booking_id,
        "payments": [{"method": "cash", "amount": "120.00"}],
        "cash_shift_id": cash_shift_id,
    }
    assert CheckoutRequest.model_validate(valid).booking_id == booking_id

    for field in ("grand_total", "vat_rate", "barber_membership_id", "shop_id"):
        with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
            CheckoutRequest.model_validate({**valid, field: "1"})

    with pytest.raises(ValidationError, match="unsafe format"):
        CheckoutRequest.model_validate(
            {
                "booking_id": booking_id,
                "payments": [
                    {
                        "method": "card",
                        "amount": "120.00",
                        "card_slip_reference": "4111111111111111",
                    }
                ],
            }
        )
    with pytest.raises(ValidationError, match="unsafe format"):
        CheckoutRequest.model_validate(
            {
                "booking_id": booking_id,
                "payments": [
                    {
                        "method": "card",
                        "amount": "120.00",
                        "card_slip_reference": "4111-1111-1111-1111",
                    }
                ],
            }
        )
    with pytest.raises(ValidationError, match="exactly one cash shift"):
        CheckoutRequest.model_validate(
            {
                "booking_id": booking_id,
                "payments": [{"method": "cash", "amount": "120.00"}],
            }
        )


def test_checkout_route_requires_idempotency() -> None:
    path = "/api/v1/businesses/{business_id}/shops/{shop_id}/pos/checkout"
    operation = create_app(Settings(_env_file=None)).openapi()["paths"][path]["post"]
    parameters = {parameter["name"]: parameter for parameter in operation["parameters"]}
    assert parameters["Idempotency-Key"]["required"] is True
