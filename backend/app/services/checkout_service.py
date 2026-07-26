import re
from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal, cast
from uuid import UUID

from psycopg.errors import CheckViolation, ForeignKeyViolation, UniqueViolation
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.entitlements import require_active_entitlement
from app.services.checkout_calculations import (
    CommissionCalculation,
    LineCalculation,
    MoneyCalculationError,
    calculate_commission,
    calculate_line,
)
from app.services.legal_cash_service import (
    CashAccessDeniedError,
    _require_operator,
    _write_event,
    allocate_document_number,
    select_legal_document_profile,
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
CARD_REFERENCE = re.compile(r"^[A-Za-z0-9._:/-]{1,64}$")
PAN_LIKE = re.compile(r"(?:\d[._:/-]?){12}\d")


class CheckoutAccessDeniedError(Exception):
    """The actor cannot perform checkout for this shop."""


class CheckoutNotFoundError(Exception):
    """A checkout source does not exist in the tenant scope."""


class CheckoutConflictError(Exception):
    """The checkout conflicts with durable state or trusted configuration."""


class CheckoutInputError(Exception):
    """The checkout input does not reconcile with server-selected facts."""


class CheckoutDiscount(BaseModel):
    model_config = ConfigDict(extra="forbid")

    booking_service_id: UUID
    amount: Money


class CheckoutPayment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    method: Literal["cash", "card"]
    amount: PositiveMoney
    card_slip_reference: str | None = None

    @model_validator(mode="after")
    def validate_card_reference(self) -> "CheckoutPayment":
        if self.method == "cash":
            if self.card_slip_reference is not None:
                raise ValueError("cash payments cannot include a card reference")
            return self
        reference = self.card_slip_reference
        if reference is None or reference != reference.strip():
            raise ValueError("card payments require a trimmed slip reference")
        if not CARD_REFERENCE.fullmatch(reference) or PAN_LIKE.search(reference):
            raise ValueError("card slip reference has an unsafe format")
        return self


class CheckoutRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    booking_id: UUID
    discounts: list[CheckoutDiscount] = Field(default_factory=list, max_length=10)
    payments: list[CheckoutPayment] = Field(min_length=1, max_length=2)
    tip_amount: Money = Decimal("0.00")
    cash_shift_id: UUID | None = None

    @field_validator("discounts")
    @classmethod
    def unique_discounts(cls, value: list[CheckoutDiscount]) -> list[CheckoutDiscount]:
        ids = [discount.booking_service_id for discount in value]
        if len(ids) != len(set(ids)):
            raise ValueError("discount booking_service_id values must be unique")
        return value

    @field_validator("payments")
    @classmethod
    def unique_payment_methods(cls, value: list[CheckoutPayment]) -> list[CheckoutPayment]:
        methods = [payment.method for payment in value]
        if len(methods) != len(set(methods)):
            raise ValueError("payment methods must be unique")
        return value

    @model_validator(mode="after")
    def validate_cash_shift(self) -> "CheckoutRequest":
        has_cash = any(payment.method == "cash" for payment in self.payments)
        if has_cash != (self.cash_shift_id is not None):
            raise ValueError("cash tender requires exactly one cash shift")
        return self


class CheckoutPaymentResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    method: Literal["cash", "card"]
    amount: Decimal
    card_slip_reference: str | None


class CheckoutResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    transaction_id: UUID
    booking_id: UUID
    receipt_number: str
    document_type: str
    currency: Literal["AED"] = "AED"
    subtotal_gross: Decimal
    discount_total: Decimal
    net_total: Decimal
    vat_total: Decimal
    service_gross_total: Decimal
    tip_total: Decimal
    grand_total: Decimal
    payments: list[CheckoutPaymentResponse]
    created_at: datetime


def _json_safe_tiers(
    tiers: list[dict[str, Any]] | None,
) -> list[dict[str, str]] | None:
    if tiers is None:
        return None
    return [{key: str(value) for key, value in tier.items()} for tier in tiers]


async def _booking(
    connection: Any,
    *,
    business_id: UUID,
    shop_id: UUID,
    booking_id: UUID,
) -> tuple[UUID | None, UUID]:
    cursor = await connection.execute(
        """
        select customer_id, barber_membership_id
        from public.bookings
        where id = %s
          and business_id = %s
          and shop_id = %s
          and status = 'completed'
        for update
        """,
        (booking_id, business_id, shop_id),
    )
    row = await cursor.fetchone()
    if row is None:
        raise CheckoutNotFoundError
    return (
        UUID(str(row[0])) if row[0] is not None else None,
        UUID(str(row[1])),
    )


async def _booking_services(
    connection: Any,
    *,
    business_id: UUID,
    shop_id: UUID,
    booking_id: UUID,
) -> list[tuple[Any, ...]]:
    cursor = await connection.execute(
        """
        select id, service_id, service_name, price_gross, vat_rate
        from public.booking_services
        where booking_id = %s
          and business_id = %s
          and shop_id = %s
        order by sort_order, id
        for share
        """,
        (booking_id, business_id, shop_id),
    )
    rows = cast(list[tuple[Any, ...]], await cursor.fetchall())
    if not rows:
        raise CheckoutNotFoundError
    return rows


async def _commission_rule(
    connection: Any,
    *,
    business_id: UUID,
    shop_id: UUID,
    barber_membership_id: UUID,
    at: datetime,
) -> tuple[Any, ...]:
    cursor = await connection.execute(
        """
        select
          id,
          rule_type::text,
          barber_pct,
          tiers,
          effective_from,
          effective_until,
          barber_membership_id is not null
        from public.commission_rules
        where business_id = %s
          and shop_id = %s
          and (barber_membership_id is null or barber_membership_id = %s)
          and effective_from <= %s
          and (effective_until is null or %s < effective_until)
        order by (barber_membership_id is not null) desc
        limit 1
        for share
        """,
        (business_id, shop_id, barber_membership_id, at, at),
    )
    row = await cursor.fetchone()
    if row is None:
        raise CheckoutConflictError("no effective commission rule")
    return cast(tuple[Any, ...], row)


def _legal_snapshot(profile: Any) -> dict[str, Any]:
    return {
        "source_profile_id": str(profile.source_profile_id),
        "legal_name": profile.legal_name,
        "address": profile.address,
        "vat_registered": profile.vat_registered,
        "trn": profile.trn,
        "pricing_mode": profile.pricing_mode,
        "document_type": profile.document_type,
        "currency": profile.currency,
        "effective_from": profile.effective_from.isoformat(),
        "effective_until": (
            profile.effective_until.isoformat() if profile.effective_until is not None else None
        ),
    }


def _rule_snapshot(rule: tuple[Any, ...]) -> dict[str, Any]:
    return {
        "source_rule_id": str(rule[0]),
        "rule_type": str(rule[1]),
        "barber_pct": str(rule[2]) if rule[2] is not None else None,
        "tiers": _json_safe_tiers(rule[3]),
        "effective_from": rule[4].isoformat(),
        "effective_until": rule[5].isoformat() if rule[5] is not None else None,
        "barber_specific": bool(rule[6]),
    }


async def checkout(
    pool: Any,
    *,
    actor_id: UUID,
    business_id: UUID,
    shop_id: UUID,
    idempotency_key: str,
    request_id: str,
    payload: CheckoutRequest,
    at: datetime | None = None,
) -> CheckoutResponse:
    created_at = at or datetime.now(UTC)
    scope = f"pos.checkout:{business_id}:{shop_id}"
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
                raise CheckoutAccessDeniedError from exc
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
                return CheckoutResponse.model_validate(replay)

            customer_id, barber_id = await _booking(
                connection,
                business_id=business_id,
                shop_id=shop_id,
                booking_id=payload.booking_id,
            )
            services = await _booking_services(
                connection,
                business_id=business_id,
                shop_id=shop_id,
                booking_id=payload.booking_id,
            )
            discounts = {
                discount.booking_service_id: discount.amount for discount in payload.discounts
            }
            service_ids = {UUID(str(service[0])) for service in services}
            if not discounts.keys() <= service_ids:
                raise CheckoutInputError("discount references an unknown booked service")

            legal = await select_legal_document_profile(
                connection,
                business_id=business_id,
                shop_id=shop_id,
                at=created_at,
            )
            rule = await _commission_rule(
                connection,
                business_id=business_id,
                shop_id=shop_id,
                barber_membership_id=barber_id,
                at=created_at,
            )

            calculated: list[
                tuple[tuple[Any, ...], Decimal, LineCalculation, CommissionCalculation]
            ] = []
            for service in services:
                booking_service_id = UUID(str(service[0]))
                discount = discounts.get(booking_service_id, Decimal("0.00"))
                vat_rate = Decimal(service[4]) if legal.vat_registered else Decimal("0")
                line = calculate_line(
                    unit_amount=Decimal(service[3]),
                    discount_input=discount,
                    vat_rate=vat_rate,
                    pricing_mode=cast(
                        Literal["vat_inclusive", "vat_exclusive"],
                        legal.pricing_mode,
                    ),
                )
                commission = calculate_commission(
                    commission_base=line.line_net,
                    rule_type=cast(
                        Literal["fixed_percentage", "tier"],
                        str(rule[1]),
                    ),
                    barber_pct=Decimal(rule[2]) if rule[2] is not None else None,
                    tiers=rule[3],
                )
                calculated.append((service, discount, line, commission))

            subtotal = sum(
                (item[2].pre_discount_gross for item in calculated),
                Decimal("0.00"),
            )
            discount_total = sum(
                (item[2].discount_gross for item in calculated),
                Decimal("0.00"),
            )
            net_total = sum((item[2].line_net for item in calculated), Decimal("0.00"))
            vat_total = sum((item[2].line_vat for item in calculated), Decimal("0.00"))
            service_gross = sum((item[2].line_gross for item in calculated), Decimal("0.00"))
            grand_total = service_gross + payload.tip_amount
            if grand_total <= 0:
                raise CheckoutInputError("checkout total must be positive")
            if (
                sum((payment.amount for payment in payload.payments), Decimal("0.00"))
                != grand_total
            ):
                raise CheckoutInputError("payments do not equal the checkout total")

            cash_amount = sum(
                (payment.amount for payment in payload.payments if payment.method == "cash"),
                Decimal("0.00"),
            )
            if payload.cash_shift_id is not None:
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
                    raise CheckoutConflictError("cash shift is not open")

            number = await allocate_document_number(
                connection,
                business_id=business_id,
                shop_id=shop_id,
                counter_kind="sale",
                at=created_at,
            )
            transaction_cursor = await connection.execute(
                """
                insert into public.transactions (
                  business_id, shop_id, booking_id, customer_id,
                  barber_membership_id, cash_shift_id, receipt_number,
                  document_type, subtotal_gross, discount_total, net_total,
                  vat_total, service_gross_total, tip_total, grand_total,
                  legal_snapshot, created_by_auth_user_id, created_at
                )
                values (
                  %s, %s, %s, %s, %s, %s, %s, %s,
                  %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                )
                returning id, created_at
                """,
                (
                    business_id,
                    shop_id,
                    payload.booking_id,
                    customer_id,
                    barber_id,
                    payload.cash_shift_id,
                    number.document_number,
                    legal.document_type,
                    subtotal,
                    discount_total,
                    net_total,
                    vat_total,
                    service_gross,
                    payload.tip_amount,
                    grand_total,
                    Jsonb(_legal_snapshot(legal)),
                    actor_id,
                    created_at,
                ),
            )
            transaction_row = await transaction_cursor.fetchone()
            assert transaction_row is not None
            transaction_id = UUID(str(transaction_row[0]))
            snapshot = _rule_snapshot(rule)

            barber_commission_total = Decimal("0.00")
            shop_share_total = Decimal("0.00")
            for service, discount, line, commission in calculated:
                item_cursor = await connection.execute(
                    """
                    insert into public.transaction_items (
                      business_id, shop_id, transaction_id,
                      booking_service_id, service_id, barber_membership_id,
                      service_name, unit_amount, pricing_mode, vat_rate,
                      pre_discount_gross, discount_input, discount_gross,
                      line_net, line_vat, line_gross, created_at
                    )
                    values (
                      %s, %s, %s, %s, %s, %s, %s, %s, %s,
                      %s, %s, %s, %s, %s, %s, %s, %s
                    )
                    returning id
                    """,
                    (
                        business_id,
                        shop_id,
                        transaction_id,
                        service[0],
                        service[1],
                        barber_id,
                        service[2],
                        Decimal(service[3]),
                        legal.pricing_mode,
                        Decimal(service[4]) if legal.vat_registered else Decimal("0"),
                        line.pre_discount_gross,
                        discount,
                        line.discount_gross,
                        line.line_net,
                        line.line_vat,
                        line.line_gross,
                        created_at,
                    ),
                )
                item_row = await item_cursor.fetchone()
                assert item_row is not None
                await connection.execute(
                    """
                    insert into public.transaction_item_commissions (
                      business_id, shop_id, transaction_id,
                      transaction_item_id, barber_membership_id,
                      commission_rule_id, rule_snapshot, commission_base,
                      barber_commission, shop_share, created_at
                    )
                    values (
                      %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                    )
                    """,
                    (
                        business_id,
                        shop_id,
                        transaction_id,
                        item_row[0],
                        barber_id,
                        rule[0],
                        Jsonb(
                            {
                                **snapshot,
                                "applied_tier": (
                                    {
                                        key: str(value)
                                        for key, value in commission.applied_tier.items()
                                    }
                                    if commission.applied_tier is not None
                                    else None
                                ),
                            }
                        ),
                        line.line_net,
                        commission.barber_commission,
                        commission.shop_share,
                        created_at,
                    ),
                )
                barber_commission_total += commission.barber_commission
                shop_share_total += commission.shop_share

            for payment in payload.payments:
                await connection.execute(
                    """
                    insert into public.transaction_payments (
                      business_id, shop_id, transaction_id, method, amount,
                      card_slip_reference, created_at
                    )
                    values (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        business_id,
                        shop_id,
                        transaction_id,
                        payment.method,
                        payment.amount,
                        payment.card_slip_reference,
                        created_at,
                    ),
                )

            journal_cursor = await connection.execute(
                """
                insert into public.journal_entries (
                  business_id, shop_id, source_type, source_entity_id,
                  idempotency_key, actor_auth_user_id, created_at
                )
                values (%s, %s, 'checkout', %s, %s, %s, %s)
                returning id
                """,
                (
                    business_id,
                    shop_id,
                    transaction_id,
                    idempotency_key,
                    actor_id,
                    created_at,
                ),
            )
            journal_row = await journal_cursor.fetchone()
            assert journal_row is not None
            journal_entry_id = journal_row[0]
            postings: list[tuple[str, UUID | None, Decimal, Decimal]] = [
                (
                    "cash" if payment.method == "cash" else "card_clearing",
                    None,
                    payment.amount,
                    Decimal("0.00"),
                )
                for payment in payload.payments
            ]
            postings.extend(
                [
                    (
                        "service_revenue",
                        None,
                        Decimal("0.00"),
                        shop_share_total,
                    ),
                    (
                        "barber_payable",
                        barber_id,
                        Decimal("0.00"),
                        barber_commission_total,
                    ),
                    (
                        "vat_payable",
                        None,
                        Decimal("0.00"),
                        vat_total,
                    ),
                    (
                        "tip_payable",
                        barber_id,
                        Decimal("0.00"),
                        payload.tip_amount,
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
                        journal_entry_id,
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
                      amount, source_entity_id, created_by_auth_user_id,
                      created_at
                    )
                    values (%s, %s, %s, 'cash_sale', %s, %s, %s, %s)
                    """,
                    (
                        business_id,
                        shop_id,
                        payload.cash_shift_id,
                        cash_amount,
                        transaction_id,
                        actor_id,
                        created_at,
                    ),
                )

            response = CheckoutResponse(
                transaction_id=transaction_id,
                booking_id=payload.booking_id,
                receipt_number=number.document_number,
                document_type=legal.document_type,
                subtotal_gross=subtotal,
                discount_total=discount_total,
                net_total=net_total,
                vat_total=vat_total,
                service_gross_total=service_gross,
                tip_total=payload.tip_amount,
                grand_total=grand_total,
                payments=[
                    CheckoutPaymentResponse(
                        method=payment.method,
                        amount=payment.amount,
                        card_slip_reference=payment.card_slip_reference,
                    )
                    for payment in payload.payments
                ],
                created_at=transaction_row[1],
            )
            await _write_event(
                connection,
                business_id=business_id,
                shop_id=shop_id,
                actor_id=actor_id,
                action="pos.checkout_completed",
                entity_type="transaction",
                entity_id=transaction_id,
                request_id=request_id,
                details={
                    "transaction_id": str(transaction_id),
                    "booking_id": str(payload.booking_id),
                    "receipt_number": number.document_number,
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
    except (CheckoutInputError, CheckoutNotFoundError, CheckoutConflictError):
        raise
    except MoneyCalculationError as exc:
        raise CheckoutConflictError(str(exc)) from exc
    except UniqueViolation as exc:
        raise CheckoutConflictError("checkout already exists") from exc
    except (CheckViolation, ForeignKeyViolation) as exc:
        raise CheckoutConflictError("checkout violates durable constraints") from exc
