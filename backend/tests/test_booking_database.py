import asyncio
import os
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from app.core.config import Settings
from app.core.database import create_database_pool
from app.services.booking_service import (
    BookingAccessDeniedError,
    BookingConflictError,
    BookingCreateRequest,
    BookingRescheduleRequest,
    BookingTransitionRequest,
    create_booking,
    expire_booking_holds,
    find_customer_appointment_slots,
    promote_due_appointments,
    reschedule_booking,
    transition_booking,
)

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_DATABASE_INTEGRATION") != "1",
    reason="requires reconstructed Phase 2 PostgreSQL test database",
)

RECEPTIONIST_ID = UUID("00000000-0000-0000-0000-000000000003")
BARBER_USER_ID = UUID("00000000-0000-0000-0000-000000000005")
BUSINESS_ID = UUID("10000000-0000-0000-0000-000000000001")
SHOP_ID = UUID("20000000-0000-0000-0000-000000000001")
CUSTOMER_ID = UUID("51000000-0000-0000-0000-000000000001")
SERVICE_ID = UUID("50000000-0000-0000-0000-000000000001")
SECOND_BARBER_ID = UUID("59000000-0000-0000-0000-000000000001")
BASE_AT = datetime(2026, 7, 27, 5, 0, tzinfo=UTC)
CUSTOMER_TELEGRAM_ID = 7000000001


async def _prepare_second_barber(pool: object) -> list[UUID]:
    async with pool.connection(timeout=5) as connection, connection.transaction():  # type: ignore[attr-defined]
        await connection.execute(
            """
            insert into public.shop_memberships (
              id, business_id, shop_id, telegram_user_id, role, display_name
            )
            values (%s, %s, %s, 7999999999, 'barber', 'Second Barber')
            on conflict (id) do nothing
            """,
            (SECOND_BARBER_ID, BUSINESS_ID, SHOP_ID),
        )
        await connection.execute(
            """
            insert into public.staff_schedules (
              business_id, shop_id, barber_membership_id, iso_weekday,
              start_time, end_time, effective_from
            )
            select %s, %s, %s, 1, '09:00', '18:00', '2026-07-27'
            where not exists (
              select 1
              from public.staff_schedules
              where barber_membership_id = %s and iso_weekday = 1
            )
            """,
            (BUSINESS_ID, SHOP_ID, SECOND_BARBER_ID, SECOND_BARBER_ID),
        )
        cursor = await connection.execute(
            """
            select id
            from public.shop_memberships
            where business_id = %s
              and shop_id = %s
              and role = 'barber'
              and active
            order by id
            """,
            (BUSINESS_ID, SHOP_ID),
        )
        return [UUID(str(row[0])) for row in await cursor.fetchall()]


def _appointment(
    starts_at: datetime,
    *,
    barber_id: UUID | None = None,
) -> BookingCreateRequest:
    return BookingCreateRequest(
        booking_type="appointment",
        customer_id=CUSTOMER_ID,
        barber_membership_id=barber_id,
        service_ids=[SERVICE_ID],
        scheduled_start=starts_at,
    )


