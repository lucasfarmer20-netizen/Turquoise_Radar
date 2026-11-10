"""Triage unit tests with a fake model and fake image server; no network."""

import asyncio
from decimal import Decimal
from types import SimpleNamespace

from sqlalchemy import select

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


def test_reconcile_verdict_derives_read_from_numbers():
    n = {"item_price": "1995.00", "shipping_cost": "24.80"}
    d = {"value_read": "UNDERPRICED", "score": 86, "resale_estimate_usd": [1200, 2200]}
    out = triage.reconcile_verdict(d, n)
    assert out["value_read"] == "OVERPRICED" and out["score"] == 45
    assert out["model_value_read"] == "UNDERPRICED" and out["model_score"] == 86
    assert out["landed_usd"] == "2019.80"
    # cheap ring with real headroom stays underpriced
    out = triage.reconcile_verdict(
        {"value_read": "FAIR", "score": 60, "resale_estimate_usd": [60, 120]},
        {"item_price": "20", "shipping_cost": "5.62"},
    )
    assert out["value_read"] == "UNDERPRICED" and out["score"] == 60
    # BIN price is the landed basis when present
    out = triage.reconcile_verdict(
        {"value_read": "UNDERPRICED", "score": 90, "resale_estimate_usd": [100, 150]},
        {"item_price": "39", "buy_it_now_price": "89", "shipping_cost": "0"},
    )
    assert out["value_read"] == "OVERPRICED" and out["score"] == 45  # $89 BIN vs $66-82 walk-away
    # the $55 + $8.95 ring with a $90-150 range: walk-away landed $53.26 < $63.95 -> not underpriced
    out = triage.reconcile_verdict(
        {"value_read": "UNDERPRICED", "score": 85, "resale_estimate_usd": [90, 150]},
        {"item_price": "55", "shipping_cost": "8.95"},
    )
    assert out["value_read"] == "FAIR" and out["score"] == 70
    # no range -> untouched
    assert triage.reconcile_verdict({"value_read": "FAIR", "score": 50}, n)["score"] == 50


def test_reconcile_command_updates_stored_rows(tmp_path):
    engine, sessions = _db(tmp_path)
    with sessions.begin() as s:
        item = s.query(DiscoveredItem).first()
        item.normalized = {**item.normalized, "item_price": "1995", "shipping_cost": "25"}
        s.add(
            TriageResult(
                item_id=item.id,
                model="m",
                status="OK",
                relevant=True,
                score=86,
                category="X",
                data={
                    "value_read": "UNDERPRICED",
                    "score": 86,
                    "resale_estimate_usd": [1200, 2200],
                },
                cost_usd="0",
            )
        )
    changed, total = triage.reconcile(sessions)
    assert (changed, total) == (1, 1)
    with sessions() as s:
        t = s.query(TriageResult).first()
        assert t.score == 45 and t.data["value_read"] == "OVERPRICED"
    # idempotent
    assert triage.reconcile(sessions) == (0, 1)
    engine.dispose()


def test_seed_disables_builtin_groups_and_sizes_budget(tmp_path):
    from radar.db import DiscoveryGroup, Setting
    from radar.discovery.service import DEFAULT_GROUPS, seed_discovery
    from radar.scheduler import seed

    engine, sessions = database(f"sqlite:///{tmp_path / 's.db'}")
    with sessions.begin() as s:
        seed_discovery(s)  # what the web app does on startup
    created, total, limits = seed(sessions, "fixtures/seed_queries.json", 50, 1)
    with sessions() as s:
        builtin = [s.get(DiscoveryGroup, gid) for gid in DEFAULT_GROUPS]
        assert all(g is not None and g.data["enabled"] is False for g in builtin)
        seeded = [g for g in s.scalars(select(DiscoveryGroup)) if g.id.startswith("seed-")]
        assert len(seeded) == created and all(g.data["enabled"] for g in seeded)
        budget = s.get(Setting, 3).data
        assert budget["max_queries"] >= sum(len(g.data["queries"]) for g in seeded)
    # idempotent: re-seeding keeps built-ins disabled and doesn't duplicate seed groups
    created2, _, _ = seed(sessions, "fixtures/seed_queries.json", 50, 1)
    with sessions() as s:
        assert created2 == created
        assert (
            len([g for g in s.scalars(select(DiscoveryGroup)) if g.id.startswith("seed-")])
            == created
        )
    engine.dispose()
