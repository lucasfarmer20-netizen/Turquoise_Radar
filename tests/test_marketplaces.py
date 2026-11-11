"""Etsy/Bonanza adapters and the shared upsert, with recorded-shape payloads; no network."""

import asyncio
from decimal import Decimal

from radar import marketplaces
from radar.db import DiscoveredItem, Setting, database
from radar.marketplaces import bonanza, etsy

ETSY_RAW = {
    "listing_id": 1234567890,
    "shop_id": 55,
    "title": "Vintage Navajo Sterling Turquoise Cuff",
    "description": "Old pawn style cuff",
    "state": "active",
    "original_creation_timestamp": 1762700000,
    "url": "https://www.etsy.com/listing/1234567890/vintage-navajo-cuff",
    "price": {"amount": 18500, "divisor": 100, "currency_code": "USD"},
    "item_weight": 2.5,
    "item_weight_unit": "oz",
    "tags": ["navajo", "turquoise", "sterling"],
}
BONANZA_RAW = {
    "itemId": "987654321",
    "title": "Taxco Sterling Modernist Cuff",
    "viewItemURL": "https://www.bonanza.com/listings/987654321",
    "galleryURL": "https://images.bonanzastatic.com/x.jpg",
    "sellingStatus": {"currentPrice": {"value": "64.00", "currencyId": "USD"}},
    "shippingInfo": {
        "shippingServiceCost": {"value": "6.50", "currencyId": "USD"},
        "shippingType": "Flat",
    },
}


def test_etsy_to_listing_maps_price_weight_and_images():
    item = etsy.to_listing(ETSY_RAW, ["https://i.etsystatic.com/a/il_570xN.jpg"], "navajo cuff")
    assert item.source == "Etsy" and item.source_listing_id == "1234567890"
    assert item.item_price == Decimal("185.00")
    assert item.stated_weight == Decimal("70.87") and item.weight_unit == "g"
    assert str(item.image_urls[0]).endswith("il_570xN.jpg")
    assert item.raw_search_phrase == "navajo cuff" and item.buying_options == ["FIXED_PRICE"]
    assert item.listing_time is not None


def test_bonanza_to_listing_maps_price_shipping_and_image():
    item = bonanza.to_listing(BONANZA_RAW, "taxco")
    assert item.source == "Bonanza" and item.source_listing_id == "987654321"
    assert item.item_price == Decimal("64.00") and item.shipping_cost == Decimal("6.50")
    assert len(item.image_urls) == 1
    free = bonanza.to_listing({**BONANZA_RAW, "shippingInfo": {"shippingType": "Free"}}, "taxco")
    assert free.shipping_cost == Decimal(0)


def test_store_upserts_by_identity(tmp_path):
    engine, sessions = database(f"sqlite:///{tmp_path / 'm.db'}")
    a = etsy.to_listing(ETSY_RAW, [], "navajo cuff")
    assert marketplaces.store(sessions, [a]) == (1, 0)
    b = etsy.to_listing(
        {**ETSY_RAW, "price": {"amount": 17000, "divisor": 100, "currency_code": "USD"}},
        [],
        "zuni inlay",
    )
    assert marketplaces.store(sessions, [b]) == (0, 1)
    with sessions() as s:
        rows = list(s.query(DiscoveredItem))
        assert len(rows) == 1
        assert rows[0].source == "Etsy" and rows[0].ebay_item_id == "1234567890"
        assert Decimal(rows[0].normalized["item_price"]) == Decimal("170")  # refreshed
        assert rows[0].normalized["raw_search_phrase"] == "navajo cuff"  # first finder kept
    engine.dispose()


def test_run_marketplaces_uses_configured_adapters_and_records_state(tmp_path, monkeypatch):
    engine, sessions = database(f"sqlite:///{tmp_path / 'm.db'}")
    calls = []

    async def fake_etsy(q):
        calls.append(q)
        return [etsy.to_listing({**ETSY_RAW, "listing_id": 100 + len(calls)}, [], q)], 2

    monkeypatch.setattr(etsy, "configured", lambda: True)
    monkeypatch.setattr(etsy, "search", fake_etsy)
    monkeypatch.setattr(etsy, "PAUSE_SECONDS", 0)
    monkeypatch.setattr(bonanza, "configured", lambda: False)
    report = asyncio.run(marketplaces.run_marketplaces(sessions, queries=["a", "b", "c"]))
    assert list(report) == ["Etsy"]
    assert report["Etsy"]["new"] == 3 and report["Etsy"]["calls"] == 6 and calls == ["a", "b", "c"]
    with sessions() as s:
        assert s.get(Setting, marketplaces.STATE_SETTING_ID).data["report"]["Etsy"]["new"] == 3
    engine.dispose()


def test_not_ready_stops_that_adapter_only(tmp_path, monkeypatch):
    engine, sessions = database(f"sqlite:///{tmp_path / 'm.db'}")

    async def pending(q):
        raise etsy.NotReady("HTTP 403: app pending approval")

    async def ok(q):
        return [bonanza.to_listing(BONANZA_RAW, q)], 1

    for mod, fn in ((etsy, pending), (bonanza, ok)):
        monkeypatch.setattr(mod, "configured", lambda: True)
        monkeypatch.setattr(mod, "search", fn)
        monkeypatch.setattr(mod, "PAUSE_SECONDS", 0)
    monkeypatch.setenv("BONANZA_QUERIES", "all")
    report = asyncio.run(marketplaces.run_marketplaces(sessions, queries=["a", "b"]))
    assert report["Etsy"]["errors"] == 1 and report["Etsy"]["queries"] == 0
    assert report["Bonanza"]["new"] == 1 and report["Bonanza"]["queries"] == 2
    engine.dispose()


def test_seed_queries_are_deduped():
    qs = marketplaces.seed_queries()
    assert len(qs) == len({q.casefold() for q in qs}) and "Old Pawn" in qs


def test_etsy_key_header_pairs_keystring_with_shared_secret(monkeypatch):
    monkeypatch.setenv("ETSY_API_KEY", "abc")
    monkeypatch.delenv("ETSY_SHARED_SECRET", raising=False)
    assert etsy.api_key_header() == "abc"
    monkeypatch.setenv("ETSY_SHARED_SECRET", "xyz")
    assert etsy.api_key_header() == "abc:xyz"
    monkeypatch.setenv("ETSY_API_KEY", "abc:already")
    assert etsy.api_key_header() == "abc:already"


def test_queries_for_uses_override_then_adapter_default(monkeypatch):
    from radar import marketplaces

    seeds = ["a", "b", "c"]
    monkeypatch.delenv("BONANZA_QUERIES", raising=False)
    monkeypatch.delenv("ETSY_QUERIES", raising=False)
    assert marketplaces.queries_for(bonanza, seeds) == bonanza.DEFAULT_QUERIES
    assert marketplaces.queries_for(etsy, seeds) == seeds
    monkeypatch.setenv("BONANZA_QUERIES", "old pawn, zuni inlay ,")
    assert marketplaces.queries_for(bonanza, seeds) == ["old pawn", "zuni inlay"]
    monkeypatch.setenv("BONANZA_QUERIES", "all")
    assert marketplaces.queries_for(bonanza, seeds) == seeds
