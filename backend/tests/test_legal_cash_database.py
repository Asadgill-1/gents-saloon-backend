import asyncio
import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.core.database import create_database_pool
from app.services.legal_cash_service import (
    CashAccessDeniedError,
    CashMovementRecord,
    CashShiftCloseRequest,
    CashShiftConflictError,
    CashShiftOpenRequest,
    allocate_document_number,
    close_cash_shift,
    get_cash_shift,
    open_cash_shift,
    record_cash_movement,
    select_legal_document_profile,
)

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_DATABASE_INTEGRATION") != "1",
    reason="requires reconstructed Phase 2 PostgreSQL test database",
)

RECEPTIONIST_ID = UUID("00000000-0000-0000-0000-000000000003")
BARBER_ID = UUID("00000000-0000-0000-0000-000000000005")
BUSINESS_ID = UUID("10000000-0000-0000-0000-000000000001")
SHOP_ID = UUID("20000000-0000-0000-0000-000000000001")
SHOP_TWO_ID = UUID("20000000-0000-0000-0000-000000000002")
BASE_AT = datetime(2026, 7, 27, 6, 0, tzinfo=UTC)


async def _allocate(pool: object, index: int) -> int:
    async with pool.connection(timeout=5) as connection, connection.transaction():  # type: ignore[attr-defined]
        number = await allocate_document_number(
            connection,
            business_id=BUSINESS_ID,
            shop_id=SHOP_ID,
            counter_kind="sale",
            at=BASE_AT + timedelta(seconds=index),
        )
        assert number.document_number.startswith("A1-2026-")
        return number.sequence_number