async def test_booking_hold_queue_concurrency_workers_and_redis_independence() -> None:
    pool = create_database_pool(Settings(_env_file=None))
    await pool.open()
    try:
        barber_ids = await _prepare_second_barber(pool)
        assert len(barber_ids) >= 2

        deterministic = await create_booking(
            pool,
            actor_id=RECEPTIONIST_ID,
            business_id=BUSINESS_ID,
            shop_id=SHOP_ID,
            idempotency_key="booking-any-barber-0001",
            request_id="booking-any-barber",
            payload=_appointment(datetime(2026, 7, 27, 6, 0, tzinfo=UTC)),
            at=BASE_AT,
        )
        assert deterministic.barber_membership_id == barber_ids[0]
        assert deterministic.status == "held"
        assert deterministic.hold_expires_at == BASE_AT + timedelta(minutes=5)
        reschedule_payload = BookingRescheduleRequest(
            scheduled_start=datetime(2026, 7, 27, 8, 0, tzinfo=UTC),
            barber_membership_id=barber_ids[0],
        )
        rescheduled = await reschedule_booking(
            pool,
            actor_id=RECEPTIONIST_ID,
            business_id=BUSINESS_ID,
            shop_id=SHOP_ID,
            booking_id=deterministic.booking_id,
            idempotency_key="booking-reschedule-same-key-0001",
            request_id="booking-reschedule-a",
            payload=reschedule_payload,
            at=BASE_AT + timedelta(minutes=1),
        )
        replayed_reschedule = await reschedule_booking(
            pool,
            actor_id=RECEPTIONIST_ID,
            business_id=BUSINESS_ID,
            shop_id=SHOP_ID,
            booking_id=deterministic.booking_id,
            idempotency_key="booking-reschedule-same-key-0001",
            request_id="booking-reschedule-b",
            payload=reschedule_payload,
            at=BASE_AT + timedelta(minutes=1),
        )
        assert rescheduled == replayed_reschedule
        assert rescheduled.rescheduled_from_booking_id == deterministic.booking_id

        contested_payload = _appointment(
            datetime(2026, 7, 27, 7, 0, tzinfo=UTC),
            barber_id=barber_ids[0],
        )
        results = await asyncio.gather(
            create_booking(
                pool,
                actor_id=RECEPTIONIST_ID,
                business_id=BUSINESS_ID,
                shop_id=SHOP_ID,
                idempotency_key="booking-contested-slot-0002a",
                request_id="booking-contested-a",
                payload=contested_payload,
                at=BASE_AT,
            ),
            create_booking(
                pool,
                actor_id=RECEPTIONIST_ID,
                business_id=BUSINESS_ID,
                shop_id=SHOP_ID,
                idempotency_key="booking-contested-slot-0002b",
                request_id="booking-contested-b",
                payload=contested_payload,
                at=BASE_AT,
            ),
            return_exceptions=True,
        )
        winners = [result for result in results if not isinstance(result, Exception)]
        conflicts = [result for result in results if isinstance(result, BookingConflictError)]
        assert len(winners) == 1
        assert len(conflicts) == 1
        winner = winners[0]

        confirmed_a, confirmed_b = await asyncio.gather(
            transition_booking(
                pool,
                actor_id=RECEPTIONIST_ID,
                business_id=BUSINESS_ID,
                shop_id=SHOP_ID,
                booking_id=winner.booking_id,
                target_status="confirmed",
                idempotency_key="booking-confirm-same-key-0003",
                request_id="booking-confirm-a",
                payload=BookingTransitionRequest(),
                at=BASE_AT + timedelta(minutes=1),
            ),
            transition_booking(
                pool,
                actor_id=RECEPTIONIST_ID,
                business_id=BUSINESS_ID,
                shop_id=SHOP_ID,
                booking_id=winner.booking_id,
                target_status="confirmed",
                idempotency_key="booking-confirm-same-key-0003",
                request_id="booking-confirm-b",
                payload=BookingTransitionRequest(),
                at=BASE_AT + timedelta(minutes=1),
            ),
        )
        assert confirmed_a == confirmed_b
        cancelled = await transition_booking(
            pool,
            actor_id=RECEPTIONIST_ID,
            business_id=BUSINESS_ID,
            shop_id=SHOP_ID,
            booking_id=winner.booking_id,
            target_status="cancelled",
            idempotency_key="booking-cancel-confirmed-0003b",
            request_id="booking-cancel-confirmed",
            payload=BookingTransitionRequest(reason="customer requested cancellation"),
            at=BASE_AT + timedelta(minutes=2),
        )
        assert cancelled.status == "cancelled"
        with pytest.raises(BookingConflictError):
            await transition_booking(
                pool,
                actor_id=RECEPTIONIST_ID,
                business_id=BUSINESS_ID,
                shop_id=SHOP_ID,
                booking_id=winner.booking_id,
                target_status="in_service",
                idempotency_key="booking-invalid-after-cancel-0003c",
                request_id="booking-invalid-after-cancel",
                payload=BookingTransitionRequest(),
                at=BASE_AT + timedelta(minutes=3),
            )

        queue_payload = BookingCreateRequest(
            booking_type="walk_in",
            customer_id=CUSTOMER_ID,
            service_ids=[SERVICE_ID],
        )
        queues = await asyncio.gather(
            *[
                create_booking(
                    pool,
                    actor_id=RECEPTIONIST_ID,
                    business_id=BUSINESS_ID,
                    shop_id=SHOP_ID,
                    idempotency_key=f"booking-queue-parallel-{index:04d}",
                    request_id=f"booking-queue-{index}",
                    payload=queue_payload,
                    at=BASE_AT + timedelta(minutes=15),
                )
                for index in range(4)
            ]
        )
        queue_numbers = [booking.queue_number for booking in queues]
        assert len(set(queue_numbers)) == 4
        assert all(number is not None for number in queue_numbers)

        # Booking and queue correctness is entirely PostgreSQL-backed; no Redis
        # client is accepted or read by any service call above.
        async with pool.connection(timeout=5) as connection:
            cursor = await connection.execute(
                """
                select count(*), count(distinct queue_number)
                from public.bookings
                where shop_id = %s and queue_number is not null
                """,
                (SHOP_ID,),
            )
            durable = await cursor.fetchone()
            assert durable is not None
            assert durable[0] == durable[1]

        promotion_booking = await create_booking(
            pool,
            actor_id=RECEPTIONIST_ID,
            business_id=BUSINESS_ID,
            shop_id=SHOP_ID,
            idempotency_key="booking-promotion-create-0004",
            request_id="booking-promotion-create",
            payload=_appointment(
                datetime(2026, 7, 27, 11, 0, tzinfo=UTC),
                barber_id=barber_ids[1],
            ),
            at=BASE_AT,
        )
        await transition_booking(
            pool,
            actor_id=RECEPTIONIST_ID,
            business_id=BUSINESS_ID,
            shop_id=SHOP_ID,
            booking_id=promotion_booking.booking_id,
            target_status="confirmed",
            idempotency_key="booking-promotion-confirm-0005",
            request_id="booking-promotion-confirm",
            payload=BookingTransitionRequest(),
            at=BASE_AT + timedelta(minutes=1),
        )
        promotion_at = datetime(2026, 7, 27, 10, 31, tzinfo=UTC)
        assert await promote_due_appointments(pool, at=promotion_at) >= 1
        assert await promote_due_appointments(pool, at=promotion_at) == 0

        stale = await create_booking(
            pool,
            actor_id=RECEPTIONIST_ID,
            business_id=BUSINESS_ID,
            shop_id=SHOP_ID,
            idempotency_key="booking-stale-hold-0006",
            request_id="booking-stale-hold",
            payload=_appointment(
                datetime(2026, 7, 27, 12, 0, tzinfo=UTC),
                barber_id=barber_ids[1],
            ),
            at=BASE_AT,
        )
        assert await expire_booking_holds(pool, at=BASE_AT + timedelta(minutes=6)) >= 1
        replacement = await create_booking(
            pool,
            actor_id=RECEPTIONIST_ID,
            business_id=BUSINESS_ID,
            shop_id=SHOP_ID,
            idempotency_key="booking-expired-replacement-0007",
            request_id="booking-expired-replacement",
            payload=_appointment(
                datetime(2026, 7, 27, 12, 0, tzinfo=UTC),
                barber_id=barber_ids[1],
            ),
            at=BASE_AT + timedelta(minutes=6),
        )
        assert replacement.booking_id != stale.booking_id

        with pytest.raises(BookingAccessDeniedError):
            await create_booking(
                pool,
                actor_id=BARBER_USER_ID,
                business_id=BUSINESS_ID,
                shop_id=SHOP_ID,
                idempotency_key="booking-barber-denied-0008",
                request_id="booking-barber-denied",
                payload=queue_payload,
                at=BASE_AT + timedelta(minutes=15),
            )

        async with pool.connection(timeout=5) as connection:
            cursor = await connection.execute(
                """
                select
                  (select count(*) from public.audit_log
                   where action like 'booking.%%') as audits,
                  (select count(*) from public.outbox_events
                   where topic like 'booking.%%') as outbox,
                  (select count(*) from public.booking_services
                   where booking_id = %s) as snapshots,
                  (select queue_number from public.bookings where id = %s) as promoted_number
                """,
                (winner.booking_id, promotion_booking.booking_id),
            )
            evidence = await cursor.fetchone()
            assert evidence is not None
            assert evidence[0] == evidence[1]
            assert evidence[2] == 1
            assert evidence[3] is not None
    finally:
        await pool.close()


