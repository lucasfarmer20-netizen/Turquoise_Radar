"""Shop feeds, foreign eBay marketplaces and the sourcing calendar. No network."""

import asyncio
import json
from datetime import date
from decimal import Decimal

from radar import feeds, foreign, sourcing_calendar
from radar.db import DiscoveredItem, database
from radar.economics import acquisition_terms, pickup_assumed
from radar.schemas import ListingInput

FEED = """<?xml version="1.0" encoding="UTF-8"?>
<feed xml:lang="en" xmlns="http://www.w3.org/2005/Atom"
      xmlns:s="http://jadedpixel.com/-/spec/shopify">
  <id>https://post.invalid/collections/all.atom</id>
  <title>SYNTHETIC Trading Co</title>
  <entry>
    <id>https://post.invalid/products/111</id>
    <published>2026-11-12T16:31:47-07:00</published>
    <link rel="alternate" type="text/html" href="https://post.invalid/products/cuff-111"/>
    <title>SYNTHETIC cluster cuff</title>
    <s:type>Bracelets</s:type>
    <s:vendor>SYNTHETIC Trading Co</s:vendor>
    <summary type="html"><![CDATA[<table><tr><td>
      <img width="200" src="https://cdn.post.invalid/a.jpg?v=1"></td>
      <td><p><strong>Price: </strong>450.00</p></td></tr>
      <tr><td><p>Heavy sterling cuff &amp; stones.</p></td></tr></table>]]></summary>
    <s:tag>artist_doe-jane</s:tag>
    <s:tag>tribal-affiliation_Navajo</s:tag>
    <s:variant><title>Default</title><s:price currency="USD">450.00</s:price></s:variant>
  </entry>
  <entry>
    <id>https://post.invalid/products/222</id>
    <link rel="alternate" type="text/html" href="https://post.invalid/products/rug-222"/>
    <title>SYNTHETIC rug</title>
    <s:type>Rugs &amp; Weavings</s:type>
    <s:variant><s:price currency="USD">1112.00</s:price></s:variant>
  </entry>
  <entry>
    <id>https://post.invalid/products/333</id>
    <link rel="alternate" type="text/html" href="https://elsewhere.invalid/products/ring"/>
    <title>SYNTHETIC ring on another host</title>
    <s:type>Rings</s:type>
    <s:variant><s:price currency="USD">50.00</s:price></s:variant>
  </entry>
</feed>"""


def test_shop_feed_keeps_jewelry_with_seller_tags():
    items = feeds.parse(FEED, "https://post.invalid", feeds.settings()["types"])
    assert [i.raw_title for i in items] == ["SYNTHETIC cluster cuff"]  # rug and off-host dropped
    cuff = items[0]
    assert cuff.item_price == Decimal("450.00") and cuff.currency == "USD"
    assert cuff.source == "Shop: SYNTHETIC Trading Co"
    assert cuff.source_listing_id == "post.invalid-111"
    assert [str(u) for u in cuff.image_urls] == ["https://cdn.post.invalid/a.jpg?v=1"]
    assert "tribal affiliation: Navajo" in cuff.raw_description
    assert "artist: doe jane" in cuff.style_description
    # a shop quotes its own shipping at checkout: not treated as a pickup lot
    assert pickup_assumed(cuff.model_dump(mode="json")) is False


def test_shop_feed_rejects_entity_declarations():
    import pytest

    with pytest.raises(ValueError):
        feeds.parse('<!DOCTYPE x [<!ENTITY a "b">]><feed/>', "https://post.invalid")


def test_ecb_rates_convert_to_usd_per_unit():
    xml = "<Cube><Cube currency='USD' rate='1.1000'/><Cube currency='GBP' rate='0.8800'/></Cube>"
    fx = foreign.parse_ecb(xml)
    assert fx["EUR"] == Decimal("1.1000") and fx["GBP"] == Decimal("1.25")
    assert foreign.parse_ecb("<Cube/>") == {}


def _listing(**over):
    base = dict(
        source="eBay",
        source_listing_id="v1|900|0",
        listing_url="https://www.ebay.de/itm/900",
        raw_title="SYNTHETIC Navajo Silber Armreif",
        item_price="100.00",
        currency="EUR",
        shipping_cost="20.00",
        buying_options=["FIXED_PRICE"],
        discovery_mode="production",
    )
    return ListingInput(**{**base, **over})


