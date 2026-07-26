import asyncio
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import jwt
from jwt import PyJWKClient

from app.core.config import Settings

ALLOWED_SIGNING_ALGORITHMS = ("ES256", "RS256")


class AuthenticationError(Exception):
    """The request does not contain a valid Supabase access token."""


@dataclass(frozen=True)
class VerifiedIdentity:
    auth_user_id: UUID


class JwtVerifier:
    def __init__(
        self,
        settings: Settings,
        *,
        jwks_client: PyJWKClient | None = None,
    ) -> None:
        self._issuer = f"{settings.supabase_url.rstrip('/')}/auth/v1"
        self._audience = settings.supabase_jwt_audience
        self._configured = bool(settings.supabase_url)
        self._jwks_client = jwks_client
        if self._configured and self._jwks_client is None:
            self._jwks_client = PyJWKClient(
                f"{self._issuer}/.well-known/jwks.json",
                cache_keys=True,
                cache_jwk_set=True,
                lifespan=300,
                timeout=settings.supabase_jwks_timeout_seconds,
            )

    async def verify(self, token: str) -> VerifiedIdentity:
        if not self._configured:
            raise AuthenticationError
        return await asyncio.to_thread(self._verify_sync, token)

    def _verify_sync(self, token: str) -> VerifiedIdentity:
        try:
            if self._jwks_client is None:
                raise AuthenticationError
            signing_key = self._jwks_client.get_signing_key_from_jwt(token)
            claims: dict[str, Any] = jwt.decode(
                token,
                signing_key.key,
                algorithms=list(ALLOWED_SIGNING_ALGORITHMS),
                audience=self._audience,
                issuer=self._issuer,
                options={
                    "require": ["aud", "exp", "iss", "sub"],
                    "verify_aud": True,
                    "verify_exp": True,
                    "verify_iat": True,
                    "verify_iss": True,
                    "verify_nbf": True,
                    "verify_signature": True,
                    "verify_sub": True,
                },
            )
            return VerifiedIdentity(auth_user_id=UUID(claims["sub"]))
        except Exception as exc:
            raise AuthenticationError from exc
