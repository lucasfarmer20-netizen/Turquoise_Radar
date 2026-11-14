"""Walk-away math, pass reasons, and the auction-watch merge; no network."""

import asyncio
from decimal import Decimal

from radar import watch
from radar.db import DiscoveredItem
from radar.economics import net_of_fees, target_margin, walk_away


def test_margin_tiers_and_fees(monkeypatch):
    monkeypatch.delenv("RADAR_MARGIN_TIERS", raising=False)
    assert target_margin(Decimal(50)) == Decimal("0.50")
    assert target_margin(Decimal(250)) == Decimal("0.35")
    assert target_margin(Decimal(900)) == Decimal("0.25")
    # under $1,500: 8% + 2.9% + $0.30; at/above: 2.9% + $0.30
    assert net_of_fees(Decimal(100)) == Decimal("100") * Decimal("0.891") - Decimal("0.30")
    assert net_of_fees(Decimal(2000)) == Decimal("2000") * Decimal("0.971") - Decimal("0.30")


def test_walk_away_bid_accounts_for_shipping_and_premium():
    w = walk_away(250, 9.5)
    assert w["margin"] == Decimal("0.35") and w["landed"] == Decimal("164.77")
    assert w["bid"] == Decimal("155.27")
    # 23% buyer's premium auction house
    w2 = walk_away(400, 0, 0.23)
    assert w2["bid"] == Decimal("214.45")
    assert walk_away(None) is None and walk_away(0) is None
    assert walk_away(5, 50)["bid"] == Decimal(0)  # never negative


def test_apply_item_merges_bid_count_end_and_ship_to():
    n = {"item_price": "39.00", "buying_options": ["AUCTION"], "shipping_status": "UNKNOWN"}
    raw = {
        "currentBidPrice": {"value": "52.00", "currency": "USD"},
        "bidCount": 3,
        "itemEndDate": "2026-11-12T03:00:00.000Z",
        "buyingOptions": ["AUCTION"],
        "shipToLocations": {
            "regionIncluded": [{"regionId": "US"}],
            "regionExcluded": [{"regionId": "AK"}],
        },
    }
    out = watch.apply_item(n, raw)
    assert out["item_price"] == "52.00" and out["bid_count"] == 3
    assert out["item_end_time"] == "2026-11-12T03:00:00.000Z"
    assert out["shipping_status"] == "EXCLUDED_ALASKA" and out["watched_at"]
    ok = watch.apply_item(n, {**raw, "shipToLocations": {"regionIncluded": [{"regionId": "US"}]}})
    assert ok["shipping_status"] == "CONFIRMED_TO_ALASKA"


def test_refresh_only_touches_interested_live_ebay_auctions(tmp_path, monkeypatch):
    from datetime import UTC, datetime, timedelta

    from radar.db import database

    engine, sessions = database(f"sqlite:///{tmp_path / 'w.db'}")
    future = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    past = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    with sessions.begin() as s:
        for i, (status, src, opts, end) in enumerate(
            [
                ("INTERESTED", "eBay", ["AUCTION"], future),  # 1 watched
                ("INTERESTED", "eBay", ["FIXED_PRICE"], future),  # 2 not auction
                ("INTERESTED", "HiBid", ["AUCTION"], future),  # 3 not eBay
                ("INTERESTED", "eBay", ["AUCTION"], past),  # 4 ended
                ("NEW", "eBay", ["AUCTION"], future),  # 5 not interested
            ],
            start=1,
        ):
            s.add(
                DiscoveredItem(
                    identity=f"w{i}",
                    source=src,
                    ebay_item_id=f"v1|{i}|0",
                    inbox_status=status,
                    normalized={"buying_options": opts, "item_end_time": end, "item_price": "10"},
                    original_data={},
                )
            )
    calls = []

    class FakeAdapter:
        def __init__(self, *a, **k):
            pass

        async def get_item(self, ebay_id):
            calls.append(ebay_id)
            return {"currentBidPrice": {"value": "77.00"}, "bidCount": 9}

        async def close(self):
            pass

    monkeypatch.setenv("EBAY_MODE", "production")
    monkeypatch.setattr(watch, "EbayAdapter", FakeAdapter)
    monkeypatch.setattr(watch.Connection, "from_env", classmethod(lambda cls: object()))
    assert asyncio.run(watch.refresh(sessions)) == 1
    assert calls == ["v1|1|0"]
    with sessions() as s:
        assert s.get(DiscoveredItem, 1).normalized["item_price"] == "77.00"
        assert s.get(DiscoveredItem, 5).normalized["item_price"] == "10"
    engine.dispose()


def test_pickup_lots_allow_for_fee_and_forwarding():
    from decimal import Decimal

    from radar.economics import acquisition_terms, pickup_assumed, walk_away
    from radar.triage import landed_cost

    lot = {"source": "HiBid", "item_price": "100", "buyer_premium_rate": "0.15"}
    assert pickup_assumed(lot) is True
    ship, premium = acquisition_terms(lot)
    assert (ship, premium) == ("12", Decimal("0.20"))  # 15% premium + 5% pickup
    assert landed_cost(lot) == Decimal("132.00")  # 100 * 1.20 + 12
    # a marketplace that quotes shipping is untouched
    ebay = {"source": "eBay", "item_price": "100", "shipping_cost": "8"}
    assert pickup_assumed(ebay) is False and landed_cost(ebay) == Decimal("108.00")
    assert pickup_assumed({"source": "eBay", "item_price": "100"}) is False
    # the max bid drops accordingly
    plain = walk_away(300, None, 0)["bid"]
    assert walk_away(300, *acquisition_terms(lot))["bid"] < plain
