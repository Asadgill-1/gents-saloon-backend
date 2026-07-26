import hashlib
import json
from typing import Any
from uuid import UUID

from psycopg.types.json import Jsonb
from pydantic import BaseModel

IDEMPOTENCY_TTL_HOURS = 24


class PlatformAdminRequiredError(Exception):
    """The authenticated user is not an active platform administrator."""


class IdempotencyConflictError(Exception):
    """An idempotency key was reused with a different request."""


class IdempotencyInProgressError(Exception):
    """An existing idempotent request has no recorded result."""


def payload_hash(payload: BaseModel) -> str:
    canonical = json.dumps(
        payload.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


async def require_platform_admin(connection: Any, actor_id: UUID) -> None:
    cursor = await connection.execute(
        """
        select 1
        from public.user_profiles up
        join public.platform_admins pa
          on pa.auth_user_id = up.auth_user_id
        where up.auth_user_id = %s
          and up.active
          and pa.active
        for share of up, pa
        """,
        (actor_id,),
    )
    if await cursor.fetchone() is None:
        raise PlatformAdminRequiredError


async def reserve_idempotency(
    connection: Any,
    *,
    scope: str,
    actor_id: UUID,
    key: str,
    payload: BaseModel,
    expected_status: int,
) -> dict[str, Any] | None:
    actor_scope = f"{scope}:{actor_id}"
    request_hash = payload_hash(payload)
    await connection.execute(
        """
        delete from public.idempotency_keys
        where scope = %s and key = %s and expires_at <= now()
        """,
        (actor_scope, key),
    )
    cursor = await connection.execute(
        """
        insert into public.idempotency_keys (
          scope, key, actor_id, request_hash, expires_at
        )
        values (%s, %s, %s, %s, now() + make_interval(hours => %s))
        on conflict (scope, key) do nothing
        returning key
        """,
        (actor_scope, key, actor_id, request_hash, IDEMPOTENCY_TTL_HOURS),
    )
    if await cursor.fetchone() is not None:
        return None

    cursor = await connection.execute(
        """
        select request_hash, response_status, response_body, completed_at
        from public.idempotency_keys
        where scope = %s and key = %s
        """,
        (actor_scope, key),
    )
    existing = await cursor.fetchone()
    if existing is None or str(existing[0]) != request_hash:
        raise IdempotencyConflictError
    if existing[3] is None or existing[1] != expected_status or existing[2] is None:
        raise IdempotencyInProgressError
    return dict(existing[2])


async def complete_idempotency(
    connection: Any,
    *,
    scope: str,
    actor_id: UUID,
    key: str,
    response_status: int,
    response: BaseModel,
) -> None:
    await connection.execute(
        """
        update public.idempotency_keys
        set response_status = %s,
            response_body = %s,
            completed_at = now()
        where scope = %s and key = %s
        """,
        (
            response_status,
            Jsonb(response.model_dump(mode="json")),
            f"{scope}:{actor_id}",
            key,
        ),
    )


async def write_platform_event(
    connection: Any,
    *,
    business_id: UUID,
    shop_id: UUID | None,
    actor_id: UUID | None,
    action: str,
    entity_type: str,
    entity_id: UUID,
    request_id: str,
    details: dict[str, Any],
    system_actor: str = "subscription-expiry-worker",
) -> None:
    actor_type = "platform_admin" if actor_id is not None else "system"
    audit_actor = str(actor_id) if actor_id is not None else system_actor
    await connection.execute(
        """
        insert into public.audit_log (
          business_id, shop_id, actor_type, actor_id, action,
          entity_type, entity_id, request_id, after
        )
        values (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            business_id,
            shop_id,
            actor_type,
            audit_actor,
            action,
            entity_type,
            entity_id,
            request_id,
            Jsonb(details),
        ),
    )
    await connection.execute(
        """
        insert into public.outbox_events (
          business_id, shop_id, topic, dedupe_key, payload
        )
        values (%s, %s, %s, %s, %s)
        on conflict (dedupe_key) do nothing
        """,
        (
            business_id,
            shop_id,
            action,
            f"{action}:{request_id}:{entity_id}",
            Jsonb(details),
        ),
    )
