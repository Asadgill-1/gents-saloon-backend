from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Literal

FILS = Decimal("0.01")
ONE_HUNDRED = Decimal("100")


class MoneyCalculationError(ValueError):
    """The trusted pricing or commission configuration cannot reconcile."""


def round_fils(value: Decimal) -> Decimal:
    return value.quantize(FILS, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class LineCalculation:
    pre_discount_gross: Decimal
    discount_gross: Decimal
    line_net: Decimal
    line_vat: Decimal
    line_gross: Decimal


@dataclass(frozen=True)
class CommissionCalculation:
    barber_commission: Decimal
    shop_share: Decimal
    applied_tier: dict[str, Any] | None


def calculate_line(
    *,
    unit_amount: Decimal,
    discount_input: Decimal,
    vat_rate: Decimal,
    pricing_mode: Literal["vat_inclusive", "vat_exclusive"],
) -> LineCalculation:
    unit = round_fils(unit_amount)
    discount = round_fils(discount_input)
    if unit < 0 or discount < 0 or discount > unit:
        raise MoneyCalculationError("discount exceeds the service amount")
    if vat_rate < 0 or vat_rate > ONE_HUNDRED:
        raise MoneyCalculationError("VAT rate is outside the supported range")

    if pricing_mode == "vat_inclusive":
        line_gross = unit - discount
        divisor = ONE_HUNDRED + vat_rate
        line_vat = round_fils(line_gross * vat_rate / divisor) if vat_rate else Decimal("0.00")
        line_net = line_gross - line_vat
        return LineCalculation(
            pre_discount_gross=unit,
            discount_gross=discount,
            line_net=line_net,
            line_vat=line_vat,
            line_gross=line_gross,
        )

    line_net = unit - discount
    line_vat = round_fils(line_net * vat_rate / ONE_HUNDRED)
    line_gross = line_net + line_vat
    pre_discount_gross = unit + round_fils(unit * vat_rate / ONE_HUNDRED)
    return LineCalculation(
        pre_discount_gross=pre_discount_gross,
        discount_gross=pre_discount_gross - line_gross,
        line_net=line_net,
        line_vat=line_vat,
        line_gross=line_gross,
    )


def calculate_commission(
    *,
    commission_base: Decimal,
    rule_type: Literal["fixed_percentage", "tier"],
    barber_pct: Decimal | None,
    tiers: list[dict[str, Any]] | None,
) -> CommissionCalculation:
    base = round_fils(commission_base)
    applied_tier: dict[str, Any] | None = None
    if rule_type == "fixed_percentage":
        if barber_pct is None:
            raise MoneyCalculationError("fixed commission percentage is missing")
        barber_amount = round_fils(base * barber_pct / ONE_HUNDRED)
    else:
        if not tiers:
            raise MoneyCalculationError("commission tiers are missing")
        for tier in tiers:
            minimum = Decimal(str(tier["min_base"]))
            maximum = Decimal(str(tier["max_base"])) if "max_base" in tier else None
            if base >= minimum and (maximum is None or base < maximum):
                applied_tier = tier
                if "barber_pct" in tier:
                    barber_amount = round_fils(
                        base * Decimal(str(tier["barber_pct"])) / ONE_HUNDRED
                    )
                else:
                    barber_amount = round_fils(Decimal(str(tier["barber_flat"])))
                break
        else:
            raise MoneyCalculationError("no commission tier covers the service amount")

    if barber_amount < 0 or barber_amount > base:
        raise MoneyCalculationError("barber commission exceeds the commission base")
    return CommissionCalculation(
        barber_commission=barber_amount,
        shop_share=base - barber_amount,
        applied_tier=applied_tier,
    )


def proportional_cumulative(
    *,
    original_output: Decimal,
    original_input: Decimal,
    cumulative_input: Decimal,
) -> Decimal:
    source = round_fils(original_input)
    cumulative = round_fils(cumulative_input)
    output = round_fils(original_output)
    if source <= 0 or cumulative < 0 or cumulative > source:
        raise MoneyCalculationError("proportional correction is outside the original amount")
    if cumulative == source:
        return output
    return round_fils(output * cumulative / source)
