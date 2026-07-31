import asyncio
import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest
from psycopg.errors import RaiseException

from app.core.config import Settings
from app.core.database import create_database_pool
from app.core.entitlements import DUBAI, resolve_entitlement
from app.services.subscription_service import (
    BillingModeTransitionRequest,
    CashReceiptRequest,
    IdempotencyConflictError,
    PaidCoverageRequiredError,
    ReceiptReversalRequest,
    ResumeSubscriptionRequest,
    SuspendSubscriptionRequest,
    expire_due_subscriptions,
    record_cash_receipt,
    resume_subscription,
    reverse_cash_receipt,
    suspend_subscription,
    transition_billing_mode,
)

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_DATABASE_INTEGRATION") != "1",
    reason="requires reconstructed Phase 1 PostgreSQL test database",
)

ADMIN_ID = UUID("00000000-0000-0000-0000-000000000001")
BUSINESS_A_ID = UUID("10000000-0000-0000-0000-000000000001")
BUSINESS_B_ID = UUID("10000000-0000-0000-0000-000000000002")
SHOP_A1_ID = UUID("20000000-0000-0000-0000-000000000001")
SHOP_B1_ID = UUID("20000000-0000-0000-0000-000000000003")
SUBSCRIPTION_A_ID = UUID("30000000-0000-0000-0000-000000000001")
SUBSCRIPTION_B_ID = UUID("30000000-0000-0000-0000-000000000002")


async def _fetchone(pool: object, query: str, params: tuple[object, ...]) -> tuple[object, ...]:
    async with pool.connection(timeout=5) as connection:  # type: ignore[attr-defined]
        cursor = await connection.execute(query, params)
        row = await cursor.fetchone()
        assert row is not None
        return row


