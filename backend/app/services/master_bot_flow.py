from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class MasterMenuExpiredError(Exception):
    """The master callback is stale or the platform administrator is inactive."""


class MasterFlowResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str


async def _require_platform_admin(
    connection: Any,
    *,
    actor_id: UUID,
    telegram_user_id: int,
) -> None:
    cursor = await connection.execute(
        """
        select 1
        from public.platform_admins pa
        join public.user_profiles up on up.auth_user_id = pa.auth_user_id and up.active
        where pa.auth_user_id = %s and pa.telegram_user_id = %s and pa.active
          and not exists (
            select 1 from public.telegram_user_blocks tub
            where tub.telegram_user_id = pa.telegram_user_id
              and (tub.expires_at is null or tub.expires_at > now())
          )
        """,
        (actor_id, telegram_user_id),
    )
    if await cursor.fetchone() is None:
        raise MasterMenuExpiredError


def _lines(heading: str, rows: list[str], *, empty: str) -> MasterFlowResponse:
    return MasterFlowResponse(text=heading + "\n" + ("\n".join(rows) if rows else empty))


async def handle_master_callback(
    pool: Any,
    *,
    actor_id: UUID,
    telegram_user_id: int,
    callback: str,
) -> MasterFlowResponse:
    if not callback.startswith("v1."):
        raise MasterMenuExpiredError
    action = callback[3:]
    if action not in {f"m{index:02d}" for index in range(1, 11)}:
        raise MasterMenuExpiredError

    async with pool.connection(timeout=5) as connection, connection.transaction():
        await connection.execute("set local statement_timeout = '10s'")
        await _require_platform_admin(
            connection,
            actor_id=actor_id,
            telegram_user_id=telegram_user_id,
        )

        if action == "m01":
            cursor = await connection.execute(
                """
                select b.display_name, b.status::text, b.billing_mode::text,
                       count(sh.id)::integer
                from public.businesses b
                left join public.shops sh on sh.business_id = b.id
                group by b.id
                order by b.created_at desc, b.id desc limit 20
                """
            )
            rows = await cursor.fetchall()
            return _lines(
                "Businesses (latest 20)",
                [f"{row[0]}: {row[1]}, {row[2]}, {row[3]} shop(s)" for row in rows],
                empty="No business is registered.",
            )

        if action == "m02":
            cursor = await connection.execute(
                """
                select b.display_name, count(distinct sh.id)::integer,
                       count(distinct bot.id)::integer,
                       count(distinct lp.id)::integer
                from public.businesses b
                left join public.shops sh on sh.business_id = b.id and sh.status = 'active'
                left join public.bots bot on bot.business_id = b.id and bot.active
                left join public.shop_legal_profiles lp
                  on lp.business_id = b.id and lp.effective_until is null
                where b.status <> 'archived'
                group by b.id
                having count(distinct sh.id) = 0
                    or count(distinct bot.id) < count(distinct sh.id) * 4
                    or count(distinct lp.id) < count(distinct sh.id)
                order by b.created_at, b.id limit 20
                """
            )
            rows = await cursor.fetchall()
            return _lines(
                "Onboarding readiness (incomplete latest 20)",
                [
                    f"{row[0]}: {row[1]} shop(s), {row[2]}/{row[1] * 4} bots, "
                    f"{row[3]}/{row[1]} legal profiles"
                    for row in rows
                ],
                empty=(
                    "No incomplete tenant setup is visible. Use the authenticated platform "
                    "dashboard for owner invites, legal data, and bot token entry."
                ),
            )

        if action == "m03":
            cursor = await connection.execute(
                """
                select b.display_name, scr.amount, scr.currency, scr.receipt_reference,
                       scr.coverage_from, scr.coverage_until,
                       case when scr.reversal_of_id is null then 'receipt' else 'reversal' end
                from public.subscription_cash_receipts scr
                join public.businesses b on b.id = scr.business_id
                order by scr.collected_at desc, scr.id desc limit 20
                """
            )
            rows = await cursor.fetchall()
            return _lines(
                "Cash subscriptions (latest 20)",
                [
                    f"{row[0]}: {row[6]} {row[1]:.2f} {row[2]}, ref {row[3]}, {row[4]} to {row[5]}"
                    for row in rows
                ],
                empty="No cash subscription receipt is recorded.",
            )

        if action == "m04":
            cursor = await connection.execute(
                """
                select b.display_name, coalesce(sh.name, 'business-wide'),
                       sub.status::text, sub.paid_until, sub.manual_override_until
                from public.subscriptions sub
                join public.businesses b on b.id = sub.business_id
                left join public.shops sh on sh.id = sub.shop_id
                where sub.status in ('suspended', 'archived')
                   or sub.paid_until < current_date
                order by sub.paid_until, sub.id limit 20
                """
            )
            rows = await cursor.fetchall()
            return _lines(
                "Due or suspended subscriptions (first 20)",
                [
                    f"{row[0]} / {row[1]}: {row[2]}, paid until {row[3]}, override {row[4] or '-'}"
                    for row in rows
                ],
                empty="No due or suspended subscription is visible.",
            )

        if action == "m05":
            cursor = await connection.execute(
                """
                select b.display_name, oc.scope::text, oc.state, oc.requested_at,
                       oc.delivered_at, oc.archived_at
                from public.offboarding_cases oc
                join public.businesses b on b.id = oc.business_id
                order by oc.requested_at desc, oc.id desc limit 20
                """
            )
            rows = await cursor.fetchall()
            return _lines(
                "Exports and offboarding (latest 20)",
                [
                    f"{row[0]}: {row[1]} {row[2]}, requested {row[3].isoformat()}, "
                    f"delivered {row[4] or '-'}, archived {row[5] or '-'}"
                    for row in rows
                ],
                empty="No offboarding case is recorded.",
            )

        if action == "m06":
            totals_cursor = await connection.execute(
                """
                select count(*)::integer,
                       count(*) filter (where active)::integer,
                       count(*) filter (where active and healthy)::integer,
                       count(*) filter (where active and not healthy)::integer
                from public.bots
                """
            )
            totals = await totals_cursor.fetchone()
            assert totals is not None
            cursor = await connection.execute(
                """
                select coalesce(b.display_name, 'platform'), coalesce(sh.name, 'global'),
                       bot.role::text, bot.bot_username, bot.last_health_at
                from public.bots bot
                left join public.businesses b on b.id = bot.business_id
                left join public.shops sh on sh.id = bot.shop_id
                where bot.active and not bot.healthy
                order by bot.last_health_at nulls first, bot.id limit 20
                """
            )
            rows = await cursor.fetchall()
            detail = [
                f"{row[0]} / {row[1]} / {row[2]} @{row[3]}: {row[4] or 'never checked'}"
                for row in rows
            ]
            return _lines(
                (
                    f"Bot health: {totals[0]} registered, {totals[1]} active, "
                    f"{totals[2]} healthy, {totals[3]} unhealthy"
                ),
                detail,
                empty="No active unhealthy bot is visible.",
            )

        if action == "m07":
            cursor = await connection.execute(
                """
                select b.display_name, sh.name, al.created_at,
                       coalesce(al.after->>'category', 'unspecified')
                from public.audit_log al
                join public.businesses b on b.id = al.business_id
                join public.shops sh on sh.id = al.shop_id
                where al.action = 'telegram.escalation.created'
                order by al.created_at desc, al.id desc limit 20
                """
            )
            rows = await cursor.fetchall()
            return _lines(
                "Sanitized escalations (latest 20)",
                [f"{row[0]} / {row[1]}: {row[3]} at {row[2].isoformat()}" for row in rows],
                empty="No escalation is recorded.",
            )

        if action == "m08":
            cursor = await connection.execute(
                """
                select b.display_name,
                       (select count(*) from public.shops sh where sh.business_id = b.id),
                       (select count(*) from public.bots bot
                        where bot.business_id = b.id and bot.active),
                       coalesce((select sum(case when scr.reversal_of_id is null
                         then scr.amount else -scr.amount end)
                         from public.subscription_cash_receipts scr
                         where scr.business_id = b.id), 0)
                from public.businesses b
                order by b.created_at desc, b.id desc limit 20
                """
            )
            rows = await cursor.fetchall()
            return _lines(
                "Global analytics (latest 20 businesses)",
                [
                    f"{row[0]}: {row[1]} shop(s), {row[2]} active bots, "
                    f"AED {row[3]:.2f} net subscription cash"
                    for row in rows
                ],
                empty="No business analytics is available.",
            )

        if action == "m09":
            cursor = await connection.execute(
                """
                select telegram_user_id, reason, blocked_at, expires_at
                from public.telegram_user_blocks
                where expires_at is null or expires_at > now()
                order by blocked_at desc, telegram_user_id limit 20
                """
            )
            rows = await cursor.fetchall()
            return _lines(
                "Blocked Telegram users (latest 20)",
                [
                    f"{row[0]}: {row[1]}, blocked {row[2].isoformat()}, expires {row[3] or 'never'}"
                    for row in rows
                ],
                empty="No active Telegram block is recorded.",
            )

        cursor = await connection.execute(
            """
            select
              (select count(*) from public.businesses where status = 'active'),
              (select count(*) from public.businesses where status = 'suspended'),
              (select count(*) from public.telegram_updates where status = 'failed'),
              (select count(*) from public.telegram_updates where status = 'processing'),
              (select count(*) from public.outbox_events
               where status in ('pending', 'failed') and dead_at is null),
              (select count(*) from public.outbox_events where dead_at is not null),
              (select extract(epoch from (now() - min(created_at)))::integer
               from public.outbox_events
               where status in ('pending', 'failed') and dead_at is null)
            """
        )
        row = await cursor.fetchone()
        assert row is not None
        return MasterFlowResponse(
            text=(
                "Database-visible system health\n"
                f"Businesses active/suspended: {row[0]}/{row[1]}\n"
                f"Telegram failed/processing: {row[2]}/{row[3]}\n"
                f"Outbox ready/dead: {row[4]}/{row[5]}\n"
                f"Oldest ready outbox age: {row[6] if row[6] is not None else 0}s\n"
                "Redis, worker, backup, and host health require the authenticated dashboard "
                "or Grafana; no secret-bearing endpoint is exposed in Telegram."
            )
        )


__all__ = ["MasterFlowResponse", "MasterMenuExpiredError", "handle_master_callback"]
