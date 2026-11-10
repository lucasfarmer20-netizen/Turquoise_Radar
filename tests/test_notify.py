"""Notifier tests with a fake delivery; no network."""

import asyncio
from datetime import UTC, datetime, timedelta

from radar import notify
from radar.db import DiscoveredItem, TriageResult, database


def _db(tmp_path):
    engine, sessions = database(f"sqlite:///{tmp_path / 'n.db'}")
    now = datetime.now(UTC)
    rows = [
        (88, "UNDERPRICED", "NEW", ["FIXED_PRICE"], None),  # 1: notify
        (90, "FAIR", "NEW", ["FIXED_PRICE"], None),  # 2: fair -> no
        (60, "UNDERPRICED", "NEW", ["FIXED_PRICE"], None),  # 3: low score -> no
        (80, "UNDERPRICED", "IRRELEVANT", ["FIXED_PRICE"], None),  # 4: passed -> no
        (50, "FAIR", "INTERESTED", ["AUCTION"], now + timedelta(minutes=50)),  # 5: ending
        (50, "FAIR", "INTERESTED", ["AUCTION"], now + timedelta(hours=5)),  # 6: too far
    ]
    with sessions.begin() as s:
        for i, (_score, _read, status, opts, end) in enumerate(rows, start=1):
            s.add(
                DiscoveredItem(
                    identity=f"i{i}",
                    source="eBay",
                    ebay_item_id=f"v1|{i}|0",
                    inbox_status=status,
                    normalized={
                        "raw_title": f"cuff {i}",
                        "item_price": "40",
                        "shipping_cost": "5",
                        "buying_options": opts,
                        "listing_url": f"https://ebay.invalid/{i}",
                        "item_end_time": end.isoformat() if end else None,
                    },
                    original_data={},
                )
            )
        s.flush()
        for i, (score, read, _status, _opts, _end) in enumerate(rows[:4], start=1):
            s.add(
                TriageResult(
                    item_id=i,
                    model="m",
                    status="OK",
                    relevant=True,
                    score=score,
                    category="NATIVE_AMERICAN_STYLE",
                    data={
                        "value_read": read,
                        "score": score,
                        "resale_estimate_usd": [90, 150],
                        "reason": f"r{i}",
                        "landed_usd": "45",
                    },
                    cost_usd="0",
                )
            )
    return engine, sessions


CFG = dict(
    slack="https://hooks.invalid",
    ntfy_topic="",
    ntfy_server="",
    min_score=75,
    ending_hours=2,
    base_url="http://radar:8000",
)


def test_digest_selects_and_marks(tmp_path, monkeypatch):
    engine, sessions = _db(tmp_path)
    sent = []

    async def fake_deliver(cfg, slack_text, plain_text, title, click=None, priority="default"):
        sent.append((slack_text, plain_text, title, priority))
        return ["slack"]

    monkeypatch.setattr(notify, "deliver", fake_deliver)
    n = asyncio.run(notify.notify_pending(sessions, CFG))
    assert n == 2 and len(sent) == 1
    slack_text, plain_text, title, priority = sent[0]
    for present in ("cuff 1", "cuff 5"):
        assert present in slack_text
    for absent in ("cuff 2", "cuff 3", "cuff 4", "cuff 6"):
        assert absent not in slack_text
    assert "ends in 49m" in slack_text or "ends in 50m" in slack_text
    assert "<https://ebay.invalid/1|cuff 1>" in slack_text
    assert "<https://ebay.invalid/1|" not in plain_text
    assert title == "Radar: 1 find, 1 ending soon" and priority == "high"
    # second pass: nothing pending
    assert asyncio.run(notify.notify_pending(sessions, CFG)) == 0 and len(sent) == 1
    with sessions() as s:
        assert s.get(TriageResult, 1).data["notified_at"]
        assert s.get(DiscoveredItem, 5).normalized["ending_notified_at"]
    engine.dispose()


def test_failed_delivery_does_not_mark(tmp_path, monkeypatch):
    engine, sessions = _db(tmp_path)

    async def fail(*a, **k):
        return []

    monkeypatch.setattr(notify, "deliver", fail)
    assert asyncio.run(notify.notify_pending(sessions, CFG)) == 0
    with sessions() as s:
        assert "notified_at" not in s.get(TriageResult, 1).data
    engine.dispose()


def test_unconfigured_is_noop(tmp_path):
    engine, sessions = _db(tmp_path)
    assert asyncio.run(notify.notify_pending(sessions, {**CFG, "slack": ""})) == 0
    engine.dispose()
