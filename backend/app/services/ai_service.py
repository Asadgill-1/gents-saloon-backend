import hashlib
import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from app.core.ai_client import MoonshotAIClient
from app.core.guardrails import GuardrailViolation, validate_and_sanitize_input
from app.services.ai_tools import (
    ALLOWLISTED_TOOLS,
    execute_allowlisted_tool,
    parse_tool_arguments,
)

AI_BUDGET_SCRIPT = """
local user_count = redis.call('INCR', KEYS[1])
if user_count == 1 then redis.call('EXPIRE', KEYS[1], 3600) end
local platform_count = redis.call('INCR', KEYS[2])
if platform_count == 1 then redis.call('EXPIRE', KEYS[2], 86400) end
return {user_count, platform_count}
"""

AI_FALLBACK = "Our assisted reception is temporarily unavailable. Please use the menu buttons."
AI_SAFE_RESPONSE = (
    "I cannot process that message. Please use the menu buttons or ask reception for help."
)


class AIBudgetUnavailableError(Exception):
    """The Redis-backed AI budget cannot be enforced."""


async def enforce_ai_budgets(
    redis: Any,
    *,
    shop_id: UUID,
    customer_id: UUID,
    hourly_limit: int,
    daily_limit: int,
) -> bool:
    subject = hashlib.sha256(f"{shop_id}:{customer_id}".encode()).hexdigest()[:32]
    day = datetime.now(UTC).date().isoformat()
    try:
        values = await redis.eval(
            AI_BUDGET_SCRIPT,
            2,
            f"ai:budget:v1:user-shop:{subject}",
            f"ai:budget:v1:platform:{day}",
        )
        user_count, platform_count = int(values[0]), int(values[1])
    except Exception as exc:
        raise AIBudgetUnavailableError from exc
    return user_count <= hourly_limit and platform_count <= daily_limit


async def _store_redacted_chat(
    pool: Any,
    *,
    business_id: UUID,
    shop_id: UUID,
    customer_id: UUID,
    telegram_user_id: int,
    content_redacted: str,
) -> None:
    async with pool.connection(timeout=5) as connection, connection.transaction():
        await connection.execute(
            """
            insert into public.chat_messages (
              business_id, shop_id, customer_id, telegram_user_id,
              sender_role, content_redacted
            ) values (%s, %s, %s, %s, 'user', %s)
            """,
            (business_id, shop_id, customer_id, telegram_user_id, content_redacted),
        )


async def _store_tool_audit(
    pool: Any,
    *,
    business_id: UUID,
    shop_id: UUID,
    customer_id: UUID,
    telegram_user_id: int,
    tool_names: list[str],
) -> None:
    if not tool_names:
        return
    async with pool.connection(timeout=5) as connection, connection.transaction():
        await connection.execute(
            """
            insert into public.chat_messages (
              business_id, shop_id, customer_id, telegram_user_id,
              sender_role, content_redacted, tool_names
            ) values (%s, %s, %s, %s, 'tool', '[authoritative tool result]', %s)
            """,
            (business_id, shop_id, customer_id, telegram_user_id, tool_names),
        )


async def handle_ai_customer_chat(
    pool: Any,
    ai_client: MoonshotAIClient | None,
    user_message: str,
    *,
    business_id: UUID,
    shop_id: UUID,
    customer_id: UUID | None,
    telegram_user_id: int | None = None,
    request_id: str | None = None,
    redis: Any = None,
    hourly_budget: int = 20,
    daily_budget: int = 5000,
) -> str:
    try:
        clean_input = validate_and_sanitize_input(user_message)
    except GuardrailViolation:
        return AI_SAFE_RESPONSE
    if not clean_input or customer_id is None or telegram_user_id is None:
        return AI_FALLBACK
    if ai_client is None:
        return AI_FALLBACK
    try:
        within_budget = await enforce_ai_budgets(
            redis,
            shop_id=shop_id,
            customer_id=customer_id,
            hourly_limit=hourly_budget,
            daily_limit=daily_budget,
        )
    except AIBudgetUnavailableError:
        return AI_FALLBACK
    if not within_budget:
        return AI_FALLBACK

    try:
        await _store_redacted_chat(
            pool,
            business_id=business_id,
            shop_id=shop_id,
            customer_id=customer_id,
            telegram_user_id=telegram_user_id,
            content_redacted=clean_input,
        )
    except Exception:
        return AI_FALLBACK

    messages: list[dict[str, Any]] = [
        {
            "role": "system",
            "content": (
                "Classify the customer's saloon request and call only the supplied tools. "
                "Never state prices, hours, slots, queue facts, booking state, or action success "
                "from your own text. Tenant and customer scope is server-injected."
            ),
        },
        {"role": "user", "content": clean_input},
    ]
    authoritative: list[str] = []
    used_tools: list[str] = []
    effective_request_id = request_id or f"ai:{uuid4()}"
    for tool_round in range(3):
        try:
            response = await ai_client.chat_completion(messages, tools=ALLOWLISTED_TOOLS)
            choice = response["choices"][0]["message"]
        except Exception:
            return AI_FALLBACK
        tool_calls = choice.get("tool_calls")
        if not isinstance(tool_calls, list) or not tool_calls:
            break
        messages.append({"role": "assistant", "content": None, "tool_calls": tool_calls})
        for tool_index, tool_call in enumerate(tool_calls):
            try:
                tool_name = str(tool_call["function"]["name"])
                raw_arguments = str(tool_call["function"].get("arguments", "{}"))
                arguments = parse_tool_arguments(tool_name, raw_arguments)
                result = await execute_allowlisted_tool(
                    pool,
                    tool_name,
                    arguments,
                    business_id=business_id,
                    shop_id=shop_id,
                    customer_id=customer_id,
                    telegram_user_id=telegram_user_id,
                    request_id=f"{effective_request_id}:{tool_round}:{tool_index}",
                )
            except Exception:
                return AI_FALLBACK
            used_tools.append(tool_name)
            authoritative.append(result.rendered)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": str(tool_call["id"]),
                    "content": json.dumps(result.data, separators=(",", ":"), default=str),
                }
            )
    try:
        await _store_tool_audit(
            pool,
            business_id=business_id,
            shop_id=shop_id,
            customer_id=customer_id,
            telegram_user_id=telegram_user_id,
            tool_names=used_tools,
        )
    except Exception:
        return AI_FALLBACK
    if not authoritative:
        return AI_FALLBACK
    return "\n\n".join(authoritative)


__all__ = [
    "AIBudgetUnavailableError",
    "AI_FALLBACK",
    "AI_SAFE_RESPONSE",
    "enforce_ai_budgets",
    "handle_ai_customer_chat",
]
