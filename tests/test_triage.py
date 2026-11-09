"""Triage unit tests with a fake model and fake image server; no network."""

import asyncio
from decimal import Decimal
from types import SimpleNamespace

from radar import triage
from radar.db import DiscoveredItem, TriageResult, database


def _db(tmp_path):
    engine, sessions = database(f"sqlite:///{tmp_path / 't.db'}")
    with sessions.begin() as s:
        for i in range(1, 4):
            s.add(
                DiscoveredItem(
                    identity=f"i{i}",
                    source="eBay",
                    ebay_item_id=f"v1|{i}|0",
                    normalized={
                        "raw_title": f"ring {i}",
                        "item_price": "20",
                        "image_urls": [f"https://img.invalid/{i}/s-l1600.jpg"] if i != 3 else [],
                    },
                    original_data={},
                )
            )
    return engine, sessions


def test_thumb_url_rewrites_size_suffix():
    assert triage.thumb_url("https://i.ebayimg.com/images/g/abc/s-l1600.jpg").endswith("s-l500.jpg")
    assert triage.thumb_url("https://x/y.png") == "https://x/y.png"


def test_cost_matches_published_prices():
    c = triage.cost_usd("claude-haiku-5-5", {"input_tokens": 1_000_000, "output_tokens": 1_000_000})
    assert c == Decimal("0.60")


def test_run_triage_stores_verdicts_and_skips_missing_images(tmp_path, monkeypatch):
    engine, sessions = _db(tmp_path)
    calls = []

    async def fake_fetch(client, url):
        return "image/jpeg", b"\xff\xd8fake"

    async def fake_screen(anthropic, http, model, item):
        if not (item.normalized.get("image_urls")):
            return dict(status="SKIPPED", data={"error": "no image"}, usage={})
        calls.append(item.id)
        return dict(
            status="OK",
            data={
                "relevant": item.id == 1,
                "score": 90 if item.id == 1 else 10,
                "category": "SOUTHWEST_STYLE",
            },
            usage={"input_tokens": 700, "output_tokens": 80},
        )

    monkeypatch.setattr(triage, "fetch_image", fake_fetch)
    monkeypatch.setattr(triage, "screen_one", fake_screen)
    monkeypatch.setattr("anthropic.AsyncAnthropic", lambda **kw: SimpleNamespace(), raising=False)
    cfg = dict(
        api_key="k",
        model="claude-haiku-5-5",
        daily_budget=Decimal("1"),
        enabled=True,
        concurrency=2,
    )
    n = asyncio.run(triage.run_triage(sessions, cfg))
    assert n == 3 and sorted(calls) == [1, 2]
    with sessions() as s:
        rows = {r.item_id: r for r in s.query(TriageResult)}
    assert rows[1].relevant is True and rows[1].score == 90
    assert rows[2].relevant is False
    assert rows[3].status == "SKIPPED" and rows[3].relevant is None
    assert Decimal(rows[1].cost_usd) == Decimal("0.00011")
    # second pass: nothing left
    assert asyncio.run(triage.run_triage(sessions, cfg)) == 0
    engine.dispose()


def test_budget_stops_pass(tmp_path, monkeypatch):
    engine, sessions = _db(tmp_path)

    async def fake_screen(anthropic, http, model, item):
        return dict(
            status="OK",
            data={"relevant": True, "score": 50, "category": "UNCLEAR"},
            usage={"input_tokens": 5_000_000, "output_tokens": 0},
        )

    monkeypatch.setattr(triage, "screen_one", fake_screen)
    monkeypatch.setattr("anthropic.AsyncAnthropic", lambda **kw: SimpleNamespace(), raising=False)
    cfg = dict(
        api_key="k",
        model="claude-haiku-5-5",
        daily_budget=Decimal("0.40"),
        enabled=True,
        concurrency=1,
    )
    n = asyncio.run(triage.run_triage(sessions, cfg))
    assert n == 1  # first item costs $0.50 and trips the $0.40 cap
    assert asyncio.run(triage.run_triage(sessions, cfg)) == 0
    engine.dispose()


def test_no_key_is_a_noop(tmp_path):
    engine, sessions = _db(tmp_path)
    cfg = dict(
        api_key="", model="claude-haiku-5-5", daily_budget=Decimal("1"), enabled=True, concurrency=1
    )
    assert asyncio.run(triage.run_triage(sessions, cfg)) == 0
    engine.dispose()


def test_errored_items_are_retried(tmp_path, monkeypatch):
    engine, sessions = _db(tmp_path)
    attempts = {"n": 0}

    async def flaky(anthropic, http, model, item):
        attempts["n"] += 1
        if attempts["n"] <= 3:
            return dict(status="ERROR", data={"error": "boom"}, usage={})
        return dict(
            status="OK",
            data={"relevant": True, "score": 60, "category": "UNCLEAR"},
            usage={"input_tokens": 1, "output_tokens": 1},
        )

    monkeypatch.setattr(triage, "screen_one", flaky)
    monkeypatch.setattr("anthropic.AsyncAnthropic", lambda **kw: SimpleNamespace(), raising=False)
    cfg = dict(
        api_key="k",
        model="claude-haiku-5-5",
        daily_budget=Decimal("1"),
        enabled=True,
        concurrency=1,
    )
    asyncio.run(triage.run_triage(sessions, cfg))  # all three error
    with sessions() as s:
        assert {r.status for r in s.query(TriageResult)} == {"ERROR"}
    asyncio.run(triage.run_triage(sessions, cfg))  # retried, now OK
    with sessions() as s:
        assert {r.status for r in s.query(TriageResult)} == {"OK"}
    engine.dispose()
