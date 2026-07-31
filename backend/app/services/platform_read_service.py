from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.services.platform_operations import require_platform_admin


class TenantListItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    legal_name: str
    display_name: str
    billing_mode: str
    status: str
    shop_count: int
    created_at: datetime


class SubscriptionListItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    business_id: UUID
    shop_id: UUID | None
    scope: str
    status: str
    paid_from: date
    paid_until: date
    manual_override_until: datetime | None


class CashReceiptListItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    subscription_id: UUID
    business_id: UUID
    shop_id: UUID | None
    amount: Decimal
    currency: str
    receipt_reference: str
    receipt_sequence: int
    collected_at: datetime
    coverage_from: date
    coverage_until: date
    reversal_of_id: UUID | None


class OffboardingListItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    business_id: UUID
    shop_id: UUID | None
    scope: str
    reason: str
    export_id: UUID
    state: str
    requested_at: datetime
    delivered_at: datetime | None
    archived_at: datetime | None


class BotHealthListItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    business_id: UUID | None
    shop_id: UUID | None
    role: str
    bot_username: str
    active: bool
    healthy: bool
    last_health_at: datetime | None


class AnalyticsListItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    business_id: UUID
    display_name: str
    shop_count: int
    active_subscription_count: int
    bot_count: int
    unhealthy_bot_count: int
    cash_collected: Decimal


class TenantListResponse(BaseModel):
    items: list[TenantListItem]
    next_cursor: UUID | None


class SubscriptionListResponse(BaseModel):
    items: list[SubscriptionListItem]
    next_cursor: UUID | None


class CashReceiptListResponse(BaseModel):
    items: list[CashReceiptListItem]
    next_cursor: UUID | None


class OffboardingListResponse(BaseModel):
    items: list[OffboardingListItem]
    next_cursor: UUID | None


class BotHealthListResponse(BaseModel):
    items: list[BotHealthListItem]
    next_cursor: UUID | None


class AnalyticsListResponse(BaseModel):
    items: list[AnalyticsListItem]
    next_cursor: UUID | None


def _row_dict(cursor: Any, row: Any) -> dict[str, Any]:
    return {column.name: value for column, value in zip(cursor.description, row, strict=True)}


def _page(rows: list[dict[str, Any]], limit: int) -> tuple[list[dict[str, Any]], UUID | None]:
    page = rows[:limit]
    next_cursor = UUID(str(page[-1]["id"])) if len(rows) > limit and page else None
    return page, next_cursor


async def _query(
    pool: Any,
    *,
    actor_id: UUID,
    sql: str,
    params: tuple[Any, ...],
) -> list[dict[str, Any]]:
    async with pool.connection(timeout=5) as connection, connection.transaction():
        await connection.execute("set local statement_timeout = '10s'")
        await require_platform_admin(connection, actor_id)
        cursor = await connection.execute(sql, params)
        return [_row_dict(cursor, row) for row in await cursor.fetchall()]


async def list_tenants(
    pool: Any, *, actor_id: UUID, cursor: UUID | None, limit: int
) -> TenantListResponse:
    rows = await _query(
        pool,
        actor_id=actor_id,
        sql="""
            select b.id, b.legal_name, b.display_name, b.billing_mode::text,
                   b.status::text, count(sh.id)::integer as shop_count, b.created_at
            from public.businesses b
            left join public.shops sh on sh.business_id = b.id
            where (%s::uuid is null or b.id > %s)
            group by b.id
            order by b.id
            limit %s
        """,
        params=(cursor, cursor, limit + 1),
    )
    page, next_cursor = _page(rows, limit)
    return TenantListResponse(
        items=[TenantListItem.model_validate(row) for row in page],
        next_cursor=next_cursor,
    )


async def list_subscriptions(
    pool: Any, *, actor_id: UUID, cursor: UUID | None, limit: int
) -> SubscriptionListResponse:
    rows = await _query(
        pool,
        actor_id=actor_id,
        sql="""
            select id, business_id, shop_id, scope::text, status::text,
                   paid_from, paid_until, manual_override_until
            from public.subscriptions
            where (%s::uuid is null or id > %s)
            order by id
            limit %s
        """,
        params=(cursor, cursor, limit + 1),
    )
    page, next_cursor = _page(rows, limit)
    return SubscriptionListResponse(
        items=[SubscriptionListItem.model_validate(row) for row in page],
        next_cursor=next_cursor,
    )


