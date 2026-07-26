from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal, cast
from uuid import UUID

from psycopg.errors import (
    CheckViolation,
    ForeignKeyViolation,
    RaiseException,
    UniqueViolation,
)
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.entitlements import require_active_entitlement
from app.services.checkout_calculations import (
    MoneyCalculationError,
    proportional_cumulative,
)
from app.services.checkout_service import CheckoutPayment, CheckoutPaymentResponse
from app.services.legal_cash_service import (
    CashAccessDeniedError,
    _require_operator,
    _write_event,
    allocate_document_number,
)
from app.services.platform_operations import (
    complete_idempotency,
    reserve_idempotency,
)

Money = Annotated[
    Decimal,
    Field(ge=Decimal("0"), max_digits=14, decimal_places=2),
]
PositiveMoney = Annotated[
    Decimal,
    Field(gt=Decimal("0"), max_digits=14, decimal_places=2),
]


class CorrectionAccessDeniedError(Exception):
    """The actor cannot correct transactions for this shop."""


class CorrectionNotFoundError(Exception):
    """The original transaction does not exist in the tenant scope."""


class CorrectionConflictError(Exception):
    """The correction conflicts with immutable financial state."""


class CorrectionInputError(Exception):
    """The correction request does not reconcile with the original sale."""


class CorrectionItemRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    transaction_item_id: UUID
    amount: PositiveMoney


class CorrectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["void", "refund"]
    items: list[CorrectionItemRequest] = Field(default_factory=list, max_length=10)
    payments: list[CheckoutPayment] = Field(default_factory=list, max_length=2)
    tip_refund: Money = Decimal("0.00")
    cash_shift_id: UUID | None = None
    reason: str = Field(min_length=3, max_length=500)

    @field_validator("items")
    @classmethod
    def unique_items(cls, value: list[CorrectionItemRequest]) -> list[CorrectionItemRequest]:
        ids = [item.transaction_item_id for item in value]
        if len(ids) != len(set(ids)):
            raise ValueError("transaction_item_id values must be unique")
        return value

    @field_validator("payments")
    @classmethod
    def unique_payment_methods(cls, value: list[CheckoutPayment]) -> list[CheckoutPayment]:
        methods = [payment.method for payment in value]
        if len(methods) != len(set(methods)):
            raise ValueError("payment methods must be unique")
        return value

    @field_validator("reason")
    @classmethod
    def trimmed_reason(cls, value: str) -> str:
        if value != value.strip():
            raise ValueError("reason cannot have surrounding whitespace")
        return value

    @model_validator(mode="after")
    def validate_kind(self) -> "CorrectionRequest":
        if self.kind == "void":
            if self.items or self.payments or self.tip_refund != 0:
                raise ValueError("void derives the full correction from the original sale")
            if self.cash_shift_id is None:
                raise ValueError("void requires the original open cash shift")
            return self
        if not self.items and self.tip_refund == 0:
            raise ValueError("refund requires at least one item or tip amount")
        if not self.payments:
            raise ValueError("refund requires return payments")
        has_cash = any(payment.method == "cash" for payment in self.payments)
        if has_cash != (self.cash_shift_id is not None):
            raise ValueError("cash refund requires exactly one open cash shift")
        return self


class CorrectionItemResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    transaction_item_id: UUID
    service_name: str
    refund_net: Decimal
    refund_vat: Decimal
    refund_gross: Decimal


class CorrectionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    correction_id: UUID
    original_transaction_id: UUID
    kind: Literal["void", "refund"]
    credit_note_number: str
    currency: Literal["AED"] = "AED"
    service_gross_refund: Decimal
    net_refund: Decimal
    vat_refund: Decimal
    tip_refund: Decimal
    grand_total: Decimal
    items: list[CorrectionItemResponse]
    payments: list[CheckoutPaymentResponse]
    created_at: datetime


async def _original_transaction(
    connection: Any,
    *,
    transaction_id: UUID,
    business_id: UUID,
    shop_id: UUID,
) -> tuple[Any, ...]:
    cursor = await connection.execute(
        """
        select
          barber_membership_id, cash_shift_id, service_gross_total,
          net_total, vat_total, tip_total, grand_total, legal_snapshot
        from public.transactions
        where id = %s and business_id = %s and shop_id = %s
        for update
        """,
        (transaction_id, business_id, shop_id),
    )
    row = await cursor.fetchone()
    if row is None:
        raise CorrectionNotFoundError
    return cast(tuple[Any, ...], row)