def test_foreign_listing_is_stored_in_usd_with_import_allowance(tmp_path):
    fx = {"EUR": Decimal("1.10")}
    record = foreign.convert(_listing(), "EBAY_DE", fx, "0.15")
    assert record["item_price"] == "110.00" and record["shipping_cost"] == "22.00"
    assert record["currency"] == "USD" and record["original_price"] == "100.00 EUR"
    ship, premium = acquisition_terms(record)
    assert ship == "22.00" and premium == Decimal("0.15")
    # no rate for the currency: skipped, never guessed
    assert foreign.convert(_listing(currency="CHF"), "EBAY_DE", fx, "0.15") is None
    engine, sessions = database(f"sqlite:///{tmp_path / 'f.db'}")
    assert foreign.store(sessions, [record]) == (1, 0)
    cheaper = {**record, "item_price": "88.00"}
    assert foreign.store(sessions, [cheaper]) == (0, 1)
    with sessions() as s:
        n = s.query(DiscoveredItem).one().normalized
    assert n["item_price"] == "88.00" and n["marketplace"] == "EBAY_DE"
    engine.dispose()


def test_foreign_lane_is_off_outside_production(tmp_path, monkeypatch):
    monkeypatch.setenv("EBAY_MODE", "fixture")
    engine, sessions = database(f"sqlite:///{tmp_path / 'f.db'}")
    assert asyncio.run(foreign.run(sessions)) is None
    monkeypatch.setenv("RADAR_FOREIGN_MARKETS", "")
    assert foreign.settings()["markets"] == []
    engine.dispose()


def test_calendar_lists_upcoming_and_reminds_once(tmp_path, monkeypatch):
    cal = tmp_path / "cal.json"
    cal.write_text(
        json.dumps(
            {
                "events": [
                    {
                        "id": "a",
                        "name": "SYNTHETIC sale A",
                        "url": "https://a.invalid",
                        "when": "Nov 10-16",
                        "ends": "2026-11-16",
                        "remind_on": "2026-11-14",
                    },
                    {
                        "id": "b",
                        "name": "SYNTHETIC sale B",
                        "ends": "2027-03-01",
                        "remind_on": "2027-01-10",
                    },
                    {
                        "id": "old",
                        "name": "SYNTHETIC past",
                        "ends": "2026-01-01",
                        "remind_on": "2025-12-01",
                    },
                    {"id": "far", "name": "SYNTHETIC far", "remind_on": "2028-01-01"},
                    {"name": "no id"},
                ]
            }
        )
    )
    monkeypatch.setenv("RADAR_CALENDAR_FILE", str(cal))
    today = date(2026, 11, 14)
    up = sourcing_calendar.upcoming(today)
    assert [e["id"] for e in up] == ["a", "b"] and up[0]["live"] and not up[1]["live"]
    engine, sessions = database(f"sqlite:///{tmp_path / 'c.db'}")
    assert [e["id"] for e in sourcing_calendar.due(sessions, today)] == ["a"]
    assert "<https://a.invalid|SYNTHETIC sale A>" in sourcing_calendar.message(up[:1])
    sourcing_calendar.mark_sent(sessions, ["a"])
    assert sourcing_calendar.due(sessions, today) == []
    engine.dispose()


def test_shipped_calendar_file_is_well_formed():
    events = sourcing_calendar.events()
    assert len(events) >= 5 and all(e["remind_on"] and e["url"] for e in events)


def test_foreign_run_fetches_each_market_and_counts_calls(tmp_path, monkeypatch):
    engine, sessions = database(f"sqlite:///{tmp_path / 'r.db'}")
    asked = []

    class FakeAdapter:
        def __init__(self, *a, **kw):
            pass

        async def close(self):
            pass

    async def fake_fetch(adapter, market, query, limit):
        asked.append((market, query))
        currency = "EUR" if market == "EBAY_DE" else "GBP"
        return [
            {
                "itemId": f"v1|{len(asked)}|0",
                "title": f"SYNTHETIC {query}",
                "buyingOptions": ["FIXED_PRICE"],
                "price": {"value": "50.00", "currency": currency},
                "itemWebUrl": f"https://ebay.invalid/{len(asked)}",
            }
        ]

    async def fake_rates(client=None):
        return {"EUR": Decimal("1.10")}  # no GBP rate: UK listings are skipped

    monkeypatch.setenv("EBAY_MODE", "production")
    monkeypatch.setattr(foreign, "EbayAdapter", FakeAdapter)
    monkeypatch.setattr(foreign.Connection, "from_env", classmethod(lambda cls: None))
    monkeypatch.setattr(foreign, "fetch", fake_fetch)
    monkeypatch.setattr(foreign, "rates", fake_rates)
    cfg = dict(
        markets=["EBAY_DE", "EBAY_GB"],
        queries={"EBAY_DE": ["Indianerschmuck"], "EBAY_GB": ["Navajo silver"]},
        import_rate="0.15",
        limit=50,
    )
    stats = asyncio.run(foreign.run(sessions, cfg))
    assert asked == [("EBAY_DE", "Indianerschmuck"), ("EBAY_GB", "Navajo silver")]
    assert (stats["calls"], stats["new"], stats["skipped"]) == (2, 1, 1)
    with sessions() as s:
        n = s.query(DiscoveredItem).one().normalized
    assert n["item_price"] == "55.00" and n["original_price"] == "50.00 EUR"
    engine.dispose()
