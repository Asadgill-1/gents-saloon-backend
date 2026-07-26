from datetime import UTC, date, datetime, time
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.main import create_app
from app.services.tenant_service import TenantOnboardingRequest


def _payload(**changes: object) -> TenantOnboardingRequest:
    values: dict[str, object] = {
        "legal_name": "Example Grooming LLC",
        "display_name": "Example Grooming",
        "billing_mode": "business",
        "owner_auth_user_id": UUID("00000000-0000-0000-0000-000000000007"),
        "owner_display_name": "Example Owner",
        "shop_name": "Example Marina",
        "shop_internal_code": "MARINA-01",
        "shop_open_time": time(9),
        "shop_close_time": time(23),
        "shop_eod_time": time(23, 30),
        "default_service_minutes": 30,
        "paid_from": date(2026, 7, 1),
        "paid_until": date(2026, 7, 31),
        "initial_payment_amount": "500.00",
        "initial_receipt_reference": "INITIAL-EXAMPLE-001",
        "initial_collected_at": datetime(2026, 7, 1, 9, tzinfo=UTC),
    }
    values.update(changes)
    return TenantOnboardingRequest.model_validate(values)


def test_onboarding_contract_rejects_unknown_and_invalid_values() -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        _payload(untrusted_role="owner")
    with pytest.raises(ValidationError, match="shop_internal_code"):
        _payload(shop_internal_code="marina 1")
    with pytest.raises(ValidationError, match="paid_until"):
        _payload(paid_until=date(2026, 6, 30))
    with pytest.raises(ValidationError, match="TRN"):
        _payload(vat_registered=True)


def test_onboarding_route_requires_idempotency_header() -> None:
    operation = create_app(Settings(_env_file=None)).openapi()["paths"]["/api/v1/platform/tenants"][
        "post"
    ]
    parameters = {parameter["name"]: parameter for parameter in operation["parameters"]}

    assert parameters["Idempotency-Key"]["in"] == "header"
    assert parameters["Idempotency-Key"]["required"] is True
