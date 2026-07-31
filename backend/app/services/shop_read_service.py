from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class BookingListItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    customer_id: UUID | None
    barber_membership_id: UUID | None
    booking_type: str
    status: str
    queue_number: int | None
    scheduled_start: datetime | None
    estimated_start_at: datetime | None
    created_at: datetime


class BookingListResponse(BaseModel):
    items: list[BookingListItem]
    next_cursor: UUID | None


class ServiceListItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    name: str
    price_gross: Decimal
    vat_rate: Decimal
    duration_minutes: int
    active: bool


class ServiceListResponse(BaseModel):
    items: list[ServiceListItem]
    next_cursor: UUID | None


class BarberListItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    display_name: str
    active: bool


class BarberListResponse(BaseModel):
    items: list[BarberListItem]
    next_cursor: UUID | None


class CashShiftListItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    register_label: str
    status: str
    opening_float: Decimal
    expected_cash: Decimal | None
    counted_cash: Decimal | None
    variance: Decimal | None
    opened_at: datetime
    closed_at: datetime | None


class CashShiftListResponse(BaseModel):
    items: list[CashShiftListItem]
    next_cursor: UUID | None


def _page(rows: list[dict[str, Any]], limit: int) -> tuple[list[dict[str, Any]], UUID | None]:
    has_more = len(rows) > limit
    items = rows[:limit]
    return items, UUID(str(items[-1]["id"])) if has_more and items else None


def _row_dict(cursor: Any, row: Any) -> dict[str, Any]:
    return {column.name: value for column, value in zip(cursor.description, row, strict=True)}


async def list_bookings(
    pool: Any,
    *,
    business_id: UUID,
    shop_id: UUID,
    cursor: UUID | None,
    limit: int,
    status: str | None,
    date_from: date | None,
    date_to: date | None,
) -> BookingListResponse:
    end = date_to + timedelta(days=1) if date_to is not None else None
    async with pool.connection(timeout=5) as connection:
        query = await connection.execute(
            """
            select id, customer_id, barber_membership_id, booking_type::text,
                   status::text, queue_number, scheduled_start, estimated_start_at,
                   created_at
            from public.bookings
            where business_id = %s and shop_id = %s
              and (%s::text is null or status::text = %s)
              and (%s::date is null or created_at >= %s::date)
              and (%s::date is null or created_at < %s::date)
              and (%s::uuid is null or id > %s)
            order by id
            limit %s
            """,
            (
                business_id,
                shop_id,
                status,
                status,
                date_from,
                date_from,
                end,
                end,
                cursor,
                cursor,
                limit + 1,
            ),
        )
        rows = [_row_dict(query, row) for row in await query.fetchall()]
    page, next_cursor = _page(rows, limit)
    return BookingListResponse(
        items=[BookingListItem.model_validate(item) for item in page],
        next_cursor=next_cursor,
    )


async def list_services(
    pool: Any,
    *,
    business_id: UUID,
    shop_id: UUID,
    cursor: UUID | None,
    limit: int,
) -> ServiceListResponse:
    async with pool.connection(timeout=5) as connection:
        query = await connection.execute(
            """
            select id, name, price_gross, vat_rate, duration_minutes, active
            from public.services
            where business_id = %s and shop_id = %s
              and (%s::uuid is null or id > %s)
            order by id
            limit %s
            """,
            (business_id, shop_id, cursor, cursor, limit + 1),
        )
        rows = [_row_dict(query, row) for row in await query.fetchall()]
    page, next_cursor = _page(rows, limit)
    return ServiceListResponse(
        items=[ServiceListItem.model_validate(item) for item in page],
        next_cursor=next_cursor,
    )


async def list_barbers(
    pool: Any,
    *,
    business_id: UUID,
    shop_id: UUID,
    cursor: UUID | None,
    limit: int,
) -> BarberListResponse:
    async with pool.connection(timeout=5) as connection:
        query = await connection.execute(
            """
            select id, display_name, active
            from public.shop_memberships
            where business_id = %s and shop_id = %s and role = 'barber'
              and (%s::uuid is null or id > %s)
            order by id
            limit %s
            """,
            (business_id, shop_id, cursor, cursor, limit + 1),
        )
        rows = [_row_dict(query, row) for row in await query.fetchall()]
    page, next_cursor = _page(rows, limit)
    return BarberListResponse(
        items=[BarberListItem.model_validate(item) for item in page],
        next_cursor=next_cursor,
    )


async def list_cash_shifts(
    pool: Any,
    *,
    business_id: UUID,
    shop_id: UUID,
    cursor: UUID | None,
    limit: int,
    state: str | None,
    register: str | None,
) -> CashShiftListResponse:
    async with pool.connection(timeout=5) as connection:
        query = await connection.execute(
            """
            select id, register_label, status::text, opening_float,
                   expected_cash, counted_cash, variance, opened_at, closed_at
            from public.cash_shifts
            where business_id = %s and shop_id = %s
              and (%s::text is null or status::text = %s)
              and (%s::text is null or lower(register_label) = lower(%s))
              and (%s::uuid is null or id > %s)
            order by id
            limit %s
            """,
            (
                business_id,
                shop_id,
                state,
                state,
                register,
                register,
                cursor,
                cursor,
                limit + 1,
            ),
        )
        rows = [_row_dict(query, row) for row in await query.fetchall()]
    page, next_cursor = _page(rows, limit)
    return CashShiftListResponse(
        items=[CashShiftListItem.model_validate(item) for item in page],
        next_cursor=next_cursor,
    )


__all__ = [
    "BarberListResponse",
    "BookingListResponse",
    "CashShiftListResponse",
    "ServiceListResponse",
    "list_barbers",
    "list_bookings",
    "list_cash_shifts",
    "list_services",
]
