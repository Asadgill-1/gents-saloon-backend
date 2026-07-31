import asyncio
import os
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import pytest
from psycopg.errors import InsufficientPrivilege, RaiseException

from app.core.config import Settings
from app.core.database import create_database_pool
from app.services.checkout_service import CheckoutPayment, CheckoutRequest, checkout
from app.services.correction_service import (
    CorrectionItemRequest,
    CorrectionRequest,
    correct_transaction,
)
from app.services.legal_cash_service import CashShiftOpenRequest, open_cash_shift
from app.services.payout_service import (
    AdvanceRequest,
    FinanceAccessDeniedError,
    FinanceConflictError,
    PayoutActionRequest,
    PayoutAdjustment,
    PayoutPayRequest,
    PayoutRunRequest,
    approve_payout_run,
    cancel_payout_run,
    create_payout_run,
    grant_advance,
    pay_payout_run,
)
from app.services.report_service import (
    ReportAccessDeniedError,
    get_business_overview,
    get_shop_report,
)

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_DATABASE_INTEGRATION") != "1",
    reason="requires reconstructed Phase 2 PostgreSQL test database",
)

PLATFORM_ID = UUID("00000000-0000-0000-0000-000000000001")
OWNER_ID = UUID("00000000-0000-0000-0000-000000000002")
RECEPTIONIST_ID = UUID("00000000-0000-0000-0000-000000000003")
OTHER_OWNER_ID = UUID("00000000-0000-0000-0000-000000000004")
BARBER_ID = UUID("00000000-0000-0000-0000-000000000005")
MANAGER_ID = UUID("00000000-0000-0000-0000-000000000010")
BUSINESS_ID = UUID("10000000-0000-0000-0000-000000000001")
SHOP_ID = UUID("20000000-0000-0000-0000-000000000001")
REPORT_SHOP_ID = UUID("20000000-0000-0000-0000-000000000004")
SERVICE_ID = UUID("70500000-0000-0000-0000-000000000001")
BOOKING_ID = UUID("70600000-0000-0000-0000-000000000001")
BOOKING_SERVICE_ID = UUID("70700000-0000-0000-0000-000000000001")
SALE_AT = datetime(2026, 8, 10, 8, 0, tzinfo=UTC)
REFUND_AT = datetime(2026, 8, 10, 9, 0, tzinfo=UTC)
RUN_AT = datetime(2026, 8, 12, 8, 0, tzinfo=UTC)
PERIOD_START = datetime(2026, 8, 10, 0, 0, tzinfo=UTC)
PERIOD_END = datetime(2026, 8, 11, 0, 0, tzinfo=UTC)


