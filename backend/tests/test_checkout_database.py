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
BOOKING_ID = UUID("57000000-0000-0000-0000-000000000001")
GOLDEN_MEMBERSHIP_ID = UUID("59400000-0000-0000-0000-000000000001")
GOLDEN_SERVICE_ID = UUID("59500000-0000-0000-0000-000000000001")
GOLDEN_BOOKING_ID = UUID("59600000-0000-0000-0000-000000000001")
GOLDEN_BOOKING_SERVICE_ID = UUID("59700000-0000-0000-0000-000000000001")
BASE_AT = datetime(2026, 7, 27, 7, 0, tzinfo=UTC)


async def _seed_golden_booking(pool: object) -> None:
    async with pool.connection(timeout=5) as connection, connection.transaction():  # type: ignore[attr-defined]
        await connection.execute(
            """
            insert into public.shop_memberships (
              id, business_id, shop_id, auth_user_id, role, display_name
            )
            values (%s, %s, %s, %s, 'barber', 'Owner Barber')
            on conflict (id) do nothing
            """,
            (GOLDEN_MEMBERSHIP_ID, BUSINESS_ID, SHOP_ID, OWNER_ID),
        )
        await connection.execute(
            """
            insert into public.services (
              id, business_id, shop_id, name, price_gross, vat_rate,
              duration_minutes
            )
            values (%s, %s, %s, 'Golden Service', 120, 0, 30)
            on conflict (id) do nothing
            """,
            (GOLDEN_SERVICE_ID, BUSINESS_ID, SHOP_ID),
        )
        await connection.execute(
            """
            insert into public.bookings (
              id, business_id, shop_id, barber_membership_id, booking_type,
              status, source, scheduled_start, scheduled_end, confirmed_at,
              started_at, completed_at
            )
            values (
              %s, %s, %s, %s, 'appointment', 'completed', 'dashboard',
              '2026-07-27 11:00:00+04', '2026-07-27 11:30:00+04',
              '2026-07-27 10:00:00+04', '2026-07-27 11:00:00+04',
              '2026-07-27 11:30:00+04'
            )
            on conflict (id) do nothing
            """,
            (GOLDEN_BOOKING_ID, BUSINESS_ID, SHOP_ID, GOLDEN_MEMBERSHIP_ID),
        )
        await connection.execute(
            """
            insert into public.booking_services (
              id, business_id, shop_id, booking_id, service_id, service_name,
              price_gross, vat_rate, duration_minutes, sort_order
            )
            values (%s, %s, %s, %s, %s, 'Golden Service', 120, 0, 30, 0)
            on conflict (id) do nothing
            """,
            (
                GOLDEN_BOOKING_SERVICE_ID,
                BUSINESS_ID,
                SHOP_ID,
                GOLDEN_BOOKING_ID,
                GOLDEN_SERVICE_ID,
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
              (select count(*) from public.transactions),
              (select count(*) from public.transaction_items),
              (select count(*) from public.transaction_payments),
              (select count(*) from public.transaction_item_commissions),
              (select count(*) from public.journal_entries),
              (select count(*) from public.journal_postings)
            """
        )
        row = await cursor.fetchone()
        assert row is not None
        return tuple(int(value) for value in row)


async def _exercise_checkout_contracts() -> None:
    pool = create_database_pool(Settings(_env_file=None))
    await pool.open(wait=True)
    try:
        await _seed_golden_booking(pool)
        shift = await open_cash_shift(
            pool,
            actor_id=RECEPTIONIST_ID,
            business_id=BUSINESS_ID,
            shop_id=SHOP_ID,
            idempotency_key="checkout-shift-0001",
            request_id="checkout-shift",
            payload=CashShiftOpenRequest(
                register_label="Checkout Desk",
                opening_float=Decimal("100.00"),
            ),
            at=BASE_AT,
        )
        split_payload = CheckoutRequest(
            booking_id=BOOKING_ID,
            payments=[
                CheckoutPayment(method="cash", amount=Decimal("50.00")),
                CheckoutPayment(
                    method="card",
                    amount=Decimal("80.00"),
                    card_slip_reference="APPROVAL-0001",
                ),
            ],
            tip_amount=Decimal("10.00"),
            cash_shift_id=shift.cash_shift_id,
        )
        first, replay = await asyncio.gather(
            checkout(
                pool,
                actor_id=RECEPTIONIST_ID,
                business_id=BUSINESS_ID,
                shop_id=SHOP_ID,
                idempotency_key="checkout-replay-0001",  # gitleaks:allow
                request_id="checkout-a",
                payload=split_payload,
                at=BASE_AT,
            ),
            checkout(
                pool,
                actor_id=RECEPTIONIST_ID,
                business_id=BUSINESS_ID,
                shop_id=SHOP_ID,
                idempotency_key="checkout-replay-0001",  # gitleaks:allow
                request_id="checkout-b",
                payload=split_payload,
                at=BASE_AT,
            ),
        )
        assert first == replay
        assert first.net_total == Decimal("114.29")
        assert first.vat_total == Decimal("5.71")
        assert first.tip_total == Decimal("10.00")
        assert first.grand_total == Decimal("130.00")

        golden = await checkout(
            pool,
            actor_id=OWNER_ID,
            business_id=BUSINESS_ID,
            shop_id=SHOP_ID,
            idempotency_key="checkout-golden-0002",  # gitleaks:allow
            request_id="checkout-golden",
            payload=CheckoutRequest(
                booking_id=GOLDEN_BOOKING_ID,
                payments=[
                    CheckoutPayment(
                        method="card",
                        amount=Decimal("125.00"),
                        card_slip_reference="APPROVAL-0002",
                    )
                ],
                tip_amount=Decimal("5.00"),
            ),
            at=BASE_AT,
        )
        assert golden.net_total == Decimal("120.00")
        assert golden.vat_total == Decimal("0.00")
        assert golden.grand_total == Decimal("125.00")

        async with pool.connection(timeout=5) as connection:
            cursor = await connection.execute(
                """
                select
                  tic.barber_commission,
                  tic.shop_share,
                  t.tip_total,
                  (
                    select coalesce(sum(jp.debit), 0)
                    from public.journal_entries je
                    join public.journal_postings jp
                      on jp.journal_entry_id = je.id
                    where je.source_entity_id = t.id
                  ),
                  (
                    select coalesce(sum(jp.credit), 0)
                    from public.journal_entries je
                    join public.journal_postings jp
                      on jp.journal_entry_id = je.id
                    where je.source_entity_id = t.id
                  )
                from public.transactions t
                join public.transaction_item_commissions tic
                  on tic.transaction_id = t.id
                where t.id = %s
                """,
                (golden.transaction_id,),
            )
            evidence = await cursor.fetchone()
            assert evidence == (
                Decimal("25.00"),
                Decimal("95.00"),
                Decimal("5.00"),
                Decimal("125.00"),
                Decimal("125.00"),
            )
            cash_cursor = await connection.execute(
                """
                select count(*), sum(amount)
                from public.cash_shift_movements
                where source_entity_id = %s and movement_type = 'cash_sale'
                """,
                (first.transaction_id,),
            )
            assert await cash_cursor.fetchone() == (1, Decimal("50.00"))

        receptionist = await _visible_counts(pool, RECEPTIONIST_ID)
        barber = await _visible_counts(pool, BARBER_ID)
        owner = await _visible_counts(pool, OWNER_ID)
        other_owner = await _visible_counts(pool, OTHER_OWNER_ID)
        platform = await _visible_counts(pool, PLATFORM_ID)
        assert receptionist[:4] == (2, 2, 3, 0)
        assert receptionist[4:] == (0, 0)
        assert barber[:4] == (1, 1, 0, 1)
        assert barber[4:] == (0, 0)
        assert owner[0] == owner[1] == owner[3] == owner[4] == 2
        assert owner[2] == 3
        assert owner[5] >= 8
        assert other_owner == (0, 0, 0, 0, 0, 0)
        assert platform[0] == platform[1] == platform[3] == platform[4] == 2

        async with pool.connection(timeout=5) as connection, connection.transaction():
            await connection.execute("set local role authenticated")
            await connection.execute(
                "select set_config('request.jwt.claim.sub', %s, true)",
                (str(RECEPTIONIST_ID),),
            )
            with pytest.raises(InsufficientPrivilege):
                await connection.execute(
                    "delete from public.transactions where id = %s",
                    (first.transaction_id,),
                )
        async with pool.connection(timeout=5) as connection:
            with pytest.raises(RaiseException, match="transactions is append-only"):
                async with connection.transaction():
                    await connection.execute(
                        "update public.transactions set tip_total = 0 where id = %s",
                        (first.transaction_id,),
                    )
    finally:
        await pool.close()


def test_checkout_database_contracts() -> None:
    asyncio.run(_exercise_checkout_contracts())
