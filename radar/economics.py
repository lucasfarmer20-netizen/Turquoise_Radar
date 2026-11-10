"""Owner economics on a card: the walk-away price.

Resale target = the LOW end of the screening's resale range (the buyer's downside).
Net after platform fees (Whatnot: 8% commission below $1,500 per item, 0% at/above;
2.9% + $0.30 processing on everything) must cover landed cost plus the target margin.
Margin tiers by sale price (owner rule: higher % on cheap pieces, lower on expensive):
    RADAR_MARGIN_TIERS="100:0.50,500:0.35,inf:0.25"
Walk-away landed = net / (1 + margin). Walk-away BID = landed - shipping, and for
auction-house lots also divided by (1 + buyer's premium).
"""

import os
from decimal import ROUND_DOWN, Decimal

CENT = Decimal("0.01")


def margin_tiers():
    raw = os.environ.get("RADAR_MARGIN_TIERS", "100:0.50,500:0.35,inf:0.25")
    tiers = []
    for part in raw.split(","):
        bound, rate = part.split(":")
        bound = Decimal("Infinity") if bound.strip() == "inf" else Decimal(bound)
        tiers.append((bound, Decimal(rate)))
    return sorted(tiers, key=lambda t: t[0])


def target_margin(sale_price):
    for bound, rate in margin_tiers():
        if sale_price < bound:
            return rate
    return margin_tiers()[-1][1]


def net_of_fees(sale_price):
    commission = Decimal(os.environ.get("RADAR_FEE_COMMISSION", "0.08"))
    threshold = Decimal(os.environ.get("RADAR_FEE_COMMISSION_FREE_ABOVE", "1500"))
    processing = Decimal(os.environ.get("RADAR_FEE_PROCESSING", "0.029"))
    fixed = Decimal(os.environ.get("RADAR_FEE_FIXED", "0.30"))
    rate = processing + (commission if sale_price < threshold else Decimal(0))
    return sale_price * (1 - rate) - fixed


def walk_away(resale_low, shipping=None, premium_rate=None):
    """Return dict(sale, margin, net, landed, bid) or None when resale is unknown."""
    if resale_low in (None, ""):
        return None
    sale = Decimal(str(resale_low))
    if sale <= 0:
        return None
    margin = target_margin(sale)
    net = net_of_fees(sale)
    landed = net / (1 + margin)
    bid = landed - (Decimal(str(shipping)) if shipping not in (None, "") else Decimal(0))
    if premium_rate not in (None, "") and Decimal(str(premium_rate)) > 0:
        bid = bid / (1 + Decimal(str(premium_rate)))
    q = lambda d: d.quantize(CENT, rounding=ROUND_DOWN)  # noqa: E731
    return dict(
        sale=q(sale), margin=margin, net=q(net), landed=q(landed), bid=max(q(bid), Decimal(0))
    )