async def _seed_booking(pool: object) -> UUID:
    async with pool.connection(timeout=5) as connection, connection.transaction():  # type: ignore[attr-defined]
        await connection.execute(
            """
            insert into auth.users (id, email)
            values (%s, 'report-manager@example.test')
            on conflict (id) do nothing
            """,
            (MANAGER_ID,),
        )
        await connection.execute(
            """
            insert into public.user_profiles (auth_user_id, display_name)
            values (%s, 'Report Manager')
            on conflict (auth_user_id) do nothing
            """,
            (MANAGER_ID,),
        )
        await connection.execute(
            """
            insert into public.shops (
              id, business_id, name, internal_code, public_queue_token_hash,
              open_time, close_time, default_service_minutes, eod_time
            )
            values (
              %s, %s, 'Report Shop', 'AR',
              'report-shop-public-token-hash',
              '09:00', '23:00', 30, '23:30'
            )
            on conflict (id) do nothing
            """,
            (REPORT_SHOP_ID, BUSINESS_ID),
        )
        await connection.execute(
            """
            insert into public.shop_memberships (
              business_id, shop_id, auth_user_id, role, display_name
            )
            values (%s, %s, %s, 'manager', 'Report Manager')
            """,
            (BUSINESS_ID, SHOP_ID, MANAGER_ID),
        )
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
        barber_membership_id = UUID(str(membership[0]))
        await connection.execute(
            """
            insert into public.services (
              id, business_id, shop_id, name, price_gross, vat_rate,
              duration_minutes
            )
            values (%s, %s, %s, 'Payout Service', 120, 0, 30)
            on conflict (id) do nothing
            """,
            (SERVICE_ID, BUSINESS_ID, SHOP_ID),
        )
        await connection.execute(
            """
            insert into public.bookings (
              id, business_id, shop_id, barber_membership_id,
              booking_type, status, source, scheduled_start,
              scheduled_end, confirmed_at, started_at, completed_at
            )
            values (
              %s, %s, %s, %s, 'appointment', 'completed', 'dashboard',
              '2026-08-10 12:00:00+04', '2026-08-10 12:30:00+04',
              '2026-08-10 11:00:00+04', '2026-08-10 12:00:00+04',
              '2026-08-10 12:30:00+04'
            )
            on conflict (id) do nothing
            """,
            (BOOKING_ID, BUSINESS_ID, SHOP_ID, barber_membership_id),
        )
        await connection.execute(
            """
            insert into public.booking_services (
              id, business_id, shop_id, booking_id, service_id,
              service_name, price_gross, vat_rate, duration_minutes,
              sort_order
            )
            values (
              %s, %s, %s, %s, %s, 'Payout Service', 120, 0, 30, 0
            )
            on conflict (id) do nothing
            """,
            (
                BOOKING_SERVICE_ID,
                BUSINESS_ID,
                SHOP_ID,
                BOOKING_ID,
                SERVICE_ID,
            ),
        )
        return barber_membership_id


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
              (select count(*) from public.advances),
              (select count(*) from public.payout_runs),
              (select count(*) from public.payout_items),
              (select count(*) from public.advance_applications)
            """
        )
        row = await cursor.fetchone()
        assert row is not None
        return tuple(int(value) for value in row)


async def _exercise_payout_contracts() -> None:
    pool = create_database_pool(Settings(_env_file=None))
    await pool.open(wait=True)
    try:
        barber_membership_id = await _seed_booking(pool)
        shift = await open_cash_shift(
            pool,
            actor_id=RECEPTIONIST_ID,
            business_id=BUSINESS_ID,
            shop_id=SHOP_ID,
            idempotency_key="payout-shift-0001",
            request_id="payout-shift",
            payload=CashShiftOpenRequest(
                register_label="Payout Desk",
                opening_float=Decimal("500.00"),
            ),
            at=SALE_AT,
        )
        advance_payload = AdvanceRequest(
            barber_membership_id=barber_membership_id,
            cash_shift_id=shift.cash_shift_id,
            amount=Decimal("5.00"),
            note="Approved cash advance",
        )
        advance, advance_replay = await asyncio.gather(
            grant_advance(
                pool,
                actor_id=OWNER_ID,
                business_id=BUSINESS_ID,
                shop_id=SHOP_ID,
                idempotency_key="advance-advance-01",
                request_id="advance-a",
                payload=advance_payload,
                at=SALE_AT,
            ),
            grant_advance(
                pool,
                actor_id=OWNER_ID,
                business_id=BUSINESS_ID,
                shop_id=SHOP_ID,
                idempotency_key="advance-advance-01",
                request_id="advance-b",
                payload=advance_payload,
                at=SALE_AT,
            ),
        )
        assert advance == advance_replay
        with pytest.raises(FinanceAccessDeniedError):
            await grant_advance(
                pool,
                actor_id=RECEPTIONIST_ID,
                business_id=BUSINESS_ID,
                shop_id=SHOP_ID,
                idempotency_key="advance-denied-001",
                request_id="advance-denied",
                payload=advance_payload,
                at=SALE_AT,
            )

        transaction = await checkout(
            pool,
            actor_id=RECEPTIONIST_ID,
            business_id=BUSINESS_ID,
            shop_id=SHOP_ID,
            idempotency_key="payout-checkout-01",
            request_id="payout-checkout",
            payload=CheckoutRequest(
                booking_id=BOOKING_ID,
                payments=[CheckoutPayment(method="cash", amount=Decimal("130.00"))],
                tip_amount=Decimal("10.00"),
                cash_shift_id=shift.cash_shift_id,
            ),
            at=SALE_AT,
        )
        async with pool.connection(timeout=5) as connection:
            item_cursor = await connection.execute(
                "select id from public.transaction_items where transaction_id = %s",
                (transaction.transaction_id,),
            )
            transaction_item = await item_cursor.fetchone()
            assert transaction_item is not None
        await correct_transaction(
            pool,
            actor_id=RECEPTIONIST_ID,
            business_id=BUSINESS_ID,
            shop_id=SHOP_ID,
            transaction_id=transaction.transaction_id,
            idempotency_key="payout-refund-0001",
            request_id="payout-refund",
            payload=CorrectionRequest(
                kind="refund",
                items=[
                    CorrectionItemRequest(
                        transaction_item_id=UUID(str(transaction_item[0])),
                        amount=Decimal("60.00"),
                    )
                ],
                payments=[CheckoutPayment(method="cash", amount=Decimal("65.00"))],
                tip_refund=Decimal("5.00"),
                cash_shift_id=shift.cash_shift_id,
                reason="Partial refund before payout close",
            ),
            at=REFUND_AT,
        )

        run_payload = PayoutRunRequest(
            period_start=PERIOD_START,
            period_end=PERIOD_END,
            adjustments=[
                PayoutAdjustment(
                    barber_membership_id=barber_membership_id,
                    amount=Decimal("2.00"),
                    reason="Approved performance bonus",
                )
            ],
        )
        draft, draft_replay = await asyncio.gather(
            create_payout_run(
                pool,
                actor_id=OWNER_ID,
                business_id=BUSINESS_ID,
                shop_id=SHOP_ID,
                idempotency_key="payout-create-0001",
                request_id="payout-create-a",
                payload=run_payload,
                at=RUN_AT,
            ),
            create_payout_run(
                pool,
                actor_id=OWNER_ID,
                business_id=BUSINESS_ID,
                shop_id=SHOP_ID,
                idempotency_key="payout-create-0001",
                request_id="payout-create-b",
                payload=run_payload,
                at=RUN_AT,
            ),
        )
        assert draft == draft_replay
        assert draft.status == "draft"
        assert len(draft.items) == 1
        item = draft.items[0]
        assert item.commission_earnings == Decimal("36.00")
        assert item.tip_earnings == Decimal("10.00")
        assert item.commission_reversals == Decimal("18.00")
        assert item.tip_reversals == Decimal("5.00")
        assert item.gross_payable == Decimal("25.00")

        with pytest.raises(FinanceConflictError, match="overlaps"):
            await create_payout_run(
                pool,
                actor_id=OWNER_ID,
                business_id=BUSINESS_ID,
                shop_id=SHOP_ID,
                idempotency_key="payout-overlap-001",
                request_id="payout-overlap",
                payload=run_payload,
                at=RUN_AT,
            )

        approved = await approve_payout_run(
            pool,
            actor_id=OWNER_ID,
            business_id=BUSINESS_ID,
            shop_id=SHOP_ID,
            payout_run_id=draft.payout_run_id,
            idempotency_key="payout-approve-001",
            request_id="payout-approve",
            payload=PayoutActionRequest(),
            at=RUN_AT,
        )
        assert approved.items[0].advance_deduction == Decimal("5.00")
        assert approved.items[0].net_paid == Decimal("20.00")

        pay_payload = PayoutPayRequest(cash_shift_id=shift.cash_shift_id)
        competing_payments = await asyncio.gather(
            pay_payout_run(
                pool,
                actor_id=OWNER_ID,
                business_id=BUSINESS_ID,
                shop_id=SHOP_ID,
                payout_run_id=draft.payout_run_id,
                idempotency_key="payout-payment-001",
                request_id="payout-pay-a",
                payload=pay_payload,
                at=RUN_AT,
            ),
            pay_payout_run(
                pool,
                actor_id=OWNER_ID,
                business_id=BUSINESS_ID,
                shop_id=SHOP_ID,
                payout_run_id=draft.payout_run_id,
                idempotency_key="payout-payment-002",
                request_id="payout-pay-b",
                payload=pay_payload,
                at=RUN_AT,
            ),
            return_exceptions=True,
        )
        winners = [result for result in competing_payments if not isinstance(result, Exception)]
        conflicts = [
            result for result in competing_payments if isinstance(result, FinanceConflictError)
        ]
        assert len(winners) == len(conflicts) == 1
        paid = winners[0]
        retries = await asyncio.gather(
            *[
                pay_payout_run(
                    pool,
                    actor_id=OWNER_ID,
                    business_id=BUSINESS_ID,
                    shop_id=SHOP_ID,
                    payout_run_id=draft.payout_run_id,
                    idempotency_key=key,
                    request_id=f"payout-pay-replay-{key[-1]}",
                    payload=pay_payload,
                    at=RUN_AT,
                )
                for key in ("payout-payment-001", "payout-payment-002")
            ],
            return_exceptions=True,
        )
        paid_replays = [result for result in retries if not isinstance(result, Exception)]
        assert len(paid_replays) == 1
        paid_replay = paid_replays[0]
        assert paid == paid_replay
        assert paid.status == "paid"

        cancel_draft = await create_payout_run(
            pool,
            actor_id=OWNER_ID,
            business_id=BUSINESS_ID,
            shop_id=SHOP_ID,
            idempotency_key="payout-cancel-create",
            request_id="payout-cancel-create",
            payload=PayoutRunRequest(
                period_start=datetime(2026, 8, 20, tzinfo=UTC),
                period_end=datetime(2026, 8, 21, tzinfo=UTC),
                adjustments=[
                    PayoutAdjustment(
                        barber_membership_id=barber_membership_id,
                        amount=Decimal("10.00"),
                        reason="Approved one-time bonus",
                    )
                ],
            ),
            at=datetime(2026, 8, 22, tzinfo=UTC),
        )
        cancelled = await cancel_payout_run(
            pool,
            actor_id=OWNER_ID,
            business_id=BUSINESS_ID,
            shop_id=SHOP_ID,
            payout_run_id=cancel_draft.payout_run_id,
            idempotency_key="payout-cancel-0001",
            request_id="payout-cancel",
            payload=PayoutActionRequest(),
            at=datetime(2026, 8, 22, tzinfo=UTC),
        )
        assert cancelled.status == "cancelled"

        async with pool.connection(timeout=5) as connection:
            cursor = await connection.execute(
                """
                select
                  (select count(*) from public.advances),
                  (
                    select outstanding_amount
                    from public.advances
                    where id = %s
                  ),
                  (select count(*) from public.advance_applications),
                  (
                    select coalesce(sum(amount), 0)
                    from public.advance_applications
                  ),
                  (
                    select count(*)
                    from public.cash_shift_movements
                    where movement_type = 'advance'
                      and source_entity_id = %s
                  ),
                  (
                    select coalesce(sum(amount), 0)
                    from public.cash_shift_movements
                    where movement_type = 'payout'
                      and source_entity_id = %s
                  ),
                  (
                    select count(*)
                    from public.journal_entries
                    where source_type in ('advance', 'payout')
                      and source_entity_id in (%s, %s)
                  )
                """,
                (
                    advance.advance_id,
                    advance.advance_id,
                    paid.payout_run_id,
                    advance.advance_id,
                    paid.payout_run_id,
                ),
            )
            totals = await cursor.fetchone()
            assert totals == (
                1,
                Decimal("0.00"),
                1,
                Decimal("5.00"),
                1,
                Decimal("20.00"),
                2,
            )

        assert await _visible_counts(pool, RECEPTIONIST_ID) == (0, 0, 0, 0)
        assert await _visible_counts(pool, BARBER_ID) == (1, 0, 2, 1)
        assert await _visible_counts(pool, OWNER_ID) == (1, 2, 2, 1)
        assert await _visible_counts(pool, OTHER_OWNER_ID) == (0, 0, 0, 0)
        assert await _visible_counts(pool, PLATFORM_ID) == (1, 2, 2, 1)

        report_start = datetime(2026, 8, 10, tzinfo=UTC)
        report_end = datetime(2026, 8, 13, tzinfo=UTC)
        report = await get_shop_report(
            pool,
            actor_id=OWNER_ID,
            business_id=BUSINESS_ID,
            shop_id=SHOP_ID,
            period_start=report_start,
            period_end=report_end,
            cursor=None,
            limit=100,
        )
        assert report.totals.bookings_completed == 1
        assert report.totals.sale_count == 1
        assert report.totals.sale_grand == Decimal("130.00")
        assert report.totals.correction_count == 1
        assert report.totals.correction_grand == Decimal("65.00")
        assert report.totals.net_grand == Decimal("65.00")
        assert report.totals.cash_tender == Decimal("130.00")
        assert report.totals.cash_refunds == Decimal("65.00")
        assert report.totals.advance_cash == Decimal("5.00")
        assert report.totals.payout_cash == Decimal("20.00")
        assert report.totals.payout_gross == Decimal("25.00")
        assert report.totals.payout_advance_deduction == Decimal("5.00")
        assert report.totals.payout_net_paid == Decimal("20.00")
        assert report.totals.advance_outstanding == Decimal("0.00")
        assert report.totals.journal_debit == report.totals.journal_credit
        assert report.totals.journal_balanced
        barber_row = next(
            row for row in report.barbers if row.barber_membership_id == barber_membership_id
        )
        assert barber_row.commission_earnings == Decimal("36.00")
        assert barber_row.commission_reversals == Decimal("18.00")
        assert barber_row.tip_earnings == Decimal("10.00")
        assert barber_row.tip_reversals == Decimal("5.00")
        assert barber_row.payout_net_paid == Decimal("20.00")

        first_page = await get_shop_report(
            pool,
            actor_id=MANAGER_ID,
            business_id=BUSINESS_ID,
            shop_id=SHOP_ID,
            period_start=report_start,
            period_end=report_end,
            cursor=None,
            limit=1,
        )
        assert len(first_page.barbers) == 1
        assert first_page.next_cursor is not None
        second_page = await get_shop_report(
            pool,
            actor_id=MANAGER_ID,
            business_id=BUSINESS_ID,
            shop_id=SHOP_ID,
            period_start=report_start,
            period_end=report_end,
            cursor=first_page.next_cursor,
            limit=100,
        )
        assert all(row.barber_membership_id > first_page.next_cursor for row in second_page.barbers)
        assert [*first_page.barbers, *second_page.barbers] == report.barbers

        platform_report = await get_shop_report(
            pool,
            actor_id=PLATFORM_ID,
            business_id=BUSINESS_ID,
            shop_id=SHOP_ID,
            period_start=report_start,
            period_end=report_end,
            cursor=None,
            limit=100,
        )
        assert platform_report.totals == report.totals
        with pytest.raises(ReportAccessDeniedError):
            await get_shop_report(
                pool,
                actor_id=MANAGER_ID,
                business_id=BUSINESS_ID,
                shop_id=REPORT_SHOP_ID,
                period_start=report_start,
                period_end=report_end,
                cursor=None,
                limit=50,
            )

        overview = await get_business_overview(
            pool,
            actor_id=OWNER_ID,
            business_id=BUSINESS_ID,
            period_start=report_start,
            period_end=report_end,
            cursor=None,
            limit=100,
        )
        assert len(overview.shops) == 2
        assert overview.totals.net_grand == Decimal("65.00")
        assert next(row for row in overview.shops if row.shop_id == SHOP_ID).net_grand == Decimal(
            "65.00"
        )
        overview_page = await get_business_overview(
            pool,
            actor_id=PLATFORM_ID,
            business_id=BUSINESS_ID,
            period_start=report_start,
            period_end=report_end,
            cursor=None,
            limit=1,
        )
        assert len(overview_page.shops) == 1
        assert overview_page.next_cursor is not None
        overview_tail = await get_business_overview(
            pool,
            actor_id=PLATFORM_ID,
            business_id=BUSINESS_ID,
            period_start=report_start,
            period_end=report_end,
            cursor=overview_page.next_cursor,
            limit=100,
        )
        assert [*overview_page.shops, *overview_tail.shops] == overview.shops

        for denied_actor in (RECEPTIONIST_ID, BARBER_ID, OTHER_OWNER_ID):
            with pytest.raises(ReportAccessDeniedError):
                await get_shop_report(
                    pool,
                    actor_id=denied_actor,
                    business_id=BUSINESS_ID,
                    shop_id=SHOP_ID,
                    period_start=report_start,
                    period_end=report_end,
                    cursor=None,
                    limit=50,
                )
        with pytest.raises(ReportAccessDeniedError):
            await get_business_overview(
                pool,
                actor_id=MANAGER_ID,
                business_id=BUSINESS_ID,
                period_start=report_start,
                period_end=report_end,
                cursor=None,
                limit=50,
            )
        with pytest.raises(ReportAccessDeniedError):
            await get_business_overview(
                pool,
                actor_id=OTHER_OWNER_ID,
                business_id=BUSINESS_ID,
                period_start=report_start,
                period_end=report_end,
                cursor=None,
                limit=50,
            )

        async with pool.connection(timeout=5) as connection, connection.transaction():
            await connection.execute("set local role authenticated")
            await connection.execute(
                "select set_config('request.jwt.claim.sub', %s, true)",
                (str(OWNER_ID),),
            )
            with pytest.raises(InsufficientPrivilege):
                await connection.execute(
                    "delete from public.payout_runs where id = %s",
                    (paid.payout_run_id,),
                )
        async with pool.connection(timeout=5) as connection:
            with pytest.raises(RaiseException, match="only allow application settlement"):
                async with connection.transaction():
                    await connection.execute(
                        """
                        update public.advances
                        set outstanding_amount = original_amount
                        where id = %s
                        """,
                        (advance.advance_id,),
                    )
    finally:
        await pool.close()


def test_payout_database_contracts() -> None:
    asyncio.run(_exercise_payout_contracts())
