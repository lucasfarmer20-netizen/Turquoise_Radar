from decimal import Decimal

from pydantic import BaseModel

from radar.finance import MaximumBuy, Scenario, landed_cost, max_buy_price, scenario
from radar.melt import MeltResult, calculate_melt
from radar.schemas import Preferences, Shipping, ValueStatus


class Analysis(BaseModel):
    score: int
    categories: list[str]
    tier: str
    confidence: str
    landed: Decimal | None
    costs: dict[str, Decimal | None]
    cost_status: dict[str, str]
    acquisition_explanations: list[str]
    low: Scenario | None
    high: Scenario | None
    maximum_buy: MaximumBuy
    melt: MeltResult
    evidence: list[str]
    risks: list[str]


def analyze(item, prefs: Preferences) -> Analysis:
    # Explicitly marked UNKNOWN amounts cannot supply numerical evidence.
    item = item.model_copy(
        update={
            key: None
            for key, status in item.cost_status.items()
            if status == ValueStatus.UNKNOWN
            and key
            in (
                "item_price",
                "buyer_premium",
                "buyer_service_fee",
                "sales_tax",
                "shipping_cost",
                "handling_fee",
            )
        }
    )
    costs, landed, unknown = landed_cost(item, prefs)
    fees = prefs.resale_profiles[prefs.resale_channel]
    resale_args = dict(
        buyer_shipping=item.resale_buyer_shipping,
        buyer_tax=item.resale_buyer_tax,
        category=item.selling_category,
        tier=item.commission_tier,
    )
    state_args = dict(
        shipping_status=item.resale_shipping_status, tax_status=item.resale_tax_status
    )
    low = (
        scenario(item.resale_low, fees, landed, **resale_args, **state_args)
        if item.resale_low is not None
        else None
    )
    high = (
        scenario(item.resale_high, fees, landed, **resale_args, **state_args)
        if item.resale_high is not None
        else None
    )
    maximum = max_buy_price(
        item.resale_low,
        fees,
        item.shipping_cost,
        item.handling_fee,
        prefs.min_profit,
        prefs.min_roi,
        buyer=prefs.buyer_profiles.get(item.buyer_profile) if item.buyer_profile else None,
        fixed_premium=item.buyer_premium,
        fixed_service=item.buyer_service_fee,
        fixed_tax=item.sales_tax,
        invoice_allocation=item.invoice_fee_allocation,
        invoice_confirmed=item.invoice_allocation_confirmed,
        **resale_args,
    )
    melt = calculate_melt(item, prefs, landed)
    evidence, risks, categories = [], [], []
    score = 10
    buyer = prefs.buyer_profiles.get(item.buyer_profile) if item.buyer_profile else None
    unconfirmed = []
    cost_status = {}
    for key, value in costs.items():
        if value is None:
            cost_status[key] = "UNKNOWN"
            continue
        configured = (
            key in ("buyer_premium", "buyer_service_fee", "sales_tax")
            and getattr(item, key) is None
            and buyer
        )
        if key in ("shipping_cost", "handling_fee") and getattr(item, key) is None and buyer:
            configured = True
        status = (
            buyer.verification_status
            if configured
            else item.cost_status.get(key, ValueStatus.ESTIMATED)
        )
        cost_status[key] = str(status)
        if status != ValueStatus.CONFIRMED:
            unconfirmed.append(key)
    if unconfirmed:
        risks.append("ESTIMATED or unconfirmed acquisition inputs: " + ", ".join(unconfirmed))
    if low and low.missing:
        risks.append("UNKNOWN resale inputs: " + ", ".join(low.missing))
    if item.resale_low is not None and not item.resale_confirmed:
        risks.append("Owner confirmation of resale evidence/scenario required")
    if low and (
        fees.verification_status != ValueStatus.CONFIRMED
        or any(v != "CONFIRMED" for v in low.input_status.values())
    ):
        risks.append("Resale transaction/fee inputs are estimated or unconfirmed")
    if not item.condition:
        risks.append("Condition evidence is missing")
    if item.material_evidence == "unknown":
        risks.append("Material evidence is missing")
    if unknown:
        risks.append("UNKNOWN costs: " + ", ".join(unknown))
    if item.shipping_status != Shipping.CONFIRMED_TO_ALASKA:
        risks.append("Shipping to 99669 is " + item.shipping_status)
    if item.currency != "USD":
        risks.append("Non-USD listing: USD financial comparison unavailable")
        landed, low, high = None, None, None
        maximum = MaximumBuy(limiting_constraint="UNKNOWN currency conversion")
        melt = calculate_melt(item, prefs, None)
    missing_profile = item.buyer_profile and item.buyer_profile not in prefs.buyer_profiles
    if missing_profile:
        risks.append("Referenced buyer profile is missing")
    if item.resale_low is None:
        risks.append("No supported resale estimate")
    else:
        evidence.append("Resale basis: " + item.resale_basis)
    if "ring" in item.raw_title.casefold() and landed is not None and landed <= prefs.ring_high:
        categories.append("RING RESTOCK")
        score += 20
        evidence.append("Affordable ring; landed band is a preference, not a rejection rule")
    meets_resale = bool(
        low
        and low.profit is not None
        and low.roi is not None
        and low.profit >= prefs.min_profit
        and low.roi >= prefs.min_roi
    )
    if meets_resale:
        categories.append("WHATNOT FLIP")
        score += 40
        evidence.append("Conservative resale meets minimum profit and ROI")
    if melt.eligible and item.currency == "USD":
        categories.append("STERLING / MELT")
        score += 40
        evidence.append("Reliable net metal and material support conservative melt protection")
    if item.collector_evidence or item.hallmark_text:
        categories.append("COLLECTOR UPSIDE")
        score += 10
        risks.append("Maker/provenance premium requires research; a hallmark alone is insufficient")
    if item.piece_count > 1 or item.combined_shipping_available:
        categories.append("MULTI-LOT")
        score += 10
        evidence.append("Multiple pieces or combined shipping; no savings assumed")
    risks.extend(melt.warnings)
    critical = bool(
        unknown
        or unconfirmed
        or not item.condition
        or item.material_evidence == "unknown"
        or (
            item.resale_low is not None
            and (
                not item.resale_confirmed
                or bool(low and low.missing)
                or fees.verification_status != ValueStatus.CONFIRMED
                or bool(low and any(v != "CONFIRMED" for v in low.input_status.values()))
            )
        )
        or landed is None
        or missing_profile
        or item.shipping_status != Shipping.CONFIRMED_TO_ALASKA
    )
    strong = meets_resale or (melt.eligible and item.currency == "USD")
    confidence = "LOW" if critical else "HIGH" if strong else "MEDIUM"
    if item.shipping_status == Shipping.CONFIRMED_UNAVAILABLE:
        tier, score = "PASS", 0
    elif strong and not critical:
        tier = "BUY CANDIDATE"
    elif strong or (categories and not critical):
        tier = "STRONG REVIEW"
    elif low and low.profit is not None and low.profit < 0 and not critical:
        tier = "PASS"
    else:
        tier = "RESEARCH NEEDED"
    if critical:
        score = min(score, 49)
    return Analysis(
        score=min(score, 100),
        categories=categories,
        tier=tier,
        confidence=confidence,
        landed=landed,
        costs=costs,
        cost_status=cost_status,
        acquisition_explanations=(
            [
                f"Profile source: {buyer.source}; as of {buyer.as_of}; "
                f"{buyer.verification_status}.",
                f"Premium {buyer.premium_rate} on {buyer.premium_base}; "
                f"service {buyer.service_rate} "
                f"on {buyer.service_base} + fixed {buyer.fixed_marketplace_fee} "
                f"+ per lot {buyer.per_lot_fee}.",
                f"Tax {buyer.tax_rate} on {buyer.tax_base}. Invoice allocation: "
                f"{item.invoice_fee_allocation}; confirmed={item.invoice_allocation_confirmed}.",
                "Explicit observed amounts override profile rates at every proposed bid.",
            ]
            if buyer
            else [
                "Observed absolute fees are fixed at each proposed bid; confirm that "
                "the source does not instead charge a percentage."
            ]
        ),
        low=low,
        high=high,
        maximum_buy=maximum,
        melt=melt,
        evidence=evidence,
        risks=risks,
    )
