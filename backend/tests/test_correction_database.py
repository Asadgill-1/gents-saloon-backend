import asyncio
import os
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import pytest
from psycopg.errors import InsufficientPrivilege, RaiseException

from app.core.config import Settings
from app.core.database import create_database_pool
from app.services.checkout_service import (
    CheckoutPayment,
    CheckoutRequest,
    checkout,
)
from app.services.correction_service import (
    CorrectionConflictError,
    CorrectionItemRequest,
    CorrectionRequest,
    correct_transaction,
)
from app.services.legal_cash_service import CashShiftOpenRequest, open_cash_shift

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_DATABASE_INTEGRATION") != "1",
    reason="requires reconstructed Phase 2 PostgreSQL test database",
)

PLATFORM_ID = UUID("00000000-0000-0000-0000-000000000001")
OWNER_ID = UUID("00000000-0000-0000-0000-000000000002")
RECEPTIONIST_ID = UUID("00000000-0000-0000-0000-000000000003")
OTHER_OWNER_ID = UUID("00000000-0000-0000-0000-000000000004")
BARBER_ID = UUID("00000000-0000-0000-0000-000000000005")
BUSINESS_ID = UUID("10000000-0000-0000-0000-000000000001")
SHOP_ID = UUID("20000000-0000-0000-0000-000000000001")
SERVICE_ID = UUID("60500000-0000-0000-0000-000000000001")
BOOKING_IDS = [
    UUID("60600000-0000-0000-0000-000000000001"),
    UUID("60600000-0000-0000-0000-000000000002"),
    UUID("60600000-0000-0000-0000-000000000003"),
]
BOOKING_SERVICE_IDS = [
    UUID("60700000-0000-0000-0000-000000000001"),
    UUID("60700000-0000-0000-0000-000000000002"),
    UUID("60700000-0000-0000-0000-000000000003"),
]
BASE_AT = datetime(2026, 7, 27, 8, 0, tzinfo=UTC)


async def _seed_bookings(pool: object) -> None:
    async with pool.connection(timeout=5) as connection, connection.transaction():  # type: ignore[attr-defined]
        membership_cursor = await connection.execute(
            """
            select id
            from public.shop_memberships
            where business_id = %s
              and shop_id = %s
              and auth_user_id = %s
              and role = 'barber'
            """,
            (BUSINESS_ID, SHOP_ID, BARBER_ID),
        )
        membership = await membership_cursor.fetchone()
        assert membership is not None
        barber_membership_id = membership[0]
        await connection.execute(
            """
            insert into public.services (
              id, business_id, shop_id, name, price_gross, vat_rate,
              duration_minutes
            )
            values (%s, %s, %s, 'Correction Service', 105, 5, 30)
            on conflict (id) do nothing
            """,
            (SERVICE_ID, BUSINESS_ID, SHOP_ID),
        )
        for index, (booking_id, booking_service_id) in enumerate(
            zip(BOOKING_IDS, BOOKING_SERVICE_IDS, strict=True)
        ):
            start_hour = 12 + index
            await connection.execute(
                """
                insert into public.bookings (
                  id, business_id, shop_id, barber_membership_id,
                  booking_type, status, source, scheduled_start,
                  scheduled_end, confirmed_at, started_at, completed_at
                )
                values (
                  %s, %s, %s, %s, 'appointment', 'completed', 'dashboard',
                  make_timestamptz(2026, 7, 27, %s, 0, 0, 'Asia/Dubai'),
                  make_timestamptz(2026, 7, 27, %s, 30, 0, 'Asia/Dubai'),
                  '2026-07-27 10:00:00+04',
                  make_timestamptz(2026, 7, 27, %s, 0, 0, 'Asia/Dubai'),
                  make_timestamptz(2026, 7, 27, %s, 30, 0, 'Asia/Dubai')
                )
                on conflict (id) do nothing
                """,
                (
                    booking_id,
                    BUSINESS_ID,
                    SHOP_ID,
                    barber_membership_id,
                    start_hour,
                    start_hour,
                    start_hour,
                    start_hour,
                ),
            )
            await connection.execute(
                """
                insert into public.booking_services (
                  id, business_id, shop_id, booking_id, service_id,
                  service_name, price_gross, vat_rate, duration_minutes,
                  sort_order
                )
                values (
                  %s, %s, %s, %s, %s, 'Correction Service',
                  105, 5, 30, 0
                )
                on conflict (id) do nothing
                """,
                (
                    booking_service_id,
                    BUSINESS_ID,
                    SHOP_ID,
                    booking_id,
                    SERVICE_ID,
                ),
            )