async def test_telegram_customer_booking_reauthorizes_inside_transaction() -> None:
    pool = create_database_pool(Settings(_env_file=None))
    await pool.open()
    try:
        payload = BookingCreateRequest(
            booking_type="queue",
            customer_id=CUSTOMER_ID,
            service_ids=[SERVICE_ID],
        )
        created = await create_booking(
            pool,
            actor_id=None,
            business_id=BUSINESS_ID,
            shop_id=SHOP_ID,
            idempotency_key="telegram-customer-create-0001",
            request_id="telegram-customer-create",
            payload=payload,
            telegram_user_id=CUSTOMER_TELEGRAM_ID,
            at=BASE_AT + timedelta(minutes=30),
        )
        replayed = await create_booking(
            pool,
            actor_id=None,
            business_id=BUSINESS_ID,
            shop_id=SHOP_ID,
            idempotency_key="telegram-customer-create-0001",
            request_id="telegram-customer-create-replay",
            payload=payload,
            telegram_user_id=CUSTOMER_TELEGRAM_ID,
            at=BASE_AT + timedelta(minutes=30),
        )
        assert created == replayed
        assert created.status == "requested"
        assert created.customer_id == CUSTOMER_ID

        slots = await find_customer_appointment_slots(
            pool,
            business_id=BUSINESS_ID,
            shop_id=SHOP_ID,
            customer_id=CUSTOMER_ID,
            telegram_user_id=CUSTOMER_TELEGRAM_ID,
            service_ids=[SERVICE_ID],
            day=BASE_AT.date(),
            at=BASE_AT + timedelta(minutes=30),
        )
        assert slots

        cancelled = await transition_booking(
            pool,
            actor_id=None,
            business_id=BUSINESS_ID,
            shop_id=SHOP_ID,
            booking_id=created.booking_id,
            target_status="cancelled",
            idempotency_key="telegram-customer-cancel-0002",
            request_id="telegram-customer-cancel",
            payload=BookingTransitionRequest(reason="customer requested"),
            customer_id=CUSTOMER_ID,
            telegram_user_id=CUSTOMER_TELEGRAM_ID,
            at=BASE_AT + timedelta(minutes=31),
        )
        assert cancelled.status == "cancelled"

        with pytest.raises(BookingAccessDeniedError):
            await create_booking(
                pool,
                actor_id=None,
                business_id=BUSINESS_ID,
                shop_id=SHOP_ID,
                idempotency_key="telegram-foreign-identity-0003",
                request_id="telegram-foreign-identity",
                payload=payload,
                telegram_user_id=7999999998,
                at=BASE_AT + timedelta(minutes=32),
            )

        async with pool.connection(timeout=5) as connection:
            cursor = await connection.execute(
                """
                select b.source::text, a.actor_type::text, a.actor_id
                from public.bookings b
                join public.audit_log a
                  on a.entity_id = b.id and a.action = 'booking.created'
                where b.id = %s
                """,
                (created.booking_id,),
            )
            evidence = await cursor.fetchone()
            assert evidence == ("telegram", "telegram_user", str(CUSTOMER_TELEGRAM_ID))
    finally:
        await pool.close()
