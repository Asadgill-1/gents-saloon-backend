from datetime import UTC, datetime, timedelta
from unittest.mock import Mock
from uuid import uuid4

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from jwt import PyJWK, PyJWKClient

from app.core.auth import AuthenticationError, JwtVerifier
from app.core.config import Settings

ISSUER = "https://example.supabase.co/auth/v1"
AUDIENCE = "authenticated"


def _verifier_and_key() -> tuple[JwtVerifier, ec.EllipticCurvePrivateKey]:
    private_key = ec.generate_private_key(ec.SECP256R1())
    public_jwk = PyJWK.from_dict(
        {
            **jwt.algorithms.ECAlgorithm.to_jwk(private_key.public_key(), as_dict=True),
            "alg": "ES256",
            "kid": "test-key",
            "use": "sig",
        }
    )
    jwks_client = Mock(spec=PyJWKClient)
    jwks_client.get_signing_key_from_jwt.return_value = public_jwk
    settings = Settings(
        supabase_url="https://example.supabase.co",
        supabase_jwt_audience=AUDIENCE,
        _env_file=None,
    )
    return JwtVerifier(settings, jwks_client=jwks_client), private_key


def _token(
    private_key: ec.EllipticCurvePrivateKey,
    *,
    subject: str | None = None,
    audience: str = AUDIENCE,
    issuer: str = ISSUER,
    expires_at: datetime | None = None,
    not_before: datetime | None = None,
    extra_claims: dict[str, object] | None = None,
) -> str:
    now = datetime.now(UTC)
    claims: dict[str, object] = {
        "sub": subject or str(uuid4()),
        "aud": audience,
        "iss": issuer,
        "iat": now,
        "exp": expires_at or now + timedelta(minutes=5),
    }
    if not_before is not None:
        claims["nbf"] = not_before
    claims.update(extra_claims or {})
    return jwt.encode(
        claims,
        private_key,
        algorithm="ES256",
        headers={"kid": "test-key"},
    )


async def test_verifies_signature_registered_claims_and_uuid_subject() -> None:
    verifier, private_key = _verifier_and_key()
    subject = uuid4()

    identity = await verifier.verify(_token(private_key, subject=str(subject)))

    assert identity.auth_user_id == subject


@pytest.mark.parametrize(
    ("overrides", "token"),
    [
        ({"expires_at": datetime.now(UTC) - timedelta(seconds=1)}, None),
        ({"not_before": datetime.now(UTC) + timedelta(minutes=1)}, None),
        ({"audience": "other"}, None),
        ({"issuer": "https://attacker.example/auth/v1"}, None),
        ({"subject": "not-a-uuid"}, None),
        ({}, "malformed.token"),
    ],
)
async def test_rejects_invalid_tokens(
    overrides: dict[str, object],
    token: str | None,
) -> None:
    verifier, private_key = _verifier_and_key()
    encoded = token or _token(private_key, **overrides)

    with pytest.raises(AuthenticationError):
        await verifier.verify(encoded)


async def test_ignores_untrusted_role_and_shop_claims() -> None:
    verifier, private_key = _verifier_and_key()
    subject = uuid4()
    token = _token(
        private_key,
        subject=str(subject),
        extra_claims={
            "role": "platform_admin",
            "business_id": str(uuid4()),
            "shop_id": str(uuid4()),
        },
    )

    identity = await verifier.verify(token)

    assert identity.auth_user_id == subject
    assert not hasattr(identity, "role")
    assert not hasattr(identity, "shop_id")


async def test_rejects_token_signed_by_another_key() -> None:
    verifier, _trusted_key = _verifier_and_key()
    attacker_key = ec.generate_private_key(ec.SECP256R1())

    with pytest.raises(AuthenticationError):
        await verifier.verify(_token(attacker_key))


async def test_missing_supabase_configuration_fails_closed() -> None:
    verifier = JwtVerifier(Settings(_env_file=None))

    with pytest.raises(AuthenticationError):
        await verifier.verify("header.payload.signature")