async def _original_items(
    connection: Any,
    *,
    transaction_id: UUID,
    business_id: UUID,
    shop_id: UUID,
) -> list[tuple[Any, ...]]:
    cursor = await connection.execute(
        """
        select
          ti.id, ti.service_name, ti.line_net, ti.line_vat, ti.line_gross,
          tic.id, tic.barber_commission, tic.shop_share
        from public.transaction_items ti
        join public.transaction_item_commissions tic
          on tic.transaction_item_id = ti.id
        where ti.transaction_id = %s
          and ti.business_id = %s
          and ti.shop_id = %s
        order by ti.id
        for share of ti, tic
        """,
        (transaction_id, business_id, shop_id),
    )
    return cast(list[tuple[Any, ...]], await cursor.fetchall())


async def _prior_items(
    connection: Any,
    *,
    transaction_id: UUID,
) -> dict[UUID, tuple[Decimal, Decimal, Decimal]]:
    cursor = await connection.execute(
        """
        select
          ci.original_transaction_item_id,
          sum(ci.refund_gross),
          sum(ci.refund_net),
          sum(cc.barber_commission_refund)
        from public.transaction_correction_items ci
        join public.transaction_correction_item_commissions cc
          on cc.correction_item_id = ci.id
        where ci.original_transaction_id = %s
        group by ci.original_transaction_item_id
        """,
        (transaction_id,),
    )
    return {
        UUID(str(row[0])): (Decimal(row[1]), Decimal(row[2]), Decimal(row[3]))
        for row in await cursor.fetchall()
    }


async def _payment_state(
    connection: Any,
    *,
    transaction_id: UUID,
) -> tuple[list[CheckoutPayment], dict[str, Decimal]]:
    cursor = await connection.execute(
        """
        select
          tp.method::text,
          tp.amount,
          tp.card_slip_reference,
          coalesce((
            select sum(cp.amount)
            from public.transaction_correction_payments cp
            where cp.original_transaction_id = tp.transaction_id
              and cp.method = tp.method
          ), 0)
        from public.transaction_payments tp
        where tp.transaction_id = %s
        order by tp.method
        for share
        """,
        (transaction_id,),
    )
    originals: list[CheckoutPayment] = []
    prior: dict[str, Decimal] = {}
    for row in await cursor.fetchall():
        method = cast(Literal["cash", "card"], str(row[0]))
        originals.append(
            CheckoutPayment(
                method=method,
                amount=Decimal(row[1]),
                card_slip_reference=str(row[2]) if row[2] is not None else None,
            )
        )
        prior[method] = Decimal(row[3])
    return originals, prior


