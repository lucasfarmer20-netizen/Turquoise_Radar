"""Deterministic USD calculations. None always means unknown, never zero."""

from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal

from pydantic import BaseModel

from radar.schemas import BuyerProfile, FeeProfile, ListingInput, Preferences

D = Decimal
CENT = D("0.01")


def money(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


class Scenario(BaseModel):
    sale_price: Decimal
    commission: Decimal
    processing: Decimal
    packaging: Decimal
    outbound_shipping: Decimal
    net_resale: Decimal
    profit: Decimal | None
    roi: Decimal | None


class MaximumBuy(BaseModel):
    maximum_item_price: Decimal | None = None
    landed_cost: Decimal | None = None
    profit: Decimal | None = None
    roi: Decimal | None = None
    limiting_constraint: str


def scenario(sale: Decimal, fees: FeeProfile, landed: Decimal | None) -> Scenario:
    commission = money(sale * fees.commission)
    processing = money(sale * fees.processing + fees.processing_fixed)
    net = money(sale - commission - processing - fees.packaging - fees.outbound_shipping)
    profit = money(net - landed) if landed is not None else None
    return Scenario(
        sale_price=sale,
        commission=commission,
        processing=processing,
        packaging=fees.packaging,
        outbound_shipping=fees.outbound_shipping,
        net_resale=net,
        profit=profit,
        roi=profit / landed if landed is not None and landed > 0 else None,
    )


def acquisition_costs(item: ListingInput, price: Decimal, prefs: Preferences):
    """Explicit observed fees override profiles. Taxes apply to price + premium + service.

    Shipping/handling are not assumed taxable; configure observed tax when required.
    """
    profile = prefs.buyer_profiles.get(item.buyer_profile) if item.buyer_profile else None
    premium = item.buyer_premium
    service = item.buyer_service_fee
    tax = item.sales_tax
    if profile:
        premium = premium if premium is not None else money(price * profile.premium_rate)
        service = (
            service
            if service is not None
            else money(price * profile.service_rate + profile.per_lot_fee)
        )
        tax = tax if tax is not None else money((price + premium + service) * profile.tax_rate)
    return {
        "item_price": price,
        "buyer_premium": premium,
        "buyer_service_fee": service,
        "sales_tax": tax,
        "shipping_cost": item.shipping_cost,
        "handling_fee": item.handling_fee,
    }


def landed_cost(item: ListingInput, prefs: Preferences):
    costs = (
        acquisition_costs(item, item.item_price, prefs)
        if item.item_price is not None
        else {
            "item_price": None,
            "buyer_premium": item.buyer_premium,
            "buyer_service_fee": item.buyer_service_fee,
            "sales_tax": item.sales_tax,
            "shipping_cost": item.shipping_cost,
            "handling_fee": item.handling_fee,
        }
    )
    unknown = [key for key, value in costs.items() if value is None]
    total = None if unknown else money(sum(costs.values(), D(0)))
    return costs, total, unknown


def max_buy_price(
    conservative_resale: Decimal | None,
    fees: FeeProfile,
    shipping: Decimal | None,
    handling: Decimal | None,
    target_profit: Decimal,
    required_roi: Decimal,
    buyer: BuyerProfile | None = None,
    fixed_premium: Decimal | None = None,
    fixed_service: Decimal | None = None,
    fixed_tax: Decimal | None = None,
) -> MaximumBuy:
    """Solve on integer cents; return the greatest feasible bid under both constraints.

    Profile percentages are recalculated at each bid. Fixed observed fees alone
    cannot establish whether a marketplace uses percentages; callers must know.
    """
    if conservative_resale is None or shipping is None or handling is None:
        return MaximumBuy(limiting_constraint="UNKNOWN resale, shipping or handling")
    if buyer is None and any(v is None for v in (fixed_premium, fixed_service, fixed_tax)):
        return MaximumBuy(limiting_constraint="UNKNOWN buyer fees or tax")
    buyer = buyer or BuyerProfile()
    net = scenario(conservative_resale, fees, None).net_resale
    profit_limit = net - target_profit
    roi_limit = net / (1 + required_roi)
    cap = min(profit_limit, roi_limit)

    def cost(cents):
        price = D(cents) / 100
        premium = fixed_premium if fixed_premium is not None else money(price * buyer.premium_rate)
        service = (
            fixed_service
            if fixed_service is not None
            else money(price * buyer.service_rate + buyer.per_lot_fee)
        )
        tax = (
            fixed_tax
            if fixed_tax is not None
            else money((price + premium + service) * buyer.tax_rate)
        )
        return money(price + premium + service + tax + shipping + handling)

    if cap < 0 or cost(0) > cap:
        return MaximumBuy(limiting_constraint="No nonnegative item price satisfies targets")
    lo, hi = 0, max(0, int((cap * 100).to_integral_value(rounding=ROUND_DOWN))) + 1
    while lo + 1 < hi:
        mid = (lo + hi) // 2
        if cost(mid) <= cap:
            lo = mid
        else:
            hi = mid
    landed = cost(lo)
    profit = money(net - landed)
    return MaximumBuy(
        maximum_item_price=D(lo) / 100,
        landed_cost=landed,
        profit=profit,
        roi=profit / landed if landed else None,
        limiting_constraint="minimum profit" if profit_limit <= roi_limit else "minimum ROI",
    )
