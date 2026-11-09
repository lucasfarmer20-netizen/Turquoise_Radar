"""Offline-only auction option; all source data and credentials are synthetic."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest
from pydantic import ValidationError
from test_compact_feed_routes import photo
from test_discovery import connection, no_wait, raw_item
from test_search_evaluation import record

from radar.analysis import analyze
from radar.discovery.config import SearchOptions
from radar.discovery.ebay import EbayAdapter, FixtureDiscoveryAdapter, normalize
from radar.schemas import Shipping


def test_ending_soon_requires_explicit_auction():
    with pytest.raises(ValidationError):
        SearchOptions(sort="endingSoonest")
    opts = SearchOptions(buying_format="AUCTION", sort="endingSoonest")
    assert opts.params("  SYNTHETIC ring  ", 3) == {
        "q": "  SYNTHETIC ring  ",
        "limit": 3,
        "offset": 0,
        "sort": "endingSoonest",
        "filter": "buyingOptions:{AUCTION},itemLocationCountry:US",
    }


def test_real_adapter_wire_is_bounded_and_fields_remain_factual():
    calls = []
    raw = raw_item("6")
    raw.update(
        buyingOptions=["AUCTION", "FIXED_PRICE"],
        currentBidPrice={"value": "12.50", "currency": "USD"},
        price={"value": "90.00", "currency": "USD"},
    )

    def route(request):
        calls.append(request)
        if request.url.path.endswith("/token"):
            return httpx.Response(200, json={"access_token": "SYNTHETIC-token", "expires_in": 3600})
        assert request.url.params["sort"] == "endingSoonest"
        assert request.url.params["filter"] == "buyingOptions:{AUCTION},itemLocationCountry:US"
        assert request.url.params["limit"] == "3"
        assert "99669" in request.headers["X-EBAY-C-ENDUSERCTX"]
        return httpx.Response(200, json={"itemSummaries": [raw], "total": 1})

    async def check():
        async with httpx.AsyncClient(transport=httpx.MockTransport(route)) as client:
            adapter = EbayAdapter(connection(retry_limit=0), client=client, sleep=no_wait)
            result = await adapter.search(
                "SYNTHETIC ring",
                SearchOptions(buying_format="AUCTION", sort="endingSoonest"),
                3,
                page_limit=1,
                page_size=3,
            )
            assert not result.errors
            item = normalize(result.pages[0].items[0], "sandbox", "q", "q")
            assert item.item_price == Decimal("12.50")
            assert item.item_end_time is not None and item.image_urls
            assert item.shipping_status == "UNKNOWN"
            raw.pop("currentBidPrice")
            assert normalize(raw, "sandbox", "q", "q").item_price is None

    asyncio.run(check())
    assert len(calls) == 2  # Mock OAuth + one search; no detail or image request.


def test_fixture_sorts_known_end_times_and_honors_page_cap(tmp_path):
    first, second = raw_item("6"), raw_item("6")
    first["itemEndDate"] = "2026-12-01T10:00:00Z"
    second["itemEndDate"] = "2026-12-01T09:00:00Z"
    second["itemId"] = "SYNTHETIC-EARLIER"
    path = tmp_path / "fixture.json"
    path.write_text(
        json.dumps({"queries": {"q": {"ids": ["a", "b"]}}, "items": {"a": first, "b": second}})
    )
    result = asyncio.run(
        FixtureDiscoveryAdapter(path).search(
            "q",
            SearchOptions(buying_format="AUCTION", sort="endingSoonest"),
            1,
            page_limit=1,
            page_size=1,
        )
    )
    assert len(result.pages) == 1 and result.pages[0].items[0]["itemId"] == "SYNTHETIC-EARLIER"


def test_buyer_analysis_keeps_existing_evidence_rules(item, prefs):
    # Source identity does not create a blanket ban or manufacture financial evidence.
    original = analyze(item, prefs)
    buyer = analyze(item.model_copy(update={
        "source": "eBay", "synthetic": False, "discovery_mode": "production"
    }), prefs)
    assert buyer.model_dump() == original.model_dump()


def test_auction_card_uses_existing_local_photo_and_fact_fields(
    client, item, tmp_path, monkeypatch
):
    root = tmp_path / "photos"
    root.mkdir()
    monkeypatch.setenv("VISION_IMAGE_DIR", str(root))
    monkeypatch.delenv("RADAR_EMAIL_REVIEW_DB", raising=False)
    values = item.model_copy(
        update={
            "raw_title": "SYNTHETIC ending auction",
            "buying_options": ["AUCTION"],
            "item_price": Decimal("12.50"),
            "shipping_cost": Decimal("7.00"),
            "shipping_status": Shipping.UNKNOWN,
            "item_end_time": datetime.now(UTC) + timedelta(hours=1),
        }
    )
    with client.app.state.sessions.begin() as session:
        owner, _, _ = record(session, values, "SYNTHETIC-auction", query="SYNTHETIC ring")
        owner_id = owner.id
    photo_id = photo(client, root, owner_id)
    response = client.get("/opportunities")
    assert response.status_code == 200
    for text in (
        "Last observed bid",
        "12.50 USD",
        "Observed shipping quote",
        "Recorded close in",
        "NEEDS INFO",
        "/feed/photos/" + photo_id,
    ):
        assert text in response.text
    assert client.get("/feed/photos/" + photo_id).status_code == 200
