"""Inspectable Decimal fee bases and cent-exact maximum buys. None means unknown."""

from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal

from pydantic import BaseModel, Field

from radar.schemas import BuyerProfile, FeeProfile, Preferences

D = Decimal
CENT = D("0.01")


def money(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


class Scenario(BaseModel):
    sale_price: Decimal
    commission_base: Decimal
    processing_base: Decimal | None
    commission_rate: Decimal | None
    commission: Decimal | None
    processing: Decimal | None
    packaging: Decimal | None
    outbound_shipping: Decimal | None
    buyer_shipping: Decimal | None
    buyer_tax: Decimal | None
    net_resale: Decimal | None
    profit: Decimal | None
    roi: Decimal | None
    missing: list[str] = Field(default_factory=list)
    explanations: list[str] = Field(default_factory=list)
    input_status: dict[str, str] = Field(default_factory=dict)


class MaximumBuy(BaseModel):
    maximum_item_price: Decimal | None = None
    landed_cost: Decimal | None = None
    profit: Decimal | None = None
    roi: Decimal | None = None
    limiting_constraint: str


def scenario(
    sale,
    fees: FeeProfile,
    landed,
    buyer_shipping=None,
    buyer_tax=None,
    category="jewelry",
    tier="standard",
    shipping_status="UNKNOWN",
    tax_status="UNKNOWN",
):
    missing, explanations, statuses = [], [], {}
    for name, value in (("buyer_shipping", buyer_shipping), ("buyer_tax", buyer_tax)):
        assumption = getattr(fees, name + "_assumption")
        status = shipping_status if name == "buyer_shipping" else tax_status
        if value is None and fees.scenario_name and assumption is not None:
            value = assumption
            status = "ESTIMATED"
            explanations.append(f"{name}: named scenario '{fees.scenario_name}' = {value}")
        if name == "buyer_shipping":
            buyer_shipping = value
        else:
            buyer_tax = value
        statuses[name] = status if value is not None else "UNKNOWN"
        if value is None:
            missing.append(name)
    key = f"{category}:{tier}"
    rate = fees.commission_tiers.get(key, fees.commission if tier == "standard" else None)
    if rate is None:
        missing.append("commission tier " + key)
    commission = money(sale * rate) if rate is not None else None
    base = (
        sale + buyer_shipping + buyer_tax
        if buyer_shipping is not None and buyer_tax is not None
        else None
    )
    processing = money(base * fees.processing + fees.processing_fixed) if base is not None else None
    for name in ("packaging", "outbound_shipping"):
        if getattr(fees, name) is None:
            missing.append(name)
    net = (
        None
        if missing
        else money(sale - commission - processing - fees.packaging - fees.outbound_shipping)
    )
    profit = money(net - landed) if net is not None and landed is not None else None
    explanations.extend(
        [
            f"Commission: item-only {sale} × {rate}; category/tier {key}.",
            f"Processing: (item {sale} + buyer shipping {buyer_shipping} + buyer tax {buyer_tax}) "
            f"× {fees.processing} + {fees.processing_fixed} once per transaction.",
            f"Packaging {fees.packaging}; seller-paid outbound shipping {fees.outbound_shipping}.",
            f"Fee source: {fees.source}; as of {fees.as_of}; {fees.verification_status}.",
        ]
    )
    return Scenario(
        sale_price=sale,
        commission_base=sale,
        processing_base=base,
        commission_rate=rate,
        commission=commission,
        processing=processing,
        packaging=fees.packaging,
        outbound_shipping=fees.outbound_shipping,
        buyer_shipping=buyer_shipping,
        buyer_tax=buyer_tax,
        net_resale=net,
        profit=profit,
        roi=profit / landed if profit is not None and landed > 0 else None,
        missing=missing,
        explanations=explanations,
        input_status=statuses,
    )


def _base(kind, price, shipping, handling=None, premium=None, service=None):
    if kind == "item":
        return price
    if kind == "item_shipping":
        return price + shipping if shipping is not None else None
    if kind == "item_premium_service":
        return price + premium + service if premium is not None and service is not None else None
    values = (price, shipping, handling, premium, service)
    return sum(values, D(0)) if all(v is not None for v in values) else None


def acquisition_components(
    price,
    shipping,
    handling,
    buyer=None,
    fixed_premium=None,
    fixed_service=None,
    fixed_tax=None,
    invoice_allocation=None,
    invoice_confirmed=False,
):
    if buyer:
        shipping = shipping if shipping is not None else buyer.shipping
        handling = handling if handling is not None else buyer.handling
    premium, service, tax = fixed_premium, fixed_service, fixed_tax
    if buyer and price is not None:
        premium_base = _base(buyer.premium_base, price, shipping)
        service_base = _base(buyer.service_base, price, shipping)
        if premium is None and premium_base is not None:
            premium = money(premium_base * buyer.premium_rate)
        if service is None and service_base is not None:
            invoice = D(0)
            if buyer.per_invoice_fee:
                invoice = invoice_allocation if invoice_confirmed else None
            if invoice is not None:
                service = money(
                    service_base * buyer.service_rate
                    + buyer.fixed_marketplace_fee
                    + buyer.per_lot_fee
                    + invoice
                )
        tax_base = _base(buyer.tax_base, price, shipping, handling, premium, service)
        if tax is None and tax_base is not None:
            tax = money(tax_base * buyer.tax_rate)
    return dict(
        item_price=price,
        buyer_premium=premium,
        buyer_service_fee=service,
        sales_tax=tax,
        shipping_cost=shipping,
        handling_fee=handling,
    )


def acquisition_costs(item, price, prefs: Preferences):
    buyer = prefs.buyer_profiles.get(item.buyer_profile) if item.buyer_profile else None
    return acquisition_components(
        price,
        item.shipping_cost,
        item.handling_fee,
        buyer,
        item.buyer_premium,
        item.buyer_service_fee,
        item.sales_tax,
        item.invoice_fee_allocation,
        item.invoice_allocation_confirmed,
    )


def landed_cost(item, prefs):
    costs = acquisition_costs(item, item.item_price, prefs)
    unknown = [key for key, value in costs.items() if value is None]
    return costs, None if unknown else money(sum(costs.values(), D(0))), unknown


def max_buy_price(
    conservative_resale,
    fees: FeeProfile,
    shipping,
    handling,
    target_profit,
    required_roi,
    buyer: BuyerProfile | None = None,
    fixed_premium=None,
    fixed_service=None,
    fixed_tax=None,
    *,
    buyer_shipping=None,
    buyer_tax=None,
    category="jewelry",
    tier="standard",
    invoice_allocation=None,
    invoice_confirmed=False,
):
    if conservative_resale is None:
        return MaximumBuy(limiting_constraint="UNKNOWN resale")
    net_scenario = scenario(
        conservative_resale, fees, None, buyer_shipping, buyer_tax, category, tier
    )
    if net_scenario.net_resale is None:
        return MaximumBuy(
            limiting_constraint="UNKNOWN resale inputs: " + ", ".join(net_scenario.missing)
        )
    probe = acquisition_components(
        D(0),
        shipping,
        handling,
        buyer,
        fixed_premium,
        fixed_service,
        fixed_tax,
        invoice_allocation,
        invoice_confirmed,
    )
    if any(v is None for v in probe.values()):
        return MaximumBuy(
            limiting_constraint="UNKNOWN shipping, handling, buyer fees, tax or invoice allocation"
        )
    net = net_scenario.net_resale
    profit_limit, roi_limit = net - target_profit, net / (1 + required_roi)
    cap = min(profit_limit, roi_limit)

    def cost(cents):
        return money(
            sum(
                acquisition_components(
                    D(cents) / 100,
                    shipping,
                    handling,
                    buyer,
                    fixed_premium,
                    fixed_service,
                    fixed_tax,
                    invoice_allocation,
                    invoice_confirmed,
                ).values(),
                D(0),
            )
        )

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