async def test_receipt_suspend_resume_reversal_and_mode_transition() -> None:
    pool = create_database_pool(Settings(_env_file=None))
    await pool.open()
    try:
        paid_from, paid_until = await _fetchone(
            pool,
            "select paid_from, paid_until from public.subscriptions where id = %s",
            (SUBSCRIPTION_A_ID,),
        )
        receipt_payload = CashReceiptRequest(
            subscription_id=SUBSCRIPTION_A_ID,
            amount=Decimal("500.00"),
            receipt_reference="PHASE1-CASH-A-001",
            collected_at=datetime(2026, 7, 25, 8, tzinfo=UTC),
            coverage_from=paid_from,
            coverage_until=paid_until,
            evidence_note="cash ledger page 1",
        )
        receipt, replay = await asyncio.gather(
            record_cash_receipt(
                pool,
                actor_id=ADMIN_ID,
                idempotency_key="cash-receipt-concurrent-0001",
                request_id="cash-a1",
                payload=receipt_payload,
            ),
            record_cash_receipt(
                pool,
                actor_id=ADMIN_ID,
                idempotency_key="cash-receipt-concurrent-0001",
                request_id="cash-a2",
                payload=receipt_payload,
            ),
        )
        assert receipt == replay
        assert receipt.e_invoice_document_id == replay.e_invoice_document_id
        async with pool.connection(timeout=5) as connection, connection.transaction():
            await connection.execute(
                """
                update public.idempotency_keys
                set response_body = response_body - 'e_invoice_document_id'
                where scope = 'platform.subscription.cash_receipt'
                  and key = 'cash-receipt-concurrent-0001'
                """
            )
        legacy_replay = await record_cash_receipt(
            pool,
            actor_id=ADMIN_ID,
            idempotency_key="cash-receipt-concurrent-0001",
            request_id="cash-legacy-replay",
            payload=receipt_payload,
        )
        assert legacy_replay == receipt
        assert (
            await _fetchone(
                pool,
                "select count(*) from public.subscription_cash_receipts where id = %s",
                (receipt.receipt_id,),
            )
        )[0] == 1

        reversal = await reverse_cash_receipt(
            pool,
            actor_id=ADMIN_ID,
            receipt_id=receipt.receipt_id,
            idempotency_key="cash-reversal-idempotent-0002",  # gitleaks:allow
            request_id="reverse-a1",
            payload=ReceiptReversalRequest(
                receipt_reference="PHASE1-CASH-A-001-R",
                collected_at=datetime(2026, 7, 25, 9, tzinfo=UTC),
                evidence_note="cash receipt entered in error",
            ),
        )
        replayed_reversal = await reverse_cash_receipt(
            pool,
            actor_id=ADMIN_ID,
            receipt_id=receipt.receipt_id,
            idempotency_key="cash-reversal-idempotent-0002",  # gitleaks:allow
            request_id="reverse-a2",
            payload=ReceiptReversalRequest(
                receipt_reference="PHASE1-CASH-A-001-R",
                collected_at=datetime(2026, 7, 25, 9, tzinfo=UTC),
                evidence_note="cash receipt entered in error",
            ),
        )
        assert reversal == replayed_reversal
        assert reversal.reversed_receipt_id == receipt.receipt_id
        assert reversal.e_invoice_document_id != receipt.e_invoice_document_id
        documents = await _fetchone(
            pool,
            """
            select
              count(*),
              count(*) filter (where document_type = 'invoice'),
              count(*) filter (where document_type = 'credit_note'),
              count(*) filter (
                where transaction_scope = 'b2b'
                  and status = 'prepared'
                  and source_schema_version = 'platform_billing_source_v1'
                  and source_snapshot -> 'buyer' ->> 'business_id' = %s
              ),
              count(distinct subscription_cash_receipt_id),
              count(*) filter (
                where reversal_of_document_id = %s
              )
            from public.e_invoice_documents
            where id in (%s, %s)
            """,
            (
                str(BUSINESS_A_ID),
                receipt.e_invoice_document_id,
                receipt.e_invoice_document_id,
                reversal.e_invoice_document_id,
            ),
        )
        assert documents == (2, 1, 1, 2, 2, 1)
        events = await _fetchone(
            pool,
            """
            select
              count(*) filter (where topic = 'e_invoice.document_prepared'),
              count(*) filter (where action = 'e_invoice.document_prepared')
            from (
              select topic, null::text as action
              from public.outbox_events
              where payload ->> 'document_id' in (%s, %s)
              union all
              select null::text, action
              from public.audit_log
              where entity_id in (%s, %s)
            ) evidence
            """,
            (
                str(receipt.e_invoice_document_id),
                str(reversal.e_invoice_document_id),
                receipt.e_invoice_document_id,
                reversal.e_invoice_document_id,
            ),
        )
        assert events == (2, 2)

        async with pool.connection(timeout=5) as connection:
            with pytest.raises(RaiseException, match="append-only"):
                await connection.execute(
                    """
                    update public.subscription_cash_receipts
                    set evidence_note = 'mutated'
                    where id = %s
                    """,
                    (receipt.receipt_id,),
                )
            await connection.rollback()

        expiry_at = datetime.combine(
            paid_until + timedelta(days=1),
            datetime.min.time().replace(minute=5),
            DUBAI,
        ).astimezone(UTC)
        await suspend_subscription(
            pool,
            actor_id=ADMIN_ID,
            subscription_id=SUBSCRIPTION_A_ID,
            idempotency_key="suspend-nonpayment-0003",
            request_id="suspend-a",
            payload=SuspendSubscriptionRequest(
                reason="non_payment",
                explanation="cash coverage ended",
            ),
            at=expiry_at,
        )
        with pytest.raises(PaidCoverageRequiredError):
            await resume_subscription(
                pool,
                actor_id=ADMIN_ID,
                subscription_id=SUBSCRIPTION_A_ID,
                idempotency_key="resume-without-payment-0004",
                request_id="resume-denied",
                payload=ResumeSubscriptionRequest(explanation="no current payment"),
                at=expiry_at,
            )

        extended_until = paid_until + timedelta(days=31)
        await record_cash_receipt(
            pool,
            actor_id=ADMIN_ID,
            idempotency_key="cash-receipt-resume-0005",
            request_id="cash-resume",
            payload=CashReceiptRequest(
                subscription_id=SUBSCRIPTION_A_ID,
                amount=Decimal("500.00"),
                receipt_reference="PHASE1-CASH-A-002",
                collected_at=expiry_at,
                coverage_from=paid_until + timedelta(days=1),
                coverage_until=extended_until,
                evidence_note="cash renewal",
            ),
            at=expiry_at,
        )
        resumed = await resume_subscription(
            pool,
            actor_id=ADMIN_ID,
            subscription_id=SUBSCRIPTION_A_ID,
            idempotency_key="resume-after-payment-0006",
            request_id="resume-paid",
            payload=ResumeSubscriptionRequest(explanation="cash renewal confirmed"),
            at=expiry_at,
        )
        assert resumed.status == "active"

        async with pool.connection(timeout=5) as connection:
            entitlement = await resolve_entitlement(
                connection,
                business_id=BUSINESS_A_ID,
                shop_id=SHOP_A1_ID,
                at=expiry_at,
            )
        assert entitlement.active

        transition_payload = BillingModeTransitionRequest(
            target_mode="per_shop",
            reason="owner requested per-shop renewals",
        )
        first, same = await asyncio.gather(
            transition_billing_mode(
                pool,
                actor_id=ADMIN_ID,
                business_id=BUSINESS_A_ID,
                idempotency_key="billing-mode-concurrent-0007",
                request_id="mode-a1",
                payload=transition_payload,
            ),
            transition_billing_mode(
                pool,
                actor_id=ADMIN_ID,
                business_id=BUSINESS_A_ID,
                idempotency_key="billing-mode-concurrent-0007",
                request_id="mode-a2",
                payload=transition_payload,
            ),
        )
        assert first == same
        assert first.billing_mode == "per_shop"
        assert len(first.subscription_ids) == 2

        with pytest.raises(IdempotencyConflictError):
            await transition_billing_mode(
                pool,
                actor_id=ADMIN_ID,
                business_id=BUSINESS_A_ID,
                idempotency_key="billing-mode-concurrent-0007",
                request_id="mode-conflict",
                payload=BillingModeTransitionRequest(
                    target_mode="per_shop",
                    reason="changed request",
                ),
            )

        back = await transition_billing_mode(
            pool,
            actor_id=ADMIN_ID,
            business_id=BUSINESS_A_ID,
            idempotency_key="billing-mode-back-0008",
            request_id="mode-back",
            payload=BillingModeTransitionRequest(
                target_mode="business",
                reason="restore business-wide collection",
            ),
        )
        assert back.billing_mode == "business"
        assert len(back.subscription_ids) == 1
    finally:
        await pool.close()


