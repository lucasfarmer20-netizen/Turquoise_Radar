from decimal import Decimal

from pydantic import BaseModel

from radar.finance import MaximumBuy, Scenario, landed_cost, max_buy_price, scenario
from radar.melt import MeltResult, calculate_melt
from radar.schemas import Preferences, Shipping


class Analysis(BaseModel):
    score: int
    categories: list[str]
    tier: str
    confidence: str
    landed: Decimal | None
    costs: dict[str, Decimal | None]
    low: Scenario | None
    high: Scenario | None
    maximum_buy: MaximumBuy
    melt: MeltResult
    evidence: list[str]
    risks: list[str]


def analyze(item, prefs: Preferences) -> Analysis:
    costs, landed, unknown = landed_cost(item, prefs)
    fees = prefs.resale_profiles[prefs.resale_channel]
    low = scenario(item.resale_low, fees, landed) if item.resale_low is not None else None
    high = scenario(item.resale_high, fees, landed) if item.resale_high is not None else None
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
    )
    melt = calculate_melt(item, prefs, landed)
    evidence, risks, categories = [], [], []
    score = 10
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
        low=low,
        high=high,
        maximum_buy=maximum,
        melt=melt,
        evidence=evidence,
        risks=risks,
    )
