from app.core.config import Settings
from app.main import create_app


def test_shop_and_platform_read_routes_are_registered() -> None:
    paths = create_app(Settings(_env_file=None)).openapi()["paths"]
    expected = {
        "/api/v1/businesses/{business_id}/shops/{shop_id}/bookings",
        "/api/v1/businesses/{business_id}/shops/{shop_id}/services",
        "/api/v1/businesses/{business_id}/shops/{shop_id}/barbers",
        "/api/v1/businesses/{business_id}/shops/{shop_id}/cash-shifts",
        "/api/v1/platform/tenants",
        "/api/v1/platform/subscriptions",
        "/api/v1/platform/subscriptions/cash-receipts",
        "/api/v1/platform/offboarding-cases",
        "/api/v1/platform/bots/health",
        "/api/v1/platform/analytics",
    }
    assert expected <= set(paths)
    for path in expected:
        operation = paths[path]["get"]
        parameters = {parameter["name"]: parameter for parameter in operation["parameters"]}
        assert parameters["limit"]["schema"]["default"] == 50
        assert parameters["limit"]["schema"]["maximum"] == 100
        assert "cursor" in parameters


def test_platform_onboarding_mutations_require_idempotency_key() -> None:
    paths = create_app(Settings(_env_file=None)).openapi()["paths"]
    expected = {
        "/api/v1/platform/businesses/{business_id}/shops",
        "/api/v1/platform/businesses/{business_id}/shops/{shop_id}/staff-invitations",
        "/api/v1/platform/businesses/{business_id}/shops/{shop_id}/bots",
        "/api/v1/platform/businesses/{business_id}/shops/{shop_id}/legal-tax",
    }
    assert expected <= set(paths)
    for path in expected:
        parameters = paths[path]["post"]["parameters"]
        idempotency = next(item for item in parameters if item["name"] == "Idempotency-Key")
        assert idempotency["in"] == "header"
        assert idempotency["required"] is True
        assert idempotency["schema"]["minLength"] == 16