async def list_cash_receipts(
    pool: Any, *, actor_id: UUID, cursor: UUID | None, limit: int
) -> CashReceiptListResponse:
    rows = await _query(
        pool,
        actor_id=actor_id,
        sql="""
            select id, subscription_id, business_id, shop_id, amount, currency,
                   receipt_reference, receipt_sequence, collected_at,
                   coverage_from, coverage_until, reversal_of_id
            from public.subscription_cash_receipts
            where (%s::uuid is null or id > %s)
            order by id
            limit %s
        """,
        params=(cursor, cursor, limit + 1),
    )
    page, next_cursor = _page(rows, limit)
    return CashReceiptListResponse(
        items=[CashReceiptListItem.model_validate(row) for row in page],
        next_cursor=next_cursor,
    )


async def list_offboarding_cases(
    pool: Any, *, actor_id: UUID, cursor: UUID | None, limit: int
) -> OffboardingListResponse:
    rows = await _query(
        pool,
        actor_id=actor_id,
        sql="""
            select id, business_id, shop_id, scope::text, reason, export_id,
                   state, requested_at, delivered_at, archived_at
            from public.offboarding_cases
            where (%s::uuid is null or id > %s)
            order by id
            limit %s
        """,
        params=(cursor, cursor, limit + 1),
    )
    page, next_cursor = _page(rows, limit)
    return OffboardingListResponse(
        items=[OffboardingListItem.model_validate(row) for row in page],
        next_cursor=next_cursor,
    )


async def list_bot_health(
    pool: Any, *, actor_id: UUID, cursor: UUID | None, limit: int
) -> BotHealthListResponse:
    rows = await _query(
        pool,
        actor_id=actor_id,
        sql="""
            select id, business_id, shop_id, role::text, bot_username,
                   active, healthy, last_health_at
            from public.bots
            where (%s::uuid is null or id > %s)
            order by id
            limit %s
        """,
        params=(cursor, cursor, limit + 1),
    )
    page, next_cursor = _page(rows, limit)
    return BotHealthListResponse(
        items=[BotHealthListItem.model_validate(row) for row in page],
        next_cursor=next_cursor,
    )


async def list_analytics(
    pool: Any, *, actor_id: UUID, cursor: UUID | None, limit: int
) -> AnalyticsListResponse:
    rows = await _query(
        pool,
        actor_id=actor_id,
        sql="""
            select b.id, b.id as business_id, b.display_name,
                   (select count(*)::integer from public.shops sh
                    where sh.business_id = b.id) as shop_count,
                   (select count(*)::integer from public.subscriptions sub
                    where sub.business_id = b.id and sub.status = 'active')
                     as active_subscription_count,
                   (select count(*)::integer from public.bots bot
                    where bot.business_id = b.id) as bot_count,
                   (select count(*)::integer from public.bots bot
                    where bot.business_id = b.id and bot.active and not bot.healthy)
                     as unhealthy_bot_count,
                   coalesce((
                     select sum(case when receipt.reversal_of_id is null
                       then receipt.amount else -receipt.amount end)
                     from public.subscription_cash_receipts receipt
                     where receipt.business_id = b.id
                   ), 0) as cash_collected
            from public.businesses b
            where (%s::uuid is null or b.id > %s)
            order by b.id
            limit %s
        """,
        params=(cursor, cursor, limit + 1),
    )
    page, next_cursor = _page(rows, limit)
    return AnalyticsListResponse(
        items=[AnalyticsListItem.model_validate(row) for row in page],
        next_cursor=next_cursor,
    )


__all__ = [
    "AnalyticsListResponse",
    "BotHealthListResponse",
    "CashReceiptListResponse",
    "OffboardingListResponse",
    "SubscriptionListResponse",
    "TenantListResponse",
    "list_analytics",
    "list_bot_health",
    "list_cash_receipts",
    "list_offboarding_cases",
    "list_subscriptions",
    "list_tenants",
]