async def correct_transaction(
    pool: Any,
    *,
    actor_id: UUID,
    business_id: UUID,
    shop_id: UUID,
    transaction_id: UUID,
    idempotency_key: str,
    request_id: str,
    payload: CorrectionRequest,
    at: datetime | None = None,
) -> CorrectionResponse:
    created_at = at or datetime.now(UTC)
    scope = f"pos.correction:{business_id}:{shop_id}:{transaction_id}"
    try:
        async with pool.connection(timeout=5) as connection, connection.transaction():
            try:
                await _require_operator(
                    connection,
                    actor_id=actor_id,
                    business_id=business_id,
                    shop_id=shop_id,
                )
            except CashAccessDeniedError as exc:
                raise CorrectionAccessDeniedError from exc
            await require_active_entitlement(
                connection,
                business_id=business_id,
                shop_id=shop_id,
                at=created_at,
            )
            replay = await reserve_idempotency(
                connection,
                scope=scope,
                actor_id=actor_id,
                key=idempotency_key,
                payload=payload,
                expected_status=201,
            )
            if replay is not None:
                return CorrectionResponse.model_validate(replay)

            original = await _original_transaction(
                connection,
                transaction_id=transaction_id,
                business_id=business_id,
                shop_id=shop_id,
            )
            barber_id = UUID(str(original[0]))
            original_shift_id = UUID(str(original[1])) if original[1] is not None else None
            original_items = await _original_items(
                connection,
                transaction_id=transaction_id,
                business_id=business_id,
                shop_id=shop_id,
            )
            if not original_items:
                raise CorrectionConflictError("original transaction has no items")
            prior_items = await _prior_items(
                connection,
                transaction_id=transaction_id,
            )
            original_payments, prior_payments = await _payment_state(
                connection,
                transaction_id=transaction_id,
            )

            prior_cursor = await connection.execute(
                """
                select count(*), coalesce(sum(tip_refund), 0)
                from public.transaction_corrections
                where original_transaction_id = %s
                """,
                (transaction_id,),
            )
            prior_row = await prior_cursor.fetchone()
            assert prior_row is not None
            prior_count, prior_tip = int(prior_row[0]), Decimal(prior_row[1])

            requested: dict[UUID, Decimal]
            correction_payments: list[CheckoutPayment]
            if payload.kind == "void":
                if prior_count != 0:
                    raise CorrectionConflictError("a corrected sale cannot be voided")
                if original_shift_id is None or payload.cash_shift_id != original_shift_id:
                    raise CorrectionConflictError("void requires the original cash shift")
                if any(payment.method != "cash" for payment in original_payments):
                    raise CorrectionConflictError("void requires cash-only original tender")
                requested = {UUID(str(item[0])): Decimal(item[4]) for item in original_items}
                correction_payments = original_payments
                tip_refund = Decimal(original[5])
            else:
                requested = {item.transaction_item_id: item.amount for item in payload.items}
                known_ids = {UUID(str(item[0])) for item in original_items}
                if not requested.keys() <= known_ids:
                    raise CorrectionInputError("refund references an unknown item")
                correction_payments = payload.payments
                tip_refund = payload.tip_refund

            if prior_tip + tip_refund > Decimal(original[5]):
                raise CorrectionConflictError("tip refund exceeds the original tip")

            calculated: list[
                tuple[tuple[Any, ...], Decimal, Decimal, Decimal, Decimal, Decimal]
            ] = []
            for item in original_items:
                item_id = UUID(str(item[0]))
                if item_id not in requested:
                    continue
                refund_gross = requested[item_id]
                original_net = Decimal(item[2])
                original_gross = Decimal(item[4])
                prior_gross, prior_net, prior_barber = prior_items.get(
                    item_id,
                    (Decimal("0.00"), Decimal("0.00"), Decimal("0.00")),
                )
                cumulative_gross = prior_gross + refund_gross
                cumulative_net = proportional_cumulative(
                    original_output=original_net,
                    original_input=original_gross,
                    cumulative_input=cumulative_gross,
                )
                refund_net = cumulative_net - prior_net
                refund_vat = refund_gross - refund_net
                cumulative_barber = proportional_cumulative(
                    original_output=Decimal(item[6]),
                    original_input=original_net,
                    cumulative_input=cumulative_net,
                )
                barber_refund = cumulative_barber - prior_barber
                shop_refund = refund_net - barber_refund
                calculated.append(
                    (
                        item,
                        refund_gross,
                        refund_net,
                        refund_vat,
                        barber_refund,
                        shop_refund,
                    )
                )

            service_gross_refund = sum(
                (item[1] for item in calculated),
                Decimal("0.00"),
            )
            net_refund = sum((item[2] for item in calculated), Decimal("0.00"))
            vat_refund = sum((item[3] for item in calculated), Decimal("0.00"))
            grand_total = service_gross_refund + tip_refund
            if grand_total <= 0:
                raise CorrectionInputError("correction total must be positive")
            if (
                sum((payment.amount for payment in correction_payments), Decimal("0.00"))
                != grand_total
            ):
                raise CorrectionInputError("return payments do not equal correction total")
            for payment in correction_payments:
                original_payment = next(
                    (source for source in original_payments if source.method == payment.method),
                    None,
                )
                if (
                    original_payment is None
                    or prior_payments.get(payment.method, Decimal("0.00")) + payment.amount
                    > original_payment.amount
                ):
                    raise CorrectionConflictError("return payment exceeds original tender")

            cash_amount = sum(
                (payment.amount for payment in correction_payments if payment.method == "cash"),
                Decimal("0.00"),
            )
            if cash_amount > 0:
                shift_cursor = await connection.execute(
                    """
                    select 1
                    from public.cash_shifts
                    where id = %s
                      and business_id = %s
                      and shop_id = %s
                      and status = 'open'
                    for update
                    """,
                    (payload.cash_shift_id, business_id, shop_id),
                )
                if await shift_cursor.fetchone() is None:
                    raise CorrectionConflictError("cash shift is not open")

            number = await allocate_document_number(
                connection,
                business_id=business_id,
                shop_id=shop_id,
                counter_kind="credit_note",
                at=created_at,
            )
            correction_cursor = await connection.execute(
                """
                insert into public.transaction_corrections (
                  business_id, shop_id, original_transaction_id,
                  barber_membership_id, cash_shift_id, kind,
                  credit_note_number, service_gross_refund, net_refund,
                  vat_refund, tip_refund, grand_total, reason,
                  legal_snapshot, created_by_auth_user_id, created_at
                )
                values (
                  %s, %s, %s, %s, %s, %s, %s, %s,
                  %s, %s, %s, %s, %s, %s, %s, %s
                )
                returning id, created_at
                """,
                (
                    business_id,
                    shop_id,
                    transaction_id,
                    barber_id,
                    payload.cash_shift_id,
                    payload.kind,
                    number.document_number,
                    service_gross_refund,
                    net_refund,
                    vat_refund,
                    tip_refund,
                    grand_total,
                    payload.reason,
                    Jsonb(original[7]),
                    actor_id,
                    created_at,
                ),
            )
            correction_row = await correction_cursor.fetchone()
            assert correction_row is not None
            correction_id = UUID(str(correction_row[0]))
            response_items: list[CorrectionItemResponse] = []
            barber_refund_total = Decimal("0.00")
            shop_refund_total = Decimal("0.00")

            for item, gross, net, vat, barber_refund, shop_refund in calculated:
                item_cursor = await connection.execute(
                    """
                    insert into public.transaction_correction_items (
                      business_id, shop_id, correction_id,
                      original_transaction_id, original_transaction_item_id,
                      barber_membership_id, service_name, refund_net,
                      refund_vat, refund_gross, created_at
                    )
                    values (
                      %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                    )
                    returning id
                    """,
                    (
                        business_id,
                        shop_id,
                        correction_id,
                        transaction_id,
                        item[0],
                        barber_id,
                        item[1],
                        net,
                        vat,
                        gross,
                        created_at,
                    ),
                )
                correction_item_row = await item_cursor.fetchone()
                assert correction_item_row is not None
                await connection.execute(
                    """
                    insert into public.transaction_correction_item_commissions (
                      business_id, shop_id, correction_id,
                      original_transaction_id, correction_item_id,
                      original_commission_id, barber_membership_id,
                      commission_base_refund, barber_commission_refund,
                      shop_share_refund, created_at
                    )
                    values (
                      %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                    )
                    """,
                    (
                        business_id,
                        shop_id,
                        correction_id,
                        transaction_id,
                        correction_item_row[0],
                        item[5],
                        barber_id,
                        net,
                        barber_refund,
                        shop_refund,
                        created_at,
                    ),
                )
                barber_refund_total += barber_refund
                shop_refund_total += shop_refund
                response_items.append(
                    CorrectionItemResponse(
                        transaction_item_id=UUID(str(item[0])),
                        service_name=str(item[1]),
                        refund_net=net,
                        refund_vat=vat,
                        refund_gross=gross,
                    )
                )

            for payment in correction_payments:
                await connection.execute(
                    """
                    insert into public.transaction_correction_payments (
                      business_id, shop_id, correction_id,
                      original_transaction_id, method, amount,
                      card_slip_reference, created_at
                    )
                    values (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        business_id,
                        shop_id,
                        correction_id,
                        transaction_id,
                        payment.method,
                        payment.amount,
                        payment.card_slip_reference,
                        created_at,
                    ),
                )

            original_journal_cursor = await connection.execute(
                """
                select id
                from public.journal_entries
                where source_type = 'checkout' and source_entity_id = %s
                for share
                """,
                (transaction_id,),
            )
            original_journal = await original_journal_cursor.fetchone()
            if original_journal is None:
                raise CorrectionConflictError("original journal entry is missing")
            journal_cursor = await connection.execute(
                """
                insert into public.journal_entries (
                  business_id, shop_id, source_type, source_entity_id,
                  idempotency_key, reversal_of_entry_id,
                  actor_auth_user_id, created_at
                )
                values (%s, %s, 'correction', %s, %s, %s, %s, %s)
                returning id
                """,
                (
                    business_id,
                    shop_id,
                    correction_id,
                    idempotency_key,
                    original_journal[0],
                    actor_id,
                    created_at,
                ),
            )
            journal_row = await journal_cursor.fetchone()
            assert journal_row is not None
            postings: list[tuple[str, UUID | None, Decimal, Decimal]] = [
                (
                    "cash" if payment.method == "cash" else "card_clearing",
                    None,
                    Decimal("0.00"),
                    payment.amount,
                )
                for payment in correction_payments
            ]
            postings.extend(
                [
                    (
                        "service_revenue",
                        None,
                        shop_refund_total,
                        Decimal("0.00"),
                    ),
                    (
                        "barber_payable",
                        barber_id,
                        barber_refund_total,
                        Decimal("0.00"),
                    ),
                    (
                        "vat_payable",
                        None,
                        vat_refund,
                        Decimal("0.00"),
                    ),
                    (
                        "tip_payable",
                        barber_id,
                        tip_refund,
                        Decimal("0.00"),
                    ),
                ]
            )
            for account, posting_barber, debit, credit in postings:
                if debit == 0 and credit == 0:
                    continue
                await connection.execute(
                    """
                    insert into public.journal_postings (
                      business_id, shop_id, journal_entry_id, account_code,
                      barber_membership_id, debit, credit, created_at
                    )
                    values (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        business_id,
                        shop_id,
                        journal_row[0],
                        account,
                        posting_barber,
                        debit,
                        credit,
                        created_at,
                    ),
                )

            if cash_amount > 0:
                await connection.execute(
                    """
                    insert into public.cash_shift_movements (
                      business_id, shop_id, cash_shift_id, movement_type,
                      amount, reason, source_entity_id,
                      created_by_auth_user_id, created_at
                    )
                    values (%s, %s, %s, 'refund', %s, %s, %s, %s, %s)
                    """,
                    (
                        business_id,
                        shop_id,
                        payload.cash_shift_id,
                        cash_amount,
                        payload.reason,
                        correction_id,
                        actor_id,
                        created_at,
                    ),
                )

            response = CorrectionResponse(
                correction_id=correction_id,
                original_transaction_id=transaction_id,
                kind=payload.kind,
                credit_note_number=number.document_number,
                service_gross_refund=service_gross_refund,
                net_refund=net_refund,
                vat_refund=vat_refund,
                tip_refund=tip_refund,
                grand_total=grand_total,
                items=response_items,
                payments=[
                    CheckoutPaymentResponse(
                        method=payment.method,
                        amount=payment.amount,
                        card_slip_reference=payment.card_slip_reference,
                    )
                    for payment in correction_payments
                ],
                created_at=correction_row[1],
            )
            await _write_event(
                connection,
                business_id=business_id,
                shop_id=shop_id,
                actor_id=actor_id,
                action="pos.transaction_corrected",
                entity_type="transaction_correction",
                entity_id=correction_id,
                request_id=request_id,
                details={
                    "correction_id": str(correction_id),
                    "original_transaction_id": str(transaction_id),
                    "kind": payload.kind,
                    "credit_note_number": number.document_number,
                    "grand_total": str(grand_total),
                },
            )
            await complete_idempotency(
                connection,
                scope=scope,
                actor_id=actor_id,
                key=idempotency_key,
                response_status=201,
                response=response,
            )
            return response
    except (
        CorrectionInputError,
        CorrectionNotFoundError,
        CorrectionConflictError,
    ):
        raise
    except MoneyCalculationError as exc:
        raise CorrectionConflictError(str(exc)) from exc
    except UniqueViolation as exc:
        raise CorrectionConflictError("correction already exists") from exc
    except (CheckViolation, ForeignKeyViolation, RaiseException) as exc:
        raise CorrectionConflictError("correction violates durable constraints") from exc


__all__ = [
    "CorrectionAccessDeniedError",
    "CorrectionConflictError",
    "CorrectionInputError",
    "CorrectionNotFoundError",
    "CorrectionRequest",
    "CorrectionResponse",
    "correct_transaction",
]
