"""Synthetic rows only. /radar reads DiscoveredItem directly and stores owner decisions."""

from datetime import UTC, datetime, timedelta

from radar.db import DiscoveredItem


def _add(client, item_id, **over):
    now = datetime.now(UTC)
    normalized = {
        "raw_title": f"SYNTHETIC navaho cuff {item_id}",
        "listing_url": f"https://example.invalid/{item_id}",
        "item_price": "42.00",
        "shipping_cost": "9.50",
        "shipping_status": "UNKNOWN",
        "buying_options": ["AUCTION"],
        "item_end_time": (now + timedelta(hours=3)).isoformat(),
        "image_urls": ["https://example.invalid/img.jpg"],
        "raw_search_phrase": "Navaho Jewelry",
        "synthetic": True,
        "discovery_mode": "fixture",
    } | over
    with client.app.state.sessions.begin() as s:
        s.add(
            DiscoveredItem(
                identity=f"id-{item_id}",
                source="eBay FIXTURE",
                ebay_item_id=f"v1|{item_id}|0",
                normalized=normalized,
                original_data={},
            )
        )


def test_empty_page_renders(client):
    r = client.get("/radar")
    assert r.status_code == 200
    assert "Radar finds" in r.text
    assert "scheduler run --once" in r.text


def test_cards_show_price_landed_and_ending(client):
    _add(client, 1)
    _add(client, 2, buying_options=["FIXED_PRICE"], item_price="18", shipping_cost=None)
    r = client.get("/radar")
    assert r.status_code == 200
    assert "navaho cuff 1" in r.text and "navaho cuff 2" in r.text
    assert "$51.50" in r.text  # landed = 42 + 9.50
    assert "ENDS IN 2H" in r.text or "ENDS IN 3H" in r.text
    assert "Buy It Now" in r.text and "Auction" in r.text
    # filters
    assert "navaho cuff 2" not in client.get("/radar?kind=auction").text
    assert "navaho cuff 1" not in client.get("/radar?kind=bin").text


def test_decisions_persist_and_filter(client):
    _add(client, 7)
    r = client.post(
        "/radar/1/decision",
        data={"decision": "interested", "back": "/radar?status=new"},
        follow_redirects=False,
    )
    assert r.status_code == 303 and r.headers["location"].startswith("/radar")
    with client.app.state.sessions() as s:
        assert s.get(DiscoveredItem, 1).inbox_status == "INTERESTED"
    assert "navaho cuff 7" not in client.get("/radar?status=new").text
    assert "navaho cuff 7" in client.get("/radar?status=interested").text
    client.post("/radar/1/decision", data={"decision": "pass"})
    assert "navaho cuff 7" in client.get("/radar?status=passed").text
    assert client.post("/radar/1/decision", data={"decision": "buy"}).status_code == 400
    assert client.post("/radar/999/decision", data={"decision": "pass"}).status_code == 404


def test_external_back_url_is_not_followed(client):
    _add(client, 3)
    r = client.post(
        "/radar/1/decision",
        data={"decision": "pass", "back": "https://evil.invalid/"},
        follow_redirects=False,
    )
    assert r.headers["location"] == "/radar"
