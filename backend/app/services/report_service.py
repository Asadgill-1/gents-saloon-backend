from datetime import UTC, datetime, time, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict

from app.core.entitlements import (
    SubscriptionSuspendedError,
    has_current_coverage,
    resolve_entitlement,
)

MAX_REPORT_PERIOD = timedelta(days=366)


class ReportAccessDeniedError(Exception):
    """The actor cannot read the requested report scope."""


class ReportInputError(Exception):
    """The requested report range or cursor is invalid."""


class ReportTotals(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bookings_created: int
    bookings_completed: int
    created_cancelled: int
    created_no_show: int
    sale_count: int
    sale_service_gross: Decimal
    sale_net: Decimal
    sale_vat: Decimal
    sale_tip: Decimal
    sale_grand: Decimal
    correction_count: int
    correction_service_gross: Decimal
    correction_net: Decimal
    correction_vat: Decimal
    correction_tip: Decimal
    correction_grand: Decimal
    net_service_gross: Decimal
    net_vat: Decimal
    net_tip: Decimal
    net_grand: Decimal
    cash_tender: Decimal
    card_tender: Decimal
    cash_refunds: Decimal
    card_refunds: Decimal
    cash_sales: Decimal
    pay_ins: Decimal
    pay_outs: Decimal
    advance_cash: Decimal
    payout_cash: Decimal
    refund_cash: Decimal
    closed_shifts: int
    shift_variance: Decimal
    advances_granted: Decimal
    advance_outstanding: Decimal
    payout_gross: Decimal
    payout_advance_deduction: Decimal
    payout_net_paid: Decimal
    journal_debit: Decimal
    journal_credit: Decimal
    journal_balanced: bool


class BarberReportRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    barber_membership_id: UUID
    display_name: str
    active: bool
    service_gross: Decimal
    commission_earnings: Decimal
    tip_earnings: Decimal
    commission_reversals: Decimal
    tip_reversals: Decimal
    advances_granted: Decimal
    advance_deducted: Decimal
    payout_net_paid: Decimal
    advance_outstanding: Decimal


class ShopReportResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    business_id: UUID
    shop_id: UUID
    period_start: datetime
    period_end: datetime
    totals: ReportTotals
    barbers: list[BarberReportRow]
    next_cursor: UUID | None


class ShopOverviewRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    shop_id: UUID
    shop_name: str
    sale_count: int
    correction_count: int
    net_grand: Decimal
    payout_net_paid: Decimal
    advance_outstanding: Decimal


class BusinessOverviewResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    business_id: UUID
    period_start: datetime
    period_end: datetime
    totals: ReportTotals
    shops: list[ShopOverviewRow]
    next_cursor: UUID | None


SUMMARY_SQL = """
with params as (
  select
    %s::uuid as business_id,
    %s::uuid[] as shop_ids,
    %s::timestamptz as period_start,
    %s::timestamptz as period_end
),
booking_created_totals as (
  select
    count(b.id) as bookings_created,
    count(b.id) filter (where b.status = 'cancelled') as created_cancelled,
    count(b.id) filter (where b.status = 'no_show') as created_no_show
  from params p
  left join public.bookings b
    on b.business_id = p.business_id
   and b.shop_id = any(p.shop_ids)
   and b.created_at >= p.period_start
   and b.created_at < p.period_end
),
booking_completed_totals as (
  select count(b.id) as bookings_completed
  from params p
  left join public.bookings b
    on b.business_id = p.business_id
   and b.shop_id = any(p.shop_ids)
   and b.completed_at >= p.period_start
   and b.completed_at < p.period_end
),
sales as (
  select
    count(t.id) as sale_count,
    coalesce(sum(t.service_gross_total), 0) as sale_service_gross,
    coalesce(sum(t.net_total), 0) as sale_net,
    coalesce(sum(t.vat_total), 0) as sale_vat,
    coalesce(sum(t.tip_total), 0) as sale_tip,
    coalesce(sum(t.grand_total), 0) as sale_grand
  from params p
  left join public.transactions t
    on t.business_id = p.business_id
   and t.shop_id = any(p.shop_ids)
   and t.created_at >= p.period_start
   and t.created_at < p.period_end
),
corrections as (
  select
    count(tc.id) as correction_count,
    coalesce(sum(tc.service_gross_refund), 0) as correction_service_gross,
    coalesce(sum(tc.net_refund), 0) as correction_net,
    coalesce(sum(tc.vat_refund), 0) as correction_vat,
    coalesce(sum(tc.tip_refund), 0) as correction_tip,
    coalesce(sum(tc.grand_total), 0) as correction_grand
  from params p
  left join public.transaction_corrections tc
    on tc.business_id = p.business_id
   and tc.shop_id = any(p.shop_ids)
   and tc.created_at >= p.period_start
   and tc.created_at < p.period_end
),
sale_tenders as (
  select
    coalesce(sum(tp.amount) filter (where tp.method = 'cash'), 0) as cash_tender,
    coalesce(sum(tp.amount) filter (where tp.method = 'card'), 0) as card_tender
  from params p
  left join public.transactions t
    on t.business_id = p.business_id
   and t.shop_id = any(p.shop_ids)
   and t.created_at >= p.period_start
   and t.created_at < p.period_end
  left join public.transaction_payments tp on tp.transaction_id = t.id
),
refund_tenders as (
  select
    coalesce(sum(tcp.amount) filter (where tcp.method = 'cash'), 0) as cash_refunds,
    coalesce(sum(tcp.amount) filter (where tcp.method = 'card'), 0) as card_refunds
  from params p
  left join public.transaction_corrections tc
    on tc.business_id = p.business_id
   and tc.shop_id = any(p.shop_ids)
   and tc.created_at >= p.period_start
   and tc.created_at < p.period_end
  left join public.transaction_correction_payments tcp
    on tcp.correction_id = tc.id
),
cash_totals as (
  select
    coalesce(sum(csm.amount) filter (where csm.movement_type = 'cash_sale'), 0)
      as cash_sales,
    coalesce(sum(csm.amount) filter (where csm.movement_type = 'pay_in'), 0)
      as pay_ins,
    coalesce(sum(csm.amount) filter (where csm.movement_type = 'pay_out'), 0)
      as pay_outs,
    coalesce(sum(csm.amount) filter (where csm.movement_type = 'advance'), 0)
      as advance_cash,
    coalesce(sum(csm.amount) filter (where csm.movement_type = 'payout'), 0)
      as payout_cash,
    coalesce(sum(csm.amount) filter (where csm.movement_type = 'refund'), 0)
      as refund_cash
  from params p
  left join public.cash_shift_movements csm
    on csm.business_id = p.business_id
   and csm.shop_id = any(p.shop_ids)
   and csm.created_at >= p.period_start
   and csm.created_at < p.period_end
),
shift_totals as (
  select
    count(cs.id) as closed_shifts,
    coalesce(sum(cs.variance), 0) as shift_variance
  from params p
  left join public.cash_shifts cs
    on cs.business_id = p.business_id
   and cs.shop_id = any(p.shop_ids)
   and cs.status = 'closed'
   and cs.closed_at >= p.period_start
   and cs.closed_at < p.period_end
),
advance_totals as (
  select
    coalesce(sum(a.original_amount) filter (
      where a.given_at >= p.period_start and a.given_at < p.period_end
    ), 0) as advances_granted,
    coalesce(sum(a.outstanding_amount), 0) as advance_outstanding
  from params p
  left join public.advances a
    on a.business_id = p.business_id and a.shop_id = any(p.shop_ids)
),
payout_totals as (
  select
    coalesce(sum(pi.gross_payable), 0) as payout_gross,
    coalesce(sum(pi.advance_deduction), 0) as payout_advance_deduction,
    coalesce(sum(pi.net_paid), 0) as payout_net_paid
  from params p
  left join public.payout_runs pr
    on pr.business_id = p.business_id
   and pr.shop_id = any(p.shop_ids)
   and pr.status = 'paid'
   and pr.paid_at >= p.period_start
   and pr.paid_at < p.period_end
  left join public.payout_items pi on pi.payout_run_id = pr.id
),
journal_totals as (
  select
    coalesce(sum(jp.debit), 0) as journal_debit,
    coalesce(sum(jp.credit), 0) as journal_credit
  from params p
  left join public.journal_entries je
    on je.business_id = p.business_id
   and je.shop_id = any(p.shop_ids)
   and je.created_at >= p.period_start
   and je.created_at < p.period_end
  left join public.journal_postings jp on jp.journal_entry_id = je.id
)
select
  bct.bookings_created,
  bft.bookings_completed,
  bct.created_cancelled,
  bct.created_no_show,
  s.sale_count,
  s.sale_service_gross,
  s.sale_net,
  s.sale_vat,
  s.sale_tip,
  s.sale_grand,
  c.correction_count,
  c.correction_service_gross,
  c.correction_net,
  c.correction_vat,
  c.correction_tip,
  c.correction_grand,
  s.sale_service_gross - c.correction_service_gross as net_service_gross,
  s.sale_vat - c.correction_vat as net_vat,
  s.sale_tip - c.correction_tip as net_tip,
  s.sale_grand - c.correction_grand as net_grand,
  st.cash_tender,
  st.card_tender,
  rt.cash_refunds,
  rt.card_refunds,
  ct.cash_sales,
  ct.pay_ins,
  ct.pay_outs,
  ct.advance_cash,
  ct.payout_cash,
  ct.refund_cash,
  sht.closed_shifts,
  sht.shift_variance,
  at.advances_granted,
  at.advance_outstanding,
  pt.payout_gross,
  pt.payout_advance_deduction,
  pt.payout_net_paid,
  jt.journal_debit,
  jt.journal_credit,
  jt.journal_debit = jt.journal_credit as journal_balanced
from booking_created_totals bct
cross join booking_completed_totals bft
cross join sales s
cross join corrections c
cross join sale_tenders st
cross join refund_tenders rt
cross join cash_totals ct
cross join shift_totals sht
cross join advance_totals at
cross join payout_totals pt
cross join journal_totals jt
"""


BARBER_ROWS_SQL = """
with params as (
  select
    %s::uuid as business_id,
    %s::uuid as shop_id,
    %s::timestamptz as period_start,
    %s::timestamptz as period_end,
    %s::uuid as after_id
),
sales as (
  select
    t.barber_membership_id,
    sum(t.service_gross_total) as service_gross,
    sum(t.tip_total) as tip_earnings
  from public.transactions t, params p
  where t.business_id = p.business_id
    and t.shop_id = p.shop_id
    and t.created_at >= p.period_start
    and t.created_at < p.period_end
  group by t.barber_membership_id
),
commissions as (
  select
    tic.barber_membership_id,
    sum(tic.barber_commission) as commission_earnings
  from public.transaction_item_commissions tic
  join public.transactions t on t.id = tic.transaction_id
  cross join params p
  where t.business_id = p.business_id
    and t.shop_id = p.shop_id
    and t.created_at >= p.period_start
    and t.created_at < p.period_end
  group by tic.barber_membership_id
),
corrections as (
  select
    tc.barber_membership_id,
    sum(tc.tip_refund) as tip_reversals
  from public.transaction_corrections tc, params p
  where tc.business_id = p.business_id
    and tc.shop_id = p.shop_id
    and tc.created_at >= p.period_start
    and tc.created_at < p.period_end
  group by tc.barber_membership_id
),
commission_reversals as (
  select
    tcic.barber_membership_id,
    sum(tcic.barber_commission_refund) as commission_reversals
  from public.transaction_correction_item_commissions tcic
  join public.transaction_corrections tc on tc.id = tcic.correction_id
  cross join params p
  where tc.business_id = p.business_id
    and tc.shop_id = p.shop_id
    and tc.created_at >= p.period_start
    and tc.created_at < p.period_end
  group by tcic.barber_membership_id
),
advances as (
  select
    a.barber_membership_id,
    coalesce(sum(a.original_amount) filter (
      where a.given_at >= p.period_start and a.given_at < p.period_end
    ), 0) as advances_granted,
    sum(a.outstanding_amount) as advance_outstanding
  from public.advances a, params p
  where a.business_id = p.business_id and a.shop_id = p.shop_id
  group by a.barber_membership_id
),
payouts as (
  select
    pi.barber_membership_id,
    sum(pi.advance_deduction) as advance_deducted,
    sum(pi.net_paid) as payout_net_paid
  from public.payout_runs pr
  join public.payout_items pi on pi.payout_run_id = pr.id
  cross join params p
  where pr.business_id = p.business_id
    and pr.shop_id = p.shop_id
    and pr.status = 'paid'
    and pr.paid_at >= p.period_start
    and pr.paid_at < p.period_end
  group by pi.barber_membership_id
)
select
  sm.id as barber_membership_id,
  sm.display_name,
  sm.active,
  coalesce(s.service_gross, 0) as service_gross,
  coalesce(cm.commission_earnings, 0) as commission_earnings,
  coalesce(s.tip_earnings, 0) as tip_earnings,
  coalesce(cr.commission_reversals, 0) as commission_reversals,
  coalesce(c.tip_reversals, 0) as tip_reversals,
  coalesce(a.advances_granted, 0) as advances_granted,
  coalesce(po.advance_deducted, 0) as advance_deducted,
  coalesce(po.payout_net_paid, 0) as payout_net_paid,
  coalesce(a.advance_outstanding, 0) as advance_outstanding
from public.shop_memberships sm
cross join params p
left join sales s on s.barber_membership_id = sm.id
left join commissions cm on cm.barber_membership_id = sm.id
left join corrections c on c.barber_membership_id = sm.id
left join commission_reversals cr on cr.barber_membership_id = sm.id
left join advances a on a.barber_membership_id = sm.id
left join payouts po on po.barber_membership_id = sm.id
where sm.business_id = p.business_id
  and sm.shop_id = p.shop_id
  and sm.role = 'barber'
  and (p.after_id is null or sm.id > p.after_id)
order by sm.id
limit %s
"""


SHOP_OVERVIEW_ROWS_SQL = """
with params as (
  select
    %s::uuid as business_id,
    %s::uuid[] as shop_ids,
    %s::timestamptz as period_start,
    %s::timestamptz as period_end,
    %s::uuid as after_id
),
shop_page as (
  select s.id, s.name
  from public.shops s, params p
  where s.business_id = p.business_id
    and s.id = any(p.shop_ids)
    and (p.after_id is null or s.id > p.after_id)
  order by s.id
  limit %s
),
sales as (
  select t.shop_id, count(*) as sale_count, sum(t.grand_total) as sale_grand
  from public.transactions t, params p
  where t.business_id = p.business_id
    and t.shop_id = any(p.shop_ids)
    and t.created_at >= p.period_start
    and t.created_at < p.period_end
  group by t.shop_id
),
corrections as (
  select
    tc.shop_id,
    count(*) as correction_count,
    sum(tc.grand_total) as correction_grand
  from public.transaction_corrections tc, params p
  where tc.business_id = p.business_id
    and tc.shop_id = any(p.shop_ids)
    and tc.created_at >= p.period_start
    and tc.created_at < p.period_end
  group by tc.shop_id
),
payouts as (
  select pr.shop_id, sum(pi.net_paid) as payout_net_paid
  from public.payout_runs pr
  join public.payout_items pi on pi.payout_run_id = pr.id
  cross join params p
  where pr.business_id = p.business_id
    and pr.shop_id = any(p.shop_ids)
    and pr.status = 'paid'
    and pr.paid_at >= p.period_start
    and pr.paid_at < p.period_end
  group by pr.shop_id
),
advances as (
  select a.shop_id, sum(a.outstanding_amount) as advance_outstanding
  from public.advances a, params p
  where a.business_id = p.business_id and a.shop_id = any(p.shop_ids)
  group by a.shop_id
)
select
  sp.id as shop_id,
  sp.name as shop_name,
  coalesce(s.sale_count, 0) as sale_count,
  coalesce(c.correction_count, 0) as correction_count,
  coalesce(s.sale_grand, 0) - coalesce(c.correction_grand, 0) as net_grand,
  coalesce(po.payout_net_paid, 0) as payout_net_paid,
  coalesce(a.advance_outstanding, 0) as advance_outstanding
from shop_page sp
left join sales s on s.shop_id = sp.id
left join corrections c on c.shop_id = sp.id
left join payouts po on po.shop_id = sp.id
left join advances a on a.shop_id = sp.id
order by sp.id
"""


def _validated_period(
    period_start: datetime,
    period_end: datetime,
) -> tuple[datetime, datetime]:
    if period_start.tzinfo is None or period_end.tzinfo is None:
        raise ReportInputError("report timestamps require a timezone")
    start = period_start.astimezone(UTC)
    end = period_end.astimezone(UTC)
    if end <= start or end - start > MAX_REPORT_PERIOD:
        raise ReportInputError("report period must be positive and at most 366 days")
    return start, end


def _row_dict(cursor: Any, row: tuple[Any, ...]) -> dict[str, Any]:
    columns = [column.name for column in cursor.description or ()]
    return dict(zip(columns, row, strict=True))


async def _require_shop_report_access(
    connection: Any,
    *,
    actor_id: UUID,
    business_id: UUID,
    shop_id: UUID,
) -> None:
    cursor = await connection.execute(
        """
        select 1
        from public.user_profiles up
        join public.businesses b on b.id = %s and b.status = 'active'
        join public.shops s
          on s.id = %s and s.business_id = b.id and s.status = 'active'
        where up.auth_user_id = %s
          and up.active
          and (
            exists (
              select 1 from public.platform_admins pa
              where pa.auth_user_id = up.auth_user_id and pa.active
            )
            or exists (
              select 1 from public.business_owners bo
              where bo.business_id = b.id
                and bo.auth_user_id = up.auth_user_id
                and bo.active
            )
            or exists (
              select 1 from public.shop_memberships sm
              where sm.business_id = b.id
                and sm.shop_id = s.id
                and sm.auth_user_id = up.auth_user_id
                and sm.active
                and sm.role = 'manager'
            )
          )
        for share of up, b, s
        """,
        (business_id, shop_id, actor_id),
    )
    if await cursor.fetchone() is None:
        raise ReportAccessDeniedError


async def _require_business_report_access(
    connection: Any,
    *,
    actor_id: UUID,
    business_id: UUID,
) -> list[UUID]:
    cursor = await connection.execute(
        """
        select 1
        from public.user_profiles up
        join public.businesses b on b.id = %s and b.status = 'active'
        where up.auth_user_id = %s
          and up.active
          and (
            exists (
              select 1 from public.platform_admins pa
              where pa.auth_user_id = up.auth_user_id and pa.active
            )
            or exists (
              select 1 from public.business_owners bo
              where bo.business_id = b.id
                and bo.auth_user_id = up.auth_user_id
                and bo.active
            )
          )
        for share of up, b
        """,
        (business_id, actor_id),
    )
    if await cursor.fetchone() is None:
        raise ReportAccessDeniedError
    cursor = await connection.execute(
        """
        select
          sh.id,
          sub.status::text,
          sub.paid_from,
          sub.paid_until,
          sub.manual_override_until
        from public.businesses b
        join public.shops sh
          on sh.business_id = b.id and sh.status = 'active'
        left join public.subscriptions sub
          on sub.business_id = b.id
         and sub.status <> 'archived'
         and (
           (
             b.billing_mode = 'business'
             and sub.scope = 'business'
             and sub.shop_id is null
           )
           or
           (
             b.billing_mode = 'per_shop'
             and sub.scope = 'shop'
             and sub.shop_id = sh.id
           )
         )
        where b.id = %s
        order by sh.id
        """,
        (business_id,),
    )
    checked_at = datetime.now(UTC)
    shop_ids = [
        UUID(str(row[0]))
        for row in await cursor.fetchall()
        if row[1] == "active"
        and (
            has_current_coverage(row[2], row[3], at=checked_at)
            or (row[4] is not None and checked_at < row[4])
        )
    ]
    if not shop_ids:
        raise SubscriptionSuspendedError
    return shop_ids


async def _report_totals(
    connection: Any,
    *,
    business_id: UUID,
    shop_ids: list[UUID],
    period_start: datetime,
    period_end: datetime,
) -> ReportTotals:
    cursor = await connection.execute(
        SUMMARY_SQL,
        (business_id, shop_ids, period_start, period_end),
    )
    row = await cursor.fetchone()
    if row is None:
        raise RuntimeError("report totals query returned no row")
    return ReportTotals.model_validate(_row_dict(cursor, row))


async def get_shop_report(
    pool: Any,
    *,
    actor_id: UUID,
    business_id: UUID,
    shop_id: UUID,
    period_start: datetime,
    period_end: datetime,
    cursor: UUID | None,
    limit: int,
) -> ShopReportResponse:
    start, end = _validated_period(period_start, period_end)
    async with pool.connection(timeout=5) as connection, connection.transaction():
        await connection.execute("set transaction isolation level repeatable read")
        await connection.execute("set local statement_timeout = '10s'")
        await _require_shop_report_access(
            connection,
            actor_id=actor_id,
            business_id=business_id,
            shop_id=shop_id,
        )
        entitlement = await resolve_entitlement(
            connection,
            business_id=business_id,
            shop_id=shop_id,
            at=datetime.now(UTC),
        )
        if not entitlement.active:
            raise SubscriptionSuspendedError
        totals = await _report_totals(
            connection,
            business_id=business_id,
            shop_ids=[shop_id],
            period_start=start,
            period_end=end,
        )
        row_cursor = await connection.execute(
            BARBER_ROWS_SQL,
            (business_id, shop_id, start, end, cursor, limit + 1),
        )
        rows = [_row_dict(row_cursor, row) for row in await row_cursor.fetchall()]
    has_more = len(rows) > limit
    page = rows[:limit]
    barbers = [BarberReportRow.model_validate(row) for row in page]
    next_cursor = barbers[-1].barber_membership_id if has_more and barbers else None
    return ShopReportResponse(
        business_id=business_id,
        shop_id=shop_id,
        period_start=start,
        period_end=end,
        totals=totals,
        barbers=barbers,
        next_cursor=next_cursor,
    )


async def get_reception_eod_report(
    pool: Any,
    *,
    actor_id: UUID,
    business_id: UUID,
    shop_id: UUID,
    at: datetime | None = None,
) -> ShopReportResponse:
    checked_at = at or datetime.now(UTC)
    async with pool.connection(timeout=5) as connection, connection.transaction():
        await connection.execute("set transaction isolation level repeatable read")
        await connection.execute("set local statement_timeout = '10s'")
        cursor = await connection.execute(
            """
            select sh.timezone
            from public.user_profiles up
            join public.shop_memberships sm
              on sm.auth_user_id = up.auth_user_id and sm.active
             and sm.role in ('manager', 'receptionist')
            join public.shops sh
              on sh.id = sm.shop_id and sh.business_id = sm.business_id
             and sh.status = 'active'
            join public.businesses b on b.id = sh.business_id and b.status = 'active'
            where up.auth_user_id = %s and up.active
              and sm.business_id = %s and sm.shop_id = %s
            """,
            (actor_id, business_id, shop_id),
        )
        row = await cursor.fetchone()
        if row is None:
            raise ReportAccessDeniedError
        entitlement = await resolve_entitlement(
            connection,
            business_id=business_id,
            shop_id=shop_id,
            at=checked_at,
        )
        if not entitlement.active:
            raise SubscriptionSuspendedError
        try:
            shop_timezone = ZoneInfo(str(row[0]))
        except ZoneInfoNotFoundError as exc:
            raise ReportInputError("shop timezone is invalid") from exc
        local_day = checked_at.astimezone(shop_timezone).date()
        period_start = datetime.combine(local_day, time.min, tzinfo=shop_timezone).astimezone(UTC)
        period_end = datetime.combine(
            local_day + timedelta(days=1), time.min, tzinfo=shop_timezone
        ).astimezone(UTC)
        totals = await _report_totals(
            connection,
            business_id=business_id,
            shop_ids=[shop_id],
            period_start=period_start,
            period_end=period_end,
        )
    return ShopReportResponse(
        business_id=business_id,
        shop_id=shop_id,
        period_start=period_start,
        period_end=period_end,
        totals=totals,
        barbers=[],
        next_cursor=None,
    )


async def get_business_overview(
    pool: Any,
    *,
    actor_id: UUID,
    business_id: UUID,
    period_start: datetime,
    period_end: datetime,
    cursor: UUID | None,
    limit: int,
) -> BusinessOverviewResponse:
    start, end = _validated_period(period_start, period_end)
    async with pool.connection(timeout=5) as connection, connection.transaction():
        await connection.execute("set transaction isolation level repeatable read")
        await connection.execute("set local statement_timeout = '10s'")
        shop_ids = await _require_business_report_access(
            connection,
            actor_id=actor_id,
            business_id=business_id,
        )
        totals = await _report_totals(
            connection,
            business_id=business_id,
            shop_ids=shop_ids,
            period_start=start,
            period_end=end,
        )
        row_cursor = await connection.execute(
            SHOP_OVERVIEW_ROWS_SQL,
            (business_id, shop_ids, start, end, cursor, limit + 1),
        )
        rows = [_row_dict(row_cursor, row) for row in await row_cursor.fetchall()]
    has_more = len(rows) > limit
    page = rows[:limit]
    shops = [ShopOverviewRow.model_validate(row) for row in page]
    next_cursor = shops[-1].shop_id if has_more and shops else None
    return BusinessOverviewResponse(
        business_id=business_id,
        period_start=start,
        period_end=end,
        totals=totals,
        shops=shops,
        next_cursor=next_cursor,
    )


__all__ = [
    "BusinessOverviewResponse",
    "ReportAccessDeniedError",
    "ReportInputError",
    "ShopReportResponse",
    "get_business_overview",
    "get_reception_eod_report",
    "get_shop_report",
]
