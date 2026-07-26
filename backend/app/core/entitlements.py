from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

DUBAI = ZoneInfo("Asia/Dubai")
EXPIRY_TIME = time(0, 5)


class SubscriptionSuspendedError(Exception):
    """The requested tenant scope is not currently entitled."""


@dataclass(frozen=True)
class Entitlement:
    status: str
    business_id: UUID
    shop_id: UUID
    subscription_id: UUID | None

    @property
    def active(self) -> bool:
        return self.status == "active"


def coverage_deadline(paid_until: date) -> datetime:
    return datetime.combine(paid_until + timedelta(days=1), EXPIRY_TIME, DUBAI)


def has_current_coverage(
    paid_from: date,
    paid_until: date,
    *,
    at: datetime,
) -> bool:
    local_time = at.astimezone(DUBAI)
    starts_at = datetime.combine(paid_from, time.min, DUBAI)
    return starts_at <= local_time < coverage_deadline(paid_until)


async def resolve_entitlement(
    connection: Any,
    *,
    business_id: UUID,
    shop_id: UUID,
    at: datetime | None = None,
    lock: bool = False,
) -> Entitlement:
    checked_at = at or datetime.now(UTC)
    lock_clause = "for share of b, sh" if lock else ""
    cursor = await connection.execute(
        f"""
        select
          b.status::text,
          sh.status::text,
          s.id,
          s.status::text,
          s.paid_from,
          s.paid_until,
          s.manual_override_until
        from public.businesses b
        join public.shops sh
          on sh.business_id = b.id
         and sh.id = %s
        left join public.subscriptions s
          on s.business_id = b.id
         and s.status <> 'archived'
         and (
           (b.billing_mode = 'business' and s.scope = 'business' and s.shop_id is null)
           or
           (b.billing_mode = 'per_shop' and s.scope = 'shop' and s.shop_id = sh.id)
         )
        where b.id = %s
        {lock_clause}
        """,
        (shop_id, business_id),
    )
    row = await cursor.fetchone()
    if row is None:
        return Entitlement("archived", business_id, shop_id, None)

    business_status, shop_status, subscription_id, status = row[:4]
    if lock and subscription_id is not None:
        subscription_cursor = await connection.execute(
            """
            select status::text, paid_from, paid_until, manual_override_until
            from public.subscriptions
            where id = %s
            for share
            """,
            (subscription_id,),
        )
        subscription = await subscription_cursor.fetchone()
        if subscription is None:
            subscription_id = None
            status = None
            row = (*row[:4], None, None, None)
        else:
            status, paid_from, paid_until, override_until = subscription
            row = (*row[:4], paid_from, paid_until, override_until)
    if business_status in {"offboarding", "archived"}:
        return Entitlement(str(business_status), business_id, shop_id, subscription_id)
    if shop_status in {"offboarding", "archived"}:
        return Entitlement(str(shop_status), business_id, shop_id, subscription_id)
    if subscription_id is None or status in {"suspended", "offboarding", "archived"}:
        effective = "suspended" if subscription_id is None else str(status)
        return Entitlement(effective, business_id, shop_id, subscription_id)

    paid_from, paid_until, override_until = row[4:7]
    override_active = override_until is not None and checked_at < override_until
    coverage_active = has_current_coverage(paid_from, paid_until, at=checked_at)
    effective = (
        "active" if status == "active" and (coverage_active or override_active) else "expired"
    )
    return Entitlement(effective, business_id, shop_id, subscription_id)


async def require_active_entitlement(
    connection: Any,
    *,
    business_id: UUID,
    shop_id: UUID,
    at: datetime | None = None,
) -> Entitlement:
    entitlement = await resolve_entitlement(
        connection,
        business_id=business_id,
        shop_id=shop_id,
        at=at,
        lock=True,
    )
    if not entitlement.active:
        raise SubscriptionSuspendedError
    return entitlement
