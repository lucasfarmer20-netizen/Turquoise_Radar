"""Category sweep: request shape, storage/dedup against the keyword lane; no network."""

import asyncio
import json
from pathlib import Path

from radar import category_sweep
from radar.db import DiscoveredItem, Setting, database
from radar.discovery.service import _identity


def test_params_target_category_aspect_and_us():
    p = category_sweep.params("Native American", "FIXED_PRICE", "newlyListed", 200)
    assert p["category_ids"] == "262025"
    assert p["aspect_filter"] == "categoryId:262025,Ethnic & Regional Style:{Native American}"
    assert p["filter"] == "buyingOptions:{FIXED_PRICE},itemLocationCountry:US"
    assert p["sort"] == "newlyListed" and p["limit"] == 200


def _fixture_raws():
    data = json.loads(Path("fixtures/ebay/discovery.json").read_text())
    items = data["items"]
    items = list(items.values()) if isinstance(items, dict) else items
    return items[:3]


def test_store_dedups_with_keyword_lane_identity(tmp_path):
    engine, sessions = database(f"sqlite:///{tmp_path / 'c.db'}")
    raws = _fixture_raws()
    new, refreshed, invalid = category_sweep.store(
        sessions, raws, "fixture", "category:Native American"
    )
    assert new == len(raws) and refreshed == 0
    with sessions() as s:
        rows = list(s.query(DiscoveredItem))
        assert all(r.source == "eBay" for r in rows)
        assert rows[0].identity == _identity("eBay", rows[0].ebay_item_id)
        assert rows[0].normalized["raw_search_phrase"] == "category:Native American"
    # same items again -> refreshed, not duplicated
    assert category_sweep.store(sessions, raws, "fixture", "category:Native American")[:2] == (
        0,
        len(raws),
    )
    with sessions() as s:
        assert s.query(DiscoveredItem).count() == len(raws)
    engine.dispose()


def test_run_requires_production_and_records_state(tmp_path, monkeypatch):
    engine, sessions = database(f"sqlite:///{tmp_path / 'c.db'}")
    monkeypatch.setenv("EBAY_MODE", "fixture")
    assert asyncio.run(category_sweep.run(sessions)) is None
    assert asyncio.run(category_sweep.run(sessions, {"styles": [], "limit": 200})) is None

    raws = _fixture_raws()
    calls = []

    class FakeAdapter:
        def __init__(self, *a, **k):
            pass

        async def close(self):
            pass

    async def fake_fetch(adapter, style, buying, sort, limit):
        calls.append((style, buying))
        return raws if buying == "FIXED_PRICE" else []

    monkeypatch.setenv("EBAY_MODE", "production")
    monkeypatch.setattr(category_sweep, "EbayAdapter", FakeAdapter)
    monkeypatch.setattr(category_sweep.Connection, "from_env", classmethod(lambda cls: object()))
    monkeypatch.setattr(category_sweep, "fetch", fake_fetch)
    monkeypatch.setattr(
        category_sweep,
        "normalize",
        lambda raw, mode, q, oq: category_sweep.normalize.__wrapped__(raw, "fixture", q, oq)
        if hasattr(category_sweep.normalize, "__wrapped__")
        else _norm(raw, q),
    )
    stats = asyncio.run(
        category_sweep.run(sessions, {"styles": ["Native American", "Mexican"], "limit": 50})
    )
    assert stats["calls"] == 4 and len(calls) == 4
    assert stats["new"] == len(raws)  # Mexican lane returned the same items -> refreshed
    with sessions() as s:
        assert s.get(Setting, category_sweep.STATE_SETTING_ID).data["stats"]["new"] == len(raws)
    engine.dispose()


def _norm(raw, q):
    from radar.discovery.ebay import normalize

    return normalize(raw, "fixture", q, q)