async def test_expiry_worker_and_entitlement_boundary_are_idempotent() -> None:
    pool = create_database_pool(Settings(_env_file=None))
    await pool.open()
    try:
        paid_until = (
            await _fetchone(
                pool,
                "select paid_until from public.subscriptions where id = %s",
                (SUBSCRIPTION_B_ID,),
            )
        )[0]
        expiry_at = datetime.combine(
            paid_until + timedelta(days=1),
            datetime.min.time().replace(minute=5),
            DUBAI,
        ).astimezone(UTC)
        async with pool.connection(timeout=5) as connection:
            before = await resolve_entitlement(
                connection,
                business_id=BUSINESS_B_ID,
                shop_id=SHOP_B1_ID,
                at=expiry_at - timedelta(microseconds=1),
            )
            after = await resolve_entitlement(
                connection,
                business_id=BUSINESS_B_ID,
                shop_id=SHOP_B1_ID,
                at=expiry_at,
            )
        assert before.active
        assert after.status == "expired"

        async with pool.connection(timeout=5) as connection, connection.transaction():
            await connection.execute(
                """
                update public.subscriptions
                set paid_until = %s
                where id <> %s and status = 'active'
                """,
                (paid_until + timedelta(days=365), SUBSCRIPTION_B_ID),
            )

        results = await asyncio.gather(
            expire_due_subscriptions(pool, at=expiry_at),
            expire_due_subscriptions(pool, at=expiry_at),
        )
        assert sum(results) == 1
        assert await expire_due_subscriptions(pool, at=expiry_at) == 0
        assert (
            await _fetchone(
                pool,
                "select status::text from public.subscriptions where id = %s",
                (SUBSCRIPTION_B_ID,),
            )
        )[0] == "expired"
    finally:
        await pool.close()
