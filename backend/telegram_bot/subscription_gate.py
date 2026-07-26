from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from app.core.entitlements import resolve_entitlement

SERVICE_UNAVAILABLE_MESSAGE = "Service temporarily unavailable. Please try again later."


@dataclass(frozen=True)
class TrustedBotScope:
    """Tenant scope produced only after webhook and bot-identity verification."""

    business_id: UUID
    shop_id: UUID


@dataclass(frozen=True)
class BotUpdateOutcome:
    acknowledged: bool
    operation_performed: bool
    unavailable_reply_sent: bool


class SubscriptionGateMiddleware:
    def __init__(self, pool: Any) -> None:
        self._pool = pool

    async def handle(
        self,
        scope: TrustedBotScope,
        *,
        operation: Callable[[], Awaitable[None]],
        unavailable_reply: Callable[[str], Awaitable[None]],
    ) -> BotUpdateOutcome:
        active = False
        try:
            async with self._pool.connection(timeout=5) as connection:
                entitlement = await resolve_entitlement(
                    connection,
                    business_id=scope.business_id,
                    shop_id=scope.shop_id,
                )
                active = entitlement.active
        except Exception:
            active = False

        if not active:
            reply_sent = True
            try:
                await unavailable_reply(SERVICE_UNAVAILABLE_MESSAGE)
            except Exception:
                reply_sent = False
            return BotUpdateOutcome(
                acknowledged=True,
                operation_performed=False,
                unavailable_reply_sent=reply_sent,
            )

        await operation()
        return BotUpdateOutcome(
            acknowledged=True,
            operation_performed=True,
            unavailable_reply_sent=False,
        )
