from decimal import Decimal as D

import pytest

from radar.analysis import analyze
from radar.finance import landed_cost, max_buy_price, scenario
from radar.schemas import BuyerProfile, FeeProfile


def test_financial_breakdown(item, prefs):
    a = analyze(item, prefs)
    assert a.landed == D("20.00")
    assert a.low.commission == D("4.50")
    assert a.low.processing == D("1.61")
    assert a.low.net_resale == D("37.89")
    assert a.low.profit == D("17.89")
    assert a.low.roi == D("17.89") / 20
    assert a.tier == "BUY CANDIDATE"
    assert a.high.profit > a.low.profit


@pytest.mark.parametrize(
    "field",
    [
        "item_price",
        "buyer_premium",
        "buyer_service_fee",
        "sales_tax",
        "shipping_cost",
        "handling_fee",
    ],
)
def test_unknown_cost_never_zero_or_buy(item, prefs, field):
    a = analyze(item.model_copy(update={field: None}), prefs)
    assert a.landed is None
    assert a.low.profit is None
    assert a.tier != "BUY CANDIDATE"
    assert field in " ".join(a.risks)


def test_unknown_alaska_shipping_not_rejected_or_buy(item, prefs):
    a = analyze(item.model_copy(update={"shipping_status": "UNKNOWN"}), prefs)
    assert a.tier != "BUY CANDIDATE"
    assert a.tier != "PASS"
    assert a.confidence == "LOW"


def test_unavailable_shipping_pass(item, prefs):
    assert (
        analyze(item.model_copy(update={"shipping_status": "CONFIRMED_UNAVAILABLE"}), prefs).tier
        == "PASS"
    )


def test_no_invented_resale(item, prefs):
    a = analyze(item.model_copy(update={"resale_low": None, "resale_high": None}), prefs)
    assert a.low is None and a.high is None
    assert a.maximum_buy.maximum_item_price is None
    assert a.tier != "BUY CANDIDATE"


def test_non_usd_cannot_support_buy(item, prefs):
    a = analyze(item.model_copy(update={"currency": "CAD"}), prefs)
    assert a.landed is None and a.low is None
    assert a.maximum_buy.maximum_item_price is None
    assert a.tier != "BUY CANDIDATE"


def test_auction_premium_lot_fee_tax(item, prefs):
    prefs.buyer_profiles["auction"] = BuyerProfile(
        premium_rate="0.20", per_lot_fee="3", tax_rate="0.05"
    )
    item = item.model_copy(
        update={
            "buyer_profile": "auction",
            "item_price": D("10"),
            "buyer_premium": None,
            "buyer_service_fee": None,
            "sales_tax": None,
        }
    )
    costs, landed, unknown = landed_cost(item, prefs)
    assert costs["buyer_premium"] == D("2")
    assert costs["buyer_service_fee"] == D("3")
    assert costs["sales_tax"] == D("0.75")
    assert landed == D("20.75") and not unknown


def test_observed_fees_override_profile(item, prefs):
    prefs.buyer_profiles["auction"] = BuyerProfile(premium_rate="0.20")
    assert landed_cost(item.model_copy(update={"buyer_profile": "auction"}), prefs)[1] == D("20")


@pytest.mark.parametrize(
    "premium,lot,tax,profit,roi",
    [
        ("0", "0", "0", "15", "0.60"),
        ("0.20", "3", "0.05", "15", "0.60"),
        ("0.25", "4.50", "0", "2", "1.2"),
    ],
)
def test_maximum_bid_is_highest_cent(premium, lot, tax, profit, roi):
    buyer = BuyerProfile(premium_rate=premium, per_lot_fee=lot, tax_rate=tax)
    result = max_buy_price(D("60"), FeeProfile(), D("7"), D("2"), D(profit), D(roi), buyer)
    assert result.maximum_item_price is not None
    assert result.profit >= D(profit)
    assert result.roi >= D(roi)
    # Independent brute-force cost at the next cent must violate at least one constraint.
    price = result.maximum_item_price + D("0.01")
    premium_fee = (price * D(premium)).quantize(D(".01"), rounding="ROUND_HALF_UP")
    taxable = price + premium_fee + D(lot)
    tax_fee = (taxable * D(tax)).quantize(D(".01"), rounding="ROUND_HALF_UP")
    landed = price + premium_fee + D(lot) + tax_fee + D("9")
    net = scenario(D("60"), FeeProfile(), None).net_resale
    assert net - landed < D(profit) or (net - landed) / landed < D(roi)


def test_maximum_missing_shipping():
    result = max_buy_price(D("60"), FeeProfile(), None, D(0), D(15), D(".6"), BuyerProfile())
    assert result.maximum_item_price is None
    assert "UNKNOWN" in result.limiting_constraint


def test_impossible_maximum():
    result = max_buy_price(D("20"), FeeProfile(), D(50), D(0), D(15), D(".6"), BuyerProfile())
    assert result.maximum_item_price is None


def test_zero_landed_roi_is_undefined(item, prefs):
    a = analyze(item.model_copy(update={"item_price": D(0), "shipping_cost": D(0)}), prefs)
    assert a.low.roi is None