async def _visible_counts(pool: object, actor_id: UUID) -> tuple[int, ...]:
    async with pool.connection(timeout=5) as connection, connection.transaction():  # type: ignore[attr-defined]
        await connection.execute("set local role authenticated")
        await connection.execute(
            "select set_config('request.jwt.claim.sub', %s, true)",
            (str(actor_id),),
        )
        cursor = await connection.execute(
            """
            select
              (select count(*) from public.transaction_corrections),
              (select count(*) from public.transaction_correction_items),
              (
                select count(*)
                from public.transaction_correction_item_commissions
              ),
              (select count(*) from public.transaction_correction_payments)
            """
        )
        row = await cursor.fetchone()
        assert row is not None
        return tuple(int(value) for value in row)


async def _exercise_correction_contracts() -> None:
    pool = create_database_pool(Settings(_env_file=None))
    await pool.open(wait=True)
    try:
        await _seed_bookings(pool)
        shift = await open_cash_shift(
            pool,
            actor_id=RECEPTIONIST_ID,
            business_id=BUSINESS_ID,
            shop_id=SHOP_ID,
            idempotency_key="shift-shift-shift",
            request_id="correction-shift",
            payload=CashShiftOpenRequest(
                register_label="Correction Desk",
                opening_float=Decimal("100.00"),
            ),
            at=BASE_AT,
        )
        refundable = await checkout(
            pool,
            actor_id=RECEPTIONIST_ID,
            business_id=BUSINESS_ID,
            shop_id=SHOP_ID,
            idempotency_key="sale-sale-sale-a1",
            request_id="correction-sale-1",
            payload=CheckoutRequest(
                booking_id=BOOKING_IDS[0],
                payments=[
                    CheckoutPayment(method="cash", amount=Decimal("50.00")),
                    CheckoutPayment(
                        method="card",
                        amount=Decimal("60.00"),
                        card_slip_reference="SALE-REFUND-0001",
                    ),
                ],
                tip_amount=Decimal("5.00"),
                cash_shift_id=shift.cash_shift_id,
            ),
            at=BASE_AT,
        )
        voidable = await checkout(
            pool,
            actor_id=RECEPTIONIST_ID,
            business_id=BUSINESS_ID,
            shop_id=SHOP_ID,
            idempotency_key="sale-sale-sale-b1",
            request_id="correction-sale-2",
            payload=CheckoutRequest(
                booking_id=BOOKING_IDS[1],
                payments=[
                    CheckoutPayment(method="cash", amount=Decimal("105.00")),
                ],
                cash_shift_id=shift.cash_shift_id,
            ),
            at=BASE_AT,
        )
        card_only = await checkout(
            pool,
            actor_id=RECEPTIONIST_ID,
            business_id=BUSINESS_ID,
            shop_id=SHOP_ID,
            idempotency_key="sale-sale-sale-c1",
            request_id="correction-sale-3",
            payload=CheckoutRequest(
                booking_id=BOOKING_IDS[2],
                payments=[
                    CheckoutPayment(
                        method="card",
                        amount=Decimal("105.00"),
                        card_slip_reference="SALE-REFUND-0003",
                    ),
                ],
            ),
            at=BASE_AT,
        )
        async with pool.connection(timeout=5) as connection:
            item_cursor = await connection.execute(
                """
                select id
                from public.transaction_items
                where transaction_id = %s
                """,
                (refundable.transaction_id,),
            )
            item_row = await item_cursor.fetchone()
            assert item_row is not None
            transaction_item_id = UUID(str(item_row[0]))

        with pytest.raises(CorrectionConflictError, match="cash-only original tender"):
            await correct_transaction(
                pool,
                actor_id=RECEPTIONIST_ID,
                business_id=BUSINESS_ID,
                shop_id=SHOP_ID,
                transaction_id=refundable.transaction_id,
                idempotency_key="split-void-split-a",
                request_id="correction-split-void",
                payload=CorrectionRequest(
                    kind="void",
                    cash_shift_id=shift.cash_shift_id,
                    reason="Split tender cannot be voided",
                ),
                at=BASE_AT,
            )

        first_payload = CorrectionRequest(
            kind="refund",
            items=[
                CorrectionItemRequest(
                    transaction_item_id=transaction_item_id,
                    amount=Decimal("52.50"),
                )
            ],
            payments=[
                CheckoutPayment(method="cash", amount=Decimal("25.00")),
                CheckoutPayment(
                    method="card",
                    amount=Decimal("30.00"),
                    card_slip_reference="REFUND-0001",
                ),
            ],
            tip_refund=Decimal("2.50"),
            cash_shift_id=shift.cash_shift_id,
            reason="Partial customer refund",
        )
        first, replay = await asyncio.gather(
            correct_transaction(
                pool,
                actor_id=RECEPTIONIST_ID,
                business_id=BUSINESS_ID,
                shop_id=SHOP_ID,
                transaction_id=refundable.transaction_id,
                idempotency_key="refund-refund-aa",
                request_id="correction-refund-a",
                payload=first_payload,
                at=BASE_AT,
            ),
            correct_transaction(
                pool,
                actor_id=RECEPTIONIST_ID,
                business_id=BUSINESS_ID,
                shop_id=SHOP_ID,
                transaction_id=refundable.transaction_id,
                idempotency_key="refund-refund-aa",
                request_id="correction-refund-b",
                payload=first_payload,
                at=BASE_AT,
            ),
        )
        assert first == replay
        assert first.net_refund == Decimal("50.00")
        assert first.vat_refund == Decimal("2.50")
        assert first.tip_refund == Decimal("2.50")
        assert first.grand_total == Decimal("55.00")

        remaining_payload = CorrectionRequest(
            kind="refund",
            items=[
                CorrectionItemRequest(
                    transaction_item_id=transaction_item_id,
                    amount=Decimal("52.50"),
                )
            ],
            payments=[
                CheckoutPayment(method="cash", amount=Decimal("25.00")),
                CheckoutPayment(
                    method="card",
                    amount=Decimal("30.00"),
                    card_slip_reference="REFUND-0002",
                ),
            ],
            tip_refund=Decimal("2.50"),
            cash_shift_id=shift.cash_shift_id,
            reason="Complete remaining refund",
        )
        competing = await asyncio.gather(
            *[
                correct_transaction(
                    pool,
                    actor_id=RECEPTIONIST_ID,
                    business_id=BUSINESS_ID,
                    shop_id=SHOP_ID,
                    transaction_id=refundable.transaction_id,
                    idempotency_key=f"race-race-race-{index}",
                    request_id=f"correction-race-{index}",
                    payload=remaining_payload,
                    at=BASE_AT,
                )
                for index in (1, 2)
            ],
            return_exceptions=True,
        )
        winners = [result for result in competing if not isinstance(result, Exception)]
        conflicts = [result for result in competing if isinstance(result, CorrectionConflictError)]
        assert len(winners) == len(conflicts) == 1

        voided = await correct_transaction(
            pool,
            actor_id=RECEPTIONIST_ID,
            business_id=BUSINESS_ID,
            shop_id=SHOP_ID,
            transaction_id=voidable.transaction_id,
            idempotency_key="void-void-void-aa",
            request_id="correction-void",
            payload=CorrectionRequest(
                kind="void",
                cash_shift_id=shift.cash_shift_id,
                reason="Duplicate sale",
            ),
            at=BASE_AT,
        )
        assert voided.service_gross_refund == Decimal("105.00")
        assert voided.grand_total == Decimal("105.00")

        with pytest.raises(CorrectionConflictError, match="original cash shift"):
            await correct_transaction(
                pool,
                actor_id=RECEPTIONIST_ID,
                business_id=BUSINESS_ID,
                shop_id=SHOP_ID,
                transaction_id=card_only.transaction_id,
                idempotency_key="card-void-card-aa",
                request_id="correction-card-void",
                payload=CorrectionRequest(
                    kind="void",
                    cash_shift_id=shift.cash_shift_id,
                    reason="Duplicate card sale",
                ),
                at=BASE_AT,
            )

        async with pool.connection(timeout=5) as connection:
            with pytest.raises(RaiseException, match="cash-only original tender"):
                async with connection.transaction():
                    bypass_cursor = await connection.execute(
                        """
                        insert into public.transaction_corrections (
                          business_id, shop_id, original_transaction_id,
                          barber_membership_id, cash_shift_id, kind,
                          credit_note_number, service_gross_refund, net_refund,
                          vat_refund, tip_refund, grand_total, reason,
                          legal_snapshot, created_by_auth_user_id, created_at
                        )
                        select
                          business_id, shop_id, id, barber_membership_id,
                          cash_shift_id, 'void', 'CN-DB-BYPASS',
                          service_gross_total, net_total, vat_total, tip_total,
                          grand_total, 'Database bypass attempt', legal_snapshot,
                          %s, %s
                        from public.transactions
                        where id = %s
                        returning id
                        """,
                        (RECEPTIONIST_ID, BASE_AT, refundable.transaction_id),
                    )
                    bypass_row = await bypass_cursor.fetchone()
                    assert bypass_row is not None
                    await connection.execute(
                        "select private.validate_correction_void_tender(%s)",
                        (bypass_row[0],),
                    )

        async with pool.connection(timeout=5) as connection:
            totals_cursor = await connection.execute(
                """
                select
                  count(*),
                  sum(service_gross_refund),
                  sum(tip_refund),
                  (
                    select count(*)
                    from public.journal_entries
                    where source_type = 'correction'
                  ),
                    (
                      select coalesce(sum(amount), 0)
                      from public.cash_shift_movements
                      where movement_type = 'refund'
                        and source_entity_id in (
                          select id
                          from public.transaction_corrections
                          where original_transaction_id in (%s, %s)
                        )
                  ),
                  (
                    select refunded_total
                    from public.transactions
                    where id = %s
                  )
                from public.transaction_corrections
                where original_transaction_id in (%s, %s)
                """,
                (
                    refundable.transaction_id,
                    voidable.transaction_id,
                    refundable.transaction_id,
                    refundable.transaction_id,
                    voidable.transaction_id,
                ),
            )
            totals = await totals_cursor.fetchone()
            assert totals == (
                3,
                Decimal("210.00"),
                Decimal("5.00"),
                3,
                Decimal("155.00"),
                Decimal("0.00"),
            )

        receptionist = await _visible_counts(pool, RECEPTIONIST_ID)
        barber = await _visible_counts(pool, BARBER_ID)
        owner = await _visible_counts(pool, OWNER_ID)
        other_owner = await _visible_counts(pool, OTHER_OWNER_ID)
        platform = await _visible_counts(pool, PLATFORM_ID)
        assert receptionist == (3, 3, 0, 5)
        assert barber == (3, 3, 3, 0)
        assert owner == (3, 3, 3, 5)
        assert other_owner == (0, 0, 0, 0)
        assert platform == (3, 3, 3, 5)

        async with pool.connection(timeout=5) as connection, connection.transaction():
            await connection.execute("set local role authenticated")
            await connection.execute(
                "select set_config('request.jwt.claim.sub', %s, true)",
                (str(RECEPTIONIST_ID),),
            )
            with pytest.raises(InsufficientPrivilege):
                await connection.execute(
                    "delete from public.transaction_corrections where id = %s",
                    (voided.correction_id,),
                )
        async with pool.connection(timeout=5) as connection:
            with pytest.raises(RaiseException, match="append-only"):
                async with connection.transaction():
                    await connection.execute(
                        """
                        update public.transaction_corrections
                        set reason = 'Changed reason'
                        where id = %s
                        """,
                        (voided.correction_id,),
                    )
    finally:
        await pool.close()


def test_correction_database_contracts() -> None:
    asyncio.run(_exercise_correction_contracts())
