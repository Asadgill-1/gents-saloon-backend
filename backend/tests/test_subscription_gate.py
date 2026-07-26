from uuid import uuid4

from app.core.entitlements import Entitlement
from telegram_bot import subscription_gate
from telegram_bot.subscription_gate import (
    SERVICE_UNAVAILABLE_MESSAGE,
    SubscriptionGateMiddleware,
    TrustedBotScope,
)


class _ConnectionContext:
    async def __aenter__(self) -> object:
        return object()

    async def __aexit__(self, *args: object) -> None:
        return None


class _Pool:
    def connection(self, timeout: int) -> _ConnectionContext:
        assert timeout == 5
        return _ConnectionContext()


async def test_suspended_bot_update_is_acknowledged_without_operation(
    monkeypatch,
) -> None:
    scope = TrustedBotScope(uuid4(), uuid4())
    calls: list[str] = []

    async def suspended(*_args: object, **_kwargs: object) -> Entitlement:
        return Entitlement("suspended", scope.business_id, scope.shop_id, uuid4())

    async def operation() -> None:
        calls.append("operation")

    async def reply(message: str) -> None:
        calls.append(message)

    monkeypatch.setattr(subscription_gate, "resolve_entitlement", suspended)
    outcome = await SubscriptionGateMiddleware(_Pool()).handle(
        scope,
        operation=operation,
        unavailable_reply=reply,
    )

    assert outcome.acknowledged is True
    assert outcome.operation_performed is False
    assert calls == [SERVICE_UNAVAILABLE_MESSAGE]


async def test_active_bot_update_reaches_operation(monkeypatch) -> None:
    scope = TrustedBotScope(uuid4(), uuid4())
    calls: list[str] = []

    async def active(*_args: object, **_kwargs: object) -> Entitlement:
        return Entitlement("active", scope.business_id, scope.shop_id, uuid4())

    async def operation() -> None:
        calls.append("operation")

    async def reply(_message: str) -> None:
        calls.append("reply")

    monkeypatch.setattr(subscription_gate, "resolve_entitlement", active)
    outcome = await SubscriptionGateMiddleware(_Pool()).handle(
        scope,
        operation=operation,
        unavailable_reply=reply,
    )

    assert outcome.operation_performed is True
    assert calls == ["operation"]


async def test_entitlement_failure_fails_closed_with_generic_reply(monkeypatch) -> None:
    scope = TrustedBotScope(uuid4(), uuid4())
    calls: list[str] = []

    async def unavailable(*_args: object, **_kwargs: object) -> Entitlement:
        raise ConnectionError

    async def operation() -> None:
        calls.append("operation")

    async def reply(message: str) -> None:
        calls.append(message)

    monkeypatch.setattr(subscription_gate, "resolve_entitlement", unavailable)
    outcome = await SubscriptionGateMiddleware(_Pool()).handle(
        scope,
        operation=operation,
        unavailable_reply=reply,
    )

    assert outcome.operation_performed is False
    assert calls == [SERVICE_UNAVAILABLE_MESSAGE]


async def test_suspended_update_stays_acknowledged_when_reply_fails(monkeypatch) -> None:
    scope = TrustedBotScope(uuid4(), uuid4())

    async def suspended(*_args: object, **_kwargs: object) -> Entitlement:
        return Entitlement("suspended", scope.business_id, scope.shop_id, uuid4())

    async def operation() -> None:
        raise AssertionError("operation must not run")

    async def failed_reply(_message: str) -> None:
        raise ConnectionError

    monkeypatch.setattr(subscription_gate, "resolve_entitlement", suspended)
    outcome = await SubscriptionGateMiddleware(_Pool()).handle(
        scope,
        operation=operation,
        unavailable_reply=failed_reply,
    )

    assert outcome.acknowledged is True
    assert outcome.operation_performed is False
    assert outcome.unavailable_reply_sent is False
