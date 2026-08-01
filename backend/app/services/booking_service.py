from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from psycopg.errors import ExclusionViolation, ForeignKeyViolation
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.entitlements import SubscriptionSuspendedError, require_active_entitlement
from app.services.platform_operations import (
    IdempotencyConflictError,
    IdempotencyInProgressError,
    complete_idempotency,
    reserve_idempotency,
)

HOLD_DURATION = timedelta(minutes=5)
PROMOTION_WINDOW = timedelta(minutes=30)
MAX_SERVICES = 20


class BookingAccessDeniedError(Exception):
    """The actor cannot operate bookings in this shop."""


class BookingNotFoundError(Exception):
    """The booking does not exist in the authorized shop."""


class BookingConflictError(Exception):
    """The requested slot or state is no longer available."""


class BookingInputError(Exception):
    """The requested booking data does not match shop configuration."""


class BookingCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    booking_type: Literal["appointment", "queue", "walk_in"]
    customer_id: UUID | None = None
    barber_membership_id: UUID | None = None
    service_ids: list[UUID] = Field(min_length=1, max_length=MAX_SERVICES)
    scheduled_start: datetime | None = None

    @field_validator("service_ids")
    @classmethod
    def unique_services(cls, value: list[UUID]) -> list[UUID]:
        if len(value) != len(set(value)):
            raise ValueError("service_ids must be unique")
        return value

    @field_validator("scheduled_start")
    @classmethod
    def timezone_aware(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("scheduled_start must include a timezone")
        return value

    @model_validator(mode="after")
    def booking_shape(self) -> "BookingCreateRequest":
        if self.booking_type == "appointment" and self.scheduled_start is None:
            raise ValueError("appointments require scheduled_start")
        if self.booking_type != "appointment" and self.scheduled_start is not None:
            raise ValueError("queue and walk-in bookings cannot supply scheduled_start")
        return self


class BookingTransitionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str | None = Field(default=None, min_length=3, max_length=500)


class BookingRescheduleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scheduled_start: datetime
    barber_membership_id: UUID | None = None

    @field_validator("scheduled_start")
    @classmethod
    def timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("scheduled_start must include a timezone")
        return value


class BookingResponse(BaseModel):
    booking_id: UUID
    business_id: UUID
    shop_id: UUID
    customer_id: UUID | None
    barber_membership_id: UUID
    booking_type: str
    status: str
    queue_business_date: date | None
    queue_number: int | None
    scheduled_start: datetime | None
    scheduled_end: datetime | None
    hold_expires_at: datetime | None
    estimated_start_at: datetime | None
    rescheduled_from_booking_id: UUID | None


class BookingSlot(BaseModel):
    starts_at: datetime
    barber_membership_id: UUID


async def _lock_shop(connection: Any, shop_id: UUID) -> None:
    # ponytail: one lock per shop; split by barber/counter only after measured contention.
    await connection.execute(
        "select pg_advisory_xact_lock(hashtextextended(%s, 0))",
        (str(shop_id),),
    )


async def _require_operator(
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
        where up.auth_user_id = %s
          and up.active
          and (
            exists (
              select 1
              from public.platform_admins pa
              where pa.auth_user_id = up.auth_user_id and pa.active
            )
            or exists (
              select 1
              from public.business_owners bo
              where bo.auth_user_id = up.auth_user_id
                and bo.business_id = %s
                and bo.active
                and bo.is_primary
            )
            or exists (
              select 1
              from public.shop_memberships sm
              where sm.auth_user_id = up.auth_user_id
                and sm.business_id = %s
                and sm.shop_id = %s
                and sm.role in ('manager', 'receptionist')
                and sm.active
            )
          )
        for share of up
        """,
        (actor_id, business_id, business_id, shop_id),
    )
    if await cursor.fetchone() is None:
        raise BookingAccessDeniedError


async def _require_customer(
    connection: Any,
    *,
    business_id: UUID,
    shop_id: UUID,
    customer_id: UUID,
    telegram_user_id: int,
) -> None:
    cursor = await connection.execute(
        """
        select 1
        from public.customers c
        where c.id = %s
          and c.business_id = %s
          and c.shop_id = %s
          and c.telegram_user_id = %s
          and c.blocked_at is null
          and c.anonymized_at is null
          and not exists (
            select 1
            from public.telegram_user_blocks tub
            where tub.telegram_user_id = c.telegram_user_id
              and (tub.expires_at is null or tub.expires_at > now())
          )
        for share of c
        """,
        (customer_id, business_id, shop_id, telegram_user_id),
    )
    if await cursor.fetchone() is None:
        raise BookingAccessDeniedError


async def _authorize_actor(
    connection: Any,
    *,
    actor_id: UUID | None,
    business_id: UUID,
    shop_id: UUID,
    customer_id: UUID | None,
    telegram_user_id: int | None,
) -> tuple[UUID, Literal["auth_user", "telegram_user"], UUID | int]:
    if telegram_user_id is None:
        if actor_id is None:
            raise BookingAccessDeniedError
        await _require_operator(
            connection,
            actor_id=actor_id,
            business_id=business_id,
            shop_id=shop_id,
        )
        return actor_id, "auth_user", actor_id
    if actor_id is not None or customer_id is None:
        raise BookingAccessDeniedError
    await _require_customer(
        connection,
        business_id=business_id,
        shop_id=shop_id,
        customer_id=customer_id,
        telegram_user_id=telegram_user_id,
    )
    return customer_id, "telegram_user", telegram_user_id


async def _shop_clock(
    connection: Any,
    *,
    business_id: UUID,
    shop_id: UUID,
) -> ZoneInfo:
    cursor = await connection.execute(
        """
        select timezone
        from public.shops
        where id = %s and business_id = %s
        """,
        (shop_id, business_id),
    )
    row = await cursor.fetchone()
    if row is None:
        raise BookingNotFoundError
    try:
        return ZoneInfo(str(row[0]))
    except ZoneInfoNotFoundError as exc:
        raise BookingInputError("shop timezone is invalid") from exc


def _business_date(at: datetime, timezone: ZoneInfo) -> date:
    return at.astimezone(timezone).date()


async def _allocate_queue_number(
    connection: Any,
    *,
    business_id: UUID,
    shop_id: UUID,
    business_date: date,
) -> int:
    cursor = await connection.execute(
        """
        insert into public.queue_counters (
          business_id, shop_id, business_date, last_number
        )
        values (%s, %s, %s, 1)
        on conflict (shop_id, business_date)
        do update
          set last_number = public.queue_counters.last_number + 1,
              updated_at = now()
        returning last_number
        """,
        (business_id, shop_id, business_date),
    )
    row = await cursor.fetchone()
    assert row is not None
    return int(row[0])


async def _expire_holds(
    connection: Any,
    *,
    business_id: UUID,
    shop_id: UUID,
    at: datetime,
) -> list[UUID]:
    cursor = await connection.execute(
        """
        update public.bookings
        set status = 'expired',
            hold_expires_at = null,
            updated_at = %s
        where business_id = %s
          and shop_id = %s
          and status = 'held'
          and hold_expires_at <= %s
        returning id
        """,
        (at, business_id, shop_id, at),
    )
    return [UUID(str(row[0])) for row in await cursor.fetchall()]


async def _write_event(
    connection: Any,
    *,
    business_id: UUID,
    shop_id: UUID,
    actor_id: UUID | int | None,
    actor_type: Literal["auth_user", "telegram_user"] | None = None,
    action: str,
    booking_id: UUID,
    request_id: str,
    details: dict[str, Any],
) -> None:
    resolved_actor_type = actor_type or ("auth_user" if actor_id is not None else "system")
    audit_actor = str(actor_id) if actor_id is not None else "booking-worker"
    await connection.execute(
        """
        insert into public.audit_log (
          business_id, shop_id, actor_type, actor_id, action,
          entity_type, entity_id, request_id, after
        )
        values (%s, %s, %s, %s, %s, 'booking', %s, %s, %s)
        """,
        (
            business_id,
            shop_id,
            resolved_actor_type,
            audit_actor,
            action,
            booking_id,
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
            f"{action}:{request_id}:{booking_id}",
            Jsonb(details),
        ),
    )


async def _audit_expired_holds(
    connection: Any,
    *,
    business_id: UUID,
    shop_id: UUID,
    booking_ids: list[UUID],
) -> None:
    for booking_id in booking_ids:
        await _write_event(
            connection,
            business_id=business_id,
            shop_id=shop_id,
            actor_id=None,
            action="booking.hold_expired",
            booking_id=booking_id,
            request_id=f"hold-expired:{booking_id}",
            details={"booking_id": str(booking_id), "status": "expired"},
        )


async def _service_snapshots(
    connection: Any,
    *,
    business_id: UUID,
    shop_id: UUID,
    service_ids: list[UUID],
) -> tuple[list[tuple[Any, ...]], int]:
    cursor = await connection.execute(
        """
        select id, name, price_gross, vat_rate, duration_minutes
        from public.services
        where business_id = %s
          and shop_id = %s
          and active
          and id = any(%s)
        order by array_position(%s::uuid[], id)
        for share
        """,
        (business_id, shop_id, service_ids, service_ids),
    )
    rows = await cursor.fetchall()
    if len(rows) != len(service_ids):
        raise BookingInputError("one or more services are unavailable")
    duration = sum(int(row[4]) for row in rows)
    if duration > 1440:
        raise BookingInputError("combined service duration exceeds 24 hours")
    return rows, duration


async def _choose_barber(
    connection: Any,
    *,
    business_id: UUID,
    shop_id: UUID,
    requested_barber_id: UUID | None,
    starts_at: datetime,
    ends_at: datetime,
    appointment: bool,
    at: datetime,
) -> UUID:
    cursor = await connection.execute(
        """
        with candidate_dates as (
          select (%s at time zone sh.timezone)::date as local_date
          from public.shops sh
          where sh.id = %s and sh.business_id = %s
          union all
          select ((%s at time zone sh.timezone)::date - 1)
          from public.shops sh
          where sh.id = %s and sh.business_id = %s
        ),
        shop_windows as (
          select
            (d.local_date + bh.open_time) at time zone sh.timezone as starts_at,
            (
              d.local_date + bh.close_time
              + case when bh.closes_next_day then interval '1 day' else interval '0' end
            ) at time zone sh.timezone as ends_at
          from candidate_dates d
          join public.shops sh on sh.id = %s and sh.business_id = %s
          join public.shop_business_hours bh
            on bh.shop_id = sh.id
           and bh.business_id = sh.business_id
           and bh.iso_weekday = extract(isodow from d.local_date)::integer
           and bh.effective_from <= d.local_date
           and (bh.effective_until is null or bh.effective_until > d.local_date)
           and bh.active
        ),
        staff_windows as (
          select
            ss.barber_membership_id,
            ss.id as schedule_id,
            (d.local_date + ss.start_time) at time zone sh.timezone as starts_at,
            (
              d.local_date + ss.end_time
              + case when ss.ends_next_day then interval '1 day' else interval '0' end
            ) at time zone sh.timezone as ends_at
          from candidate_dates d
          join public.shops sh on sh.id = %s and sh.business_id = %s
          join public.staff_schedules ss
            on ss.shop_id = sh.id
           and ss.business_id = sh.business_id
           and ss.iso_weekday = extract(isodow from d.local_date)::integer
           and ss.effective_from <= d.local_date
           and (ss.effective_until is null or ss.effective_until > d.local_date)
           and ss.active
        )
        select sm.id
        from public.shop_memberships sm
        where sm.business_id = %s
          and sm.shop_id = %s
          and sm.role = 'barber'
          and sm.active
          and (%s::uuid is null or sm.id = %s)
          and exists (
            select 1
            from shop_windows sw
            where sw.starts_at <= %s and sw.ends_at >= %s
          )
          and exists (
            select 1
            from staff_windows fw
            where fw.barber_membership_id = sm.id
              and fw.starts_at <= %s
              and fw.ends_at >= %s
              and not exists (
                select 1
                from public.staff_schedule_breaks sb
                where sb.schedule_id = fw.schedule_id
                  and tstzrange(
                    fw.starts_at + make_interval(mins => sb.start_offset_minutes),
                    fw.starts_at + make_interval(
                      mins => sb.start_offset_minutes + sb.duration_minutes
                    ),
                    '[)'
                  ) && tstzrange(%s, %s, '[)')
              )
          )
          and not exists (
            select 1
            from public.shop_closures sc
            where sc.business_id = sm.business_id
              and sc.shop_id = sm.shop_id
              and tstzrange(sc.starts_at, sc.ends_at, '[)')
                && tstzrange(%s, %s, '[)')
          )
          and not exists (
            select 1
            from public.staff_leave sl
            where sl.barber_membership_id = sm.id
              and tstzrange(sl.starts_at, sl.ends_at, '[)')
                && tstzrange(%s, %s, '[)')
          )
          and not exists (
            select 1
            from public.staff_unavailability su
            where su.barber_membership_id = sm.id
              and tstzrange(su.starts_at, su.ends_at, '[)')
                && tstzrange(%s, %s, '[)')
          )
          and (
            not %s
            or not exists (
              select 1
              from public.bookings b
              where b.shop_id = sm.shop_id
                and b.barber_membership_id = sm.id
                and b.booking_type = 'appointment'
                and b.status in ('held', 'requested', 'confirmed', 'in_service')
                and (b.status <> 'held' or b.hold_expires_at > %s)
                and tstzrange(b.scheduled_start, b.scheduled_end, '[)')
                  && tstzrange(%s, %s, '[)')
            )
          )
        order by (
          select count(*)
          from public.bookings active
          where active.shop_id = sm.shop_id
            and active.barber_membership_id = sm.id
            and active.status in ('confirmed', 'in_service')
        ), sm.id
        limit 1
        """,
        (
            starts_at,
            shop_id,
            business_id,
            starts_at,
            shop_id,
            business_id,
            shop_id,
            business_id,
            shop_id,
            business_id,
            business_id,
            shop_id,
            requested_barber_id,
            requested_barber_id,
            starts_at,
            ends_at,
            starts_at,
            ends_at,
            starts_at,
            ends_at,
            starts_at,
            ends_at,
            starts_at,
            ends_at,
            starts_at,
            ends_at,
            appointment,
            at,
            starts_at,
            ends_at,
        ),
    )
    row = await cursor.fetchone()
    if row is None:
        raise BookingConflictError("no barber is available")
    return UUID(str(row[0]))


async def _insert_service_snapshots(
    connection: Any,
    *,
    booking_id: UUID,
    business_id: UUID,
    shop_id: UUID,
    services: list[tuple[Any, ...]],
) -> None:
    for sort_order, service in enumerate(services):
        await connection.execute(
            """
            insert into public.booking_services (
              business_id, shop_id, booking_id, service_id, service_name,
              price_gross, vat_rate, duration_minutes, sort_order
            )
            values (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                business_id,
                shop_id,
                booking_id,
                service[0],
                service[1],
                service[2],
                service[3],
                service[4],
                sort_order,
            ),
        )


def _response(row: tuple[Any, ...]) -> BookingResponse:
    return BookingResponse(
        booking_id=UUID(str(row[0])),
        business_id=UUID(str(row[1])),
        shop_id=UUID(str(row[2])),
        customer_id=UUID(str(row[3])) if row[3] is not None else None,
        barber_membership_id=UUID(str(row[4])),
        booking_type=str(row[5]),
        status=str(row[6]),
        queue_business_date=row[7],
        queue_number=int(row[8]) if row[8] is not None else None,
        scheduled_start=row[9],
        scheduled_end=row[10],
        hold_expires_at=row[11],
        estimated_start_at=row[12],
        rescheduled_from_booking_id=UUID(str(row[13])) if row[13] is not None else None,
    )


BOOKING_RETURNING = """
returning
  id, business_id, shop_id, customer_id, barber_membership_id,
  booking_type::text, status::text, queue_business_date, queue_number,
  scheduled_start, scheduled_end, hold_expires_at, estimated_start_at,
  rescheduled_from_booking_id
"""


async def create_booking(
    pool: Any,
    *,
    actor_id: UUID | None,
    business_id: UUID,
    shop_id: UUID,
    idempotency_key: str,
    request_id: str,
    payload: BookingCreateRequest,
    telegram_user_id: int | None = None,
    at: datetime | None = None,
) -> BookingResponse:
    now = at or datetime.now(UTC)
    try:
        async with pool.connection(timeout=5) as connection, connection.transaction():
            await _lock_shop(connection, shop_id)
            idempotency_actor, audit_actor_type, audit_actor = await _authorize_actor(
                connection,
                actor_id=actor_id,
                business_id=business_id,
                shop_id=shop_id,
                customer_id=payload.customer_id,
                telegram_user_id=telegram_user_id,
            )
            await require_active_entitlement(
                connection,
                business_id=business_id,
                shop_id=shop_id,
                at=now,
            )
            replay = await reserve_idempotency(
                connection,
                scope=f"booking.create:{shop_id}",
                actor_id=idempotency_actor,
                key=idempotency_key,
                payload=payload,
                expected_status=201,
            )
            if replay is not None:
                return BookingResponse.model_validate(replay)

            expired = await _expire_holds(
                connection,
                business_id=business_id,
                shop_id=shop_id,
                at=now,
            )
            await _audit_expired_holds(
                connection,
                business_id=business_id,
                shop_id=shop_id,
                booking_ids=expired,
            )
            services, duration_minutes = await _service_snapshots(
                connection,
                business_id=business_id,
                shop_id=shop_id,
                service_ids=payload.service_ids,
            )
            timezone = await _shop_clock(
                connection,
                business_id=business_id,
                shop_id=shop_id,
            )
            starts_at = (
                payload.scheduled_start.astimezone(UTC)
                if payload.scheduled_start is not None
                else now
            )
            if payload.booking_type == "appointment" and starts_at < now:
                raise BookingInputError("appointment start must be in the future")
            ends_at = starts_at + timedelta(minutes=duration_minutes)
            barber_id = await _choose_barber(
                connection,
                business_id=business_id,
                shop_id=shop_id,
                requested_barber_id=payload.barber_membership_id,
                starts_at=starts_at,
                ends_at=ends_at,
                appointment=payload.booking_type == "appointment",
                at=now,
            )

            queue_date = None
            queue_number = None
            status = (
                "held"
                if payload.booking_type == "appointment"
                else "requested"
                if telegram_user_id is not None
                else "confirmed"
            )
            hold_expires_at = now + HOLD_DURATION if status == "held" else None
            confirmed_at = now if status == "confirmed" else None
            if payload.booking_type != "appointment":
                queue_date = _business_date(now, timezone)
                queue_number = await _allocate_queue_number(
                    connection,
                    business_id=business_id,
                    shop_id=shop_id,
                    business_date=queue_date,
                )

            cursor = await connection.execute(
                f"""
                insert into public.bookings (
                  business_id, shop_id, customer_id, barber_membership_id,
                  booking_type, status, source, queue_business_date, queue_number,
                  scheduled_start, scheduled_end, hold_expires_at, confirmed_at,
                  auto_confirmed, created_at, updated_at
                )
                values (
                  %s, %s, %s, %s, %s, %s, %s, %s, %s,
                  %s, %s, %s, %s, %s, %s, %s
                )
                {BOOKING_RETURNING}
                """,
                (
                    business_id,
                    shop_id,
                    payload.customer_id,
                    barber_id,
                    payload.booking_type,
                    status,
                    "telegram" if telegram_user_id is not None else "dashboard",
                    queue_date,
                    queue_number,
                    starts_at if payload.booking_type == "appointment" else None,
                    ends_at if payload.booking_type == "appointment" else None,
                    hold_expires_at,
                    confirmed_at,
                    status == "confirmed",
                    now,
                    now,
                ),
            )
            row = await cursor.fetchone()
            assert row is not None
            response = _response(row)
            await _insert_service_snapshots(
                connection,
                booking_id=response.booking_id,
                business_id=business_id,
                shop_id=shop_id,
                services=services,
            )
            await _write_event(
                connection,
                business_id=business_id,
                shop_id=shop_id,
                actor_id=audit_actor,
                actor_type=audit_actor_type,
                action="booking.created",
                booking_id=response.booking_id,
                request_id=request_id,
                details=response.model_dump(mode="json"),
            )
            await complete_idempotency(
                connection,
                scope=f"booking.create:{shop_id}",
                actor_id=idempotency_actor,
                key=idempotency_key,
                response_status=201,
                response=response,
            )
            return response
    except (ExclusionViolation, ForeignKeyViolation) as exc:
        raise BookingConflictError from exc


async def transition_booking(
    pool: Any,
    *,
    actor_id: UUID | None,
    business_id: UUID,
    shop_id: UUID,
    booking_id: UUID,
    target_status: Literal["confirmed", "in_service", "completed", "cancelled", "no_show"],
    idempotency_key: str,
    request_id: str,
    payload: BookingTransitionRequest,
    customer_id: UUID | None = None,
    telegram_user_id: int | None = None,
    at: datetime | None = None,
) -> BookingResponse:
    now = at or datetime.now(UTC)
    if telegram_user_id is not None and target_status != "cancelled":
        raise BookingAccessDeniedError
    idempotency_payload = payload.model_copy(update={"reason": payload.reason or target_status})
    async with pool.connection(timeout=5) as connection, connection.transaction():
        await _lock_shop(connection, shop_id)
        idempotency_actor, audit_actor_type, audit_actor = await _authorize_actor(
            connection,
            actor_id=actor_id,
            business_id=business_id,
            shop_id=shop_id,
            customer_id=customer_id,
            telegram_user_id=telegram_user_id,
        )
        await require_active_entitlement(
            connection,
            business_id=business_id,
            shop_id=shop_id,
            at=now,
        )
        scope = f"booking.{target_status}:{booking_id}"
        replay = await reserve_idempotency(
            connection,
            scope=scope,
            actor_id=idempotency_actor,
            key=idempotency_key,
            payload=idempotency_payload,
            expected_status=200,
        )
        if replay is not None:
            return BookingResponse.model_validate(replay)

        cursor = await connection.execute(
            """
            select status::text, hold_expires_at
            from public.bookings
            where id = %s and business_id = %s and shop_id = %s
              and (%s::uuid is null or customer_id = %s)
            for update
            """,
            (booking_id, business_id, shop_id, customer_id, customer_id),
        )
        current = await cursor.fetchone()
        if current is None:
            raise BookingNotFoundError
        if current[0] == "held" and current[1] <= now:
            raise BookingConflictError("booking hold expired")

        allowed_previous = {
            "confirmed": {"held", "requested"},
            "in_service": {"confirmed"},
            "completed": {"in_service"},
            "cancelled": {"held", "requested", "confirmed"},
            "no_show": {"confirmed"},
        }
        if current[0] not in allowed_previous[target_status]:
            raise BookingConflictError("booking cannot transition from current state")

        cancellation_reason = payload.reason if target_status == "cancelled" else None
        no_show_reason = payload.reason if target_status == "no_show" else None
        if target_status in {"cancelled", "no_show"} and payload.reason is None:
            raise BookingInputError("a reason is required")
        cursor = await connection.execute(
            f"""
            update public.bookings
            set status = %s,
                hold_expires_at = null,
                cancellation_reason = %s,
                no_show_reason = %s,
                confirmed_at = case when %s = 'confirmed' then %s else confirmed_at end,
                started_at = case when %s = 'in_service' then %s else started_at end,
                completed_at = case when %s = 'completed' then %s else completed_at end,
                updated_at = %s
            where id = %s and business_id = %s and shop_id = %s
            {BOOKING_RETURNING}
            """,
            (
                target_status,
                cancellation_reason,
                no_show_reason,
                target_status,
                now,
                target_status,
                now,
                target_status,
                now,
                now,
                booking_id,
                business_id,
                shop_id,
            ),
        )
        row = await cursor.fetchone()
        assert row is not None
        response = _response(row)
        await _write_event(
            connection,
            business_id=business_id,
            shop_id=shop_id,
            actor_id=audit_actor,
            actor_type=audit_actor_type,
            action=f"booking.{target_status}",
            booking_id=booking_id,
            request_id=request_id,
            details=response.model_dump(mode="json"),
        )
        await complete_idempotency(
            connection,
            scope=scope,
            actor_id=idempotency_actor,
            key=idempotency_key,
            response_status=200,
            response=response,
        )
        return response


async def reschedule_booking(
    pool: Any,
    *,
    actor_id: UUID | None,
    business_id: UUID,
    shop_id: UUID,
    booking_id: UUID,
    idempotency_key: str,
    request_id: str,
    payload: BookingRescheduleRequest,
    customer_id: UUID | None = None,
    telegram_user_id: int | None = None,
    at: datetime | None = None,
) -> BookingResponse:
    now = at or datetime.now(UTC)
    starts_at = payload.scheduled_start.astimezone(UTC)
    if starts_at < now:
        raise BookingInputError("appointment start must be in the future")
    try:
        async with pool.connection(timeout=5) as connection, connection.transaction():
            await _lock_shop(connection, shop_id)
            idempotency_actor, audit_actor_type, audit_actor = await _authorize_actor(
                connection,
                actor_id=actor_id,
                business_id=business_id,
                shop_id=shop_id,
                customer_id=customer_id,
                telegram_user_id=telegram_user_id,
            )
            await require_active_entitlement(
                connection,
                business_id=business_id,
                shop_id=shop_id,
                at=now,
            )
            scope = f"booking.reschedule:{booking_id}"
            replay = await reserve_idempotency(
                connection,
                scope=scope,
                actor_id=idempotency_actor,
                key=idempotency_key,
                payload=payload,
                expected_status=201,
            )
            if replay is not None:
                return BookingResponse.model_validate(replay)

            cursor = await connection.execute(
                """
                select customer_id, source::text, status::text, hold_expires_at
                from public.bookings
                where id = %s
                  and business_id = %s
                  and shop_id = %s
                  and booking_type = 'appointment'
                  and (%s::uuid is null or customer_id = %s)
                for update
                """,
                (booking_id, business_id, shop_id, customer_id, customer_id),
            )
            original = await cursor.fetchone()
            if original is None:
                raise BookingNotFoundError
            if original[2] not in {"held", "requested", "confirmed"}:
                raise BookingConflictError("booking cannot be rescheduled from current state")
            if original[2] == "held" and original[3] <= now:
                raise BookingConflictError("booking hold expired")

            cursor = await connection.execute(
                """
                select service_id, service_name, price_gross, vat_rate, duration_minutes
                from public.booking_services
                where booking_id = %s
                  and business_id = %s
                  and shop_id = %s
                order by sort_order
                """,
                (booking_id, business_id, shop_id),
            )
            services = await cursor.fetchall()
            if not services:
                raise BookingConflictError("booking has no service snapshots")
            duration_minutes = sum(int(service[4]) for service in services)
            ends_at = starts_at + timedelta(minutes=duration_minutes)

            await connection.execute(
                """
                update public.bookings
                set status = 'cancelled',
                    hold_expires_at = null,
                    cancellation_reason = 'rescheduled',
                    updated_at = %s
                where id = %s
                """,
                (now, booking_id),
            )
            barber_id = await _choose_barber(
                connection,
                business_id=business_id,
                shop_id=shop_id,
                requested_barber_id=payload.barber_membership_id,
                starts_at=starts_at,
                ends_at=ends_at,
                appointment=True,
                at=now,
            )
            cursor = await connection.execute(
                f"""
                insert into public.bookings (
                  business_id, shop_id, customer_id, barber_membership_id,
                  booking_type, status, source, scheduled_start, scheduled_end,
                  hold_expires_at, rescheduled_from_booking_id, created_at, updated_at
                )
                values (
                  %s, %s, %s, %s, 'appointment', 'held', %s, %s, %s,
                  %s, %s, %s, %s
                )
                {BOOKING_RETURNING}
                """,
                (
                    business_id,
                    shop_id,
                    original[0],
                    barber_id,
                    original[1],
                    starts_at,
                    ends_at,
                    now + HOLD_DURATION,
                    booking_id,
                    now,
                    now,
                ),
            )
            row = await cursor.fetchone()
            assert row is not None
            response = _response(row)
            await _insert_service_snapshots(
                connection,
                booking_id=response.booking_id,
                business_id=business_id,
                shop_id=shop_id,
                services=services,
            )
            await _write_event(
                connection,
                business_id=business_id,
                shop_id=shop_id,
                actor_id=audit_actor,
                actor_type=audit_actor_type,
                action="booking.cancelled",
                booking_id=booking_id,
                request_id=request_id,
                details={"booking_id": str(booking_id), "reason": "rescheduled"},
            )
            await _write_event(
                connection,
                business_id=business_id,
                shop_id=shop_id,
                actor_id=audit_actor,
                actor_type=audit_actor_type,
                action="booking.rescheduled",
                booking_id=response.booking_id,
                request_id=request_id,
                details=response.model_dump(mode="json"),
            )
            await complete_idempotency(
                connection,
                scope=scope,
                actor_id=idempotency_actor,
                key=idempotency_key,
                response_status=201,
                response=response,
            )
            return response
    except (ExclusionViolation, ForeignKeyViolation) as exc:
        raise BookingConflictError from exc


async def find_customer_appointment_slots(
    pool: Any,
    *,
    business_id: UUID,
    shop_id: UUID,
    customer_id: UUID,
    telegram_user_id: int,
    service_ids: list[UUID],
    day: date,
    barber_membership_id: UUID | None = None,
    at: datetime | None = None,
    limit: int = 24,
) -> list[BookingSlot]:
    now = at or datetime.now(UTC)
    if not 1 <= limit <= 48:
        raise BookingInputError("slot limit is outside the allowed range")
    async with pool.connection(timeout=5) as connection, connection.transaction():
        await _require_customer(
            connection,
            business_id=business_id,
            shop_id=shop_id,
            customer_id=customer_id,
            telegram_user_id=telegram_user_id,
        )
        await require_active_entitlement(
            connection,
            business_id=business_id,
            shop_id=shop_id,
            at=now,
        )
        _services, duration_minutes = await _service_snapshots(
            connection,
            business_id=business_id,
            shop_id=shop_id,
            service_ids=service_ids,
        )
        timezone = await _shop_clock(
            connection,
            business_id=business_id,
            shop_id=shop_id,
        )
        local_today = now.astimezone(timezone).date()
        if day < local_today or day > local_today + timedelta(days=60):
            raise BookingInputError("appointment day is outside the allowed range")
        cursor = await connection.execute(
            """
            select open_time, close_time, closes_next_day
            from public.shop_business_hours
            where business_id = %s
              and shop_id = %s
              and iso_weekday = %s
              and effective_from <= %s
              and (effective_until is null or effective_until >= %s)
              and active
            order by effective_from desc, id desc
            limit 1
            """,
            (business_id, shop_id, day.isoweekday(), day, day),
        )
        hours = await cursor.fetchone()
        if hours is None:
            return []

        opens_at = datetime.combine(day, hours[0], timezone).astimezone(UTC)
        closes_day = day + timedelta(days=1) if bool(hours[2]) else day
        closes_at = datetime.combine(closes_day, hours[1], timezone).astimezone(UTC)
        duration = timedelta(minutes=duration_minutes)
        step = timedelta(minutes=15)
        candidate = opens_at
        if candidate < now:
            elapsed_seconds = max(0, int((now - candidate).total_seconds()))
            candidate += step * ((elapsed_seconds + 899) // 900)

        slots: list[BookingSlot] = []
        while candidate + duration <= closes_at and len(slots) < limit:
            try:
                barber_id = await _choose_barber(
                    connection,
                    business_id=business_id,
                    shop_id=shop_id,
                    requested_barber_id=barber_membership_id,
                    starts_at=candidate,
                    ends_at=candidate + duration,
                    appointment=True,
                    at=now,
                )
            except BookingConflictError:
                candidate += step
                continue
            slots.append(
                BookingSlot(
                    starts_at=candidate,
                    barber_membership_id=barber_id,
                )
            )
            candidate += step
        return slots


async def promote_due_appointments(
    pool: Any,
    *,
    at: datetime | None = None,
    limit: int = 100,
) -> int:
    now = at or datetime.now(UTC)
    promoted = 0
    skipped: list[UUID] = []
    for _ in range(limit):
        async with pool.connection(timeout=5) as connection, connection.transaction():
            cursor = await connection.execute(
                """
                select id, business_id, shop_id
                from public.bookings
                where booking_type = 'appointment'
                  and status = 'confirmed'
                  and queue_number is null
                  and scheduled_start <= %s
                  and not (id = any(%s::uuid[]))
                order by scheduled_start, id
                limit 1
                for update skip locked
                """,
                (now + PROMOTION_WINDOW, skipped),
            )
            row = await cursor.fetchone()
            if row is None:
                break
            booking_id = UUID(str(row[0]))
            business_id = UUID(str(row[1]))
            shop_id = UUID(str(row[2]))
            await _lock_shop(connection, shop_id)
            try:
                await require_active_entitlement(
                    connection,
                    business_id=business_id,
                    shop_id=shop_id,
                    at=now,
                )
            except SubscriptionSuspendedError:
                skipped.append(booking_id)
                continue
            timezone = await _shop_clock(
                connection,
                business_id=business_id,
                shop_id=shop_id,
            )
            business_date = _business_date(now, timezone)
            queue_number = await _allocate_queue_number(
                connection,
                business_id=business_id,
                shop_id=shop_id,
                business_date=business_date,
            )
            await connection.execute(
                """
                update public.bookings
                set queue_business_date = %s,
                    queue_number = %s,
                    updated_at = %s
                where id = %s
                  and status = 'confirmed'
                  and queue_number is null
                """,
                (business_date, queue_number, now, booking_id),
            )
            await _write_event(
                connection,
                business_id=business_id,
                shop_id=shop_id,
                actor_id=None,
                action="booking.promoted",
                booking_id=booking_id,
                request_id=f"booking-promoted:{booking_id}",
                details={
                    "booking_id": str(booking_id),
                    "queue_business_date": business_date.isoformat(),
                    "queue_number": queue_number,
                },
            )
            promoted += 1
    return promoted


async def expire_booking_holds(
    pool: Any,
    *,
    at: datetime | None = None,
    limit: int = 100,
) -> int:
    now = at or datetime.now(UTC)
    expired_count = 0
    for _ in range(limit):
        async with pool.connection(timeout=5) as connection, connection.transaction():
            cursor = await connection.execute(
                """
                select id, business_id, shop_id
                from public.bookings
                where status = 'held' and hold_expires_at <= %s
                order by hold_expires_at, id
                limit 1
                for update skip locked
                """,
                (now,),
            )
            row = await cursor.fetchone()
            if row is None:
                break
            business_id = UUID(str(row[1]))
            shop_id = UUID(str(row[2]))
            await _lock_shop(connection, shop_id)
            expired = await _expire_holds(
                connection,
                business_id=business_id,
                shop_id=shop_id,
                at=now,
            )
            await _audit_expired_holds(
                connection,
                business_id=business_id,
                shop_id=shop_id,
                booking_ids=expired,
            )
            expired_count += len(expired)
    return expired_count


__all__ = [
    "BookingAccessDeniedError",
    "BookingConflictError",
    "BookingCreateRequest",
    "BookingInputError",
    "BookingNotFoundError",
    "BookingResponse",
    "BookingSlot",
    "BookingRescheduleRequest",
    "BookingTransitionRequest",
    "IdempotencyConflictError",
    "IdempotencyInProgressError",
    "create_booking",
    "expire_booking_holds",
    "find_customer_appointment_slots",
    "promote_due_appointments",
    "reschedule_booking",
    "transition_booking",
]