async def _exercise_legal_cash_contracts() -> None:
    pool = create_database_pool(Settings(_env_file=None))
    await pool.open(wait=True)
    try:
        async with pool.connection(timeout=5) as connection:
            vat_profile = await select_legal_document_profile(
                connection,
                business_id=BUSINESS_ID,
                shop_id=SHOP_ID,
                at=BASE_AT,
            )
            non_vat_profile = await select_legal_document_profile(
                connection,
                business_id=BUSINESS_ID,
                shop_id=SHOP_TWO_ID,
                at=BASE_AT,
            )
        assert vat_profile.vat_registered is True
        assert vat_profile.trn == "100000000000001"
        assert vat_profile.document_type == "tax_invoice"
        assert non_vat_profile.vat_registered is False
        assert non_vat_profile.trn is None
        assert non_vat_profile.document_type == "receipt"

        sequences = await asyncio.gather(*[_allocate(pool, index) for index in range(4)])
        assert sorted(sequences) == [1, 2, 3, 4]

        contested = await asyncio.gather(
            open_cash_shift(
                pool,
                actor_id=RECEPTIONIST_ID,
                business_id=BUSINESS_ID,
                shop_id=SHOP_ID,
                idempotency_key="cash-open-contested-0001a",
                request_id="cash-open-a",
                payload=CashShiftOpenRequest(
                    register_label="Front Desk",
                    opening_float=Decimal("200.00"),
                ),
                at=BASE_AT,
            ),
            open_cash_shift(
                pool,
                actor_id=RECEPTIONIST_ID,
                business_id=BUSINESS_ID,
                shop_id=SHOP_ID,
                idempotency_key="cash-open-contested-0001b",
                request_id="cash-open-b",
                payload=CashShiftOpenRequest(
                    register_label="front desk",
                    opening_float=Decimal("200.00"),
                ),
                at=BASE_AT,
            ),
            return_exceptions=True,
        )
        winners = [result for result in contested if not isinstance(result, Exception)]
        conflicts = [result for result in contested if isinstance(result, CashShiftConflictError)]
        assert len(winners) == 1
        assert len(conflicts) == 1
        shift = winners[0]

        replay_a, replay_b = await asyncio.gather(
            *[
                open_cash_shift(
                    pool,
                    actor_id=RECEPTIONIST_ID,
                    business_id=BUSINESS_ID,
                    shop_id=SHOP_ID,
                    idempotency_key="cash-open-replay-0002",
                    request_id=f"cash-open-replay-{index}",
                    payload=CashShiftOpenRequest(
                        register_label="Back Desk",
                        opening_float=Decimal("50.00"),
                    ),
                    at=BASE_AT,
                )
                for index in range(2)
            ]
        )
        assert replay_a == replay_b

        movements = (
            ("cash_sale", "100.00", uuid4(), None),
            ("pay_in", "50.00", None, "Additional float"),
            ("pay_out", "20.00", None, "Supplies"),
            ("advance", "10.00", uuid4(), None),
            ("payout", "5.00", uuid4(), None),
            ("refund", "15.00", uuid4(), None),
        )
        for index, (movement_type, amount, source_id, reason) in enumerate(movements):
            await record_cash_movement(
                pool,
                actor_id=RECEPTIONIST_ID,
                business_id=BUSINESS_ID,
                shop_id=SHOP_ID,
                cash_shift_id=shift.cash_shift_id,
                idempotency_key=f"cash-movement-{index:04d}",
                request_id=f"cash-movement-{index}",
                payload=CashMovementRecord(
                    movement_type=movement_type,
                    amount=Decimal(amount),
                    source_entity_id=source_id,
                    reason=reason,
                ),
                at=BASE_AT + timedelta(minutes=index + 1),
            )

        with pytest.raises(ValidationError):
            CashMovementRecord(
                movement_type="card",  # type: ignore[arg-type]
                amount=Decimal("100.00"),
                source_entity_id=uuid4(),
            )

        preview = await get_cash_shift(
            pool,
            business_id=BUSINESS_ID,
            shop_id=SHOP_ID,
            cash_shift_id=shift.cash_shift_id,
        )
        assert preview.expected_cash == Decimal("300.00")
        assert preview.cash_sales == Decimal("100.00")
        assert preview.counted_cash is None

        close_payload = CashShiftCloseRequest(counted_cash=Decimal("295.00"))
        closed = await close_cash_shift(
            pool,
            actor_id=RECEPTIONIST_ID,
            business_id=BUSINESS_ID,
            shop_id=SHOP_ID,
            cash_shift_id=shift.cash_shift_id,
            idempotency_key="cash-close-replay-0003",
            request_id="cash-close-a",
            payload=close_payload,
            at=BASE_AT + timedelta(hours=8),
        )
        replayed_close = await close_cash_shift(
            pool,
            actor_id=RECEPTIONIST_ID,
            business_id=BUSINESS_ID,
            shop_id=SHOP_ID,
            cash_shift_id=shift.cash_shift_id,
            idempotency_key="cash-close-replay-0003",
            request_id="cash-close-b",
            payload=close_payload,
            at=BASE_AT + timedelta(hours=8),
        )
        assert closed == replayed_close
        assert closed.expected_cash == Decimal("300.00")
        assert closed.counted_cash == Decimal("295.00")
        assert closed.variance == Decimal("-5.00")

        with pytest.raises(CashShiftConflictError):
            await record_cash_movement(
                pool,
                actor_id=RECEPTIONIST_ID,
                business_id=BUSINESS_ID,
                shop_id=SHOP_ID,
                cash_shift_id=shift.cash_shift_id,
                idempotency_key="cash-after-close-0004",  # gitleaks:allow
                request_id="cash-after-close",
                payload=CashMovementRecord(
                    movement_type="pay_in",
                    amount=Decimal("1.00"),
                    reason="Too late",
                ),
                at=BASE_AT + timedelta(hours=9),
            )

        reopened = await open_cash_shift(
            pool,
            actor_id=RECEPTIONIST_ID,
            business_id=BUSINESS_ID,
            shop_id=SHOP_ID,
            idempotency_key="cash-reopen-0005",
            request_id="cash-reopen",
            payload=CashShiftOpenRequest(
                register_label=shift.register_label,
                opening_float=Decimal("0.00"),
            ),
            at=BASE_AT + timedelta(days=1),
        )
        assert reopened.status == "open"

        with pytest.raises(CashAccessDeniedError):
            await open_cash_shift(
                pool,
                actor_id=BARBER_ID,
                business_id=BUSINESS_ID,
                shop_id=SHOP_ID,
                idempotency_key="cash-barber-denied-0006",  # gitleaks:allow
                request_id="cash-barber-denied",
                payload=CashShiftOpenRequest(
                    register_label="Barber Register",
                    opening_float=Decimal("0.00"),
                ),
                at=BASE_AT,
            )

        async with pool.connection(timeout=5) as connection:
            cursor = await connection.execute(
                """
                select
                  (select count(*) from public.audit_log
                   where action like 'cash_shift.%%') as audits,
                  (select count(*) from public.outbox_events
                   where topic like 'cash_shift.%%') as outbox,
                  (select count(*) from public.cash_shift_movements
                   where cash_shift_id = %s) as movements
                """,
                (shift.cash_shift_id,),
            )
            evidence = await cursor.fetchone()
            assert evidence is not None
            assert evidence[0] == evidence[1]
            assert evidence[2] == 6
    finally:
        await pool.close()


def test_legal_cash_database_contracts() -> None:
    asyncio.run(_exercise_legal_cash_contracts())
