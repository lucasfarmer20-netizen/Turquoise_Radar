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
    r = client.get("/radar?show=relevant")
    assert r.status_code == 200
    assert "navaho cuff 1" in r.text and "navaho cuff 2" in r.text
    assert "$51.50" in r.text  # landed = 42 + 9.50
    assert "ENDS IN 2H" in r.text or "ENDS IN 3H" in r.text
    assert "Buy It Now" in r.text and "Auction" in r.text
    # filters
    assert "navaho cuff 2" not in client.get("/radar?show=relevant&kind=auction").text
    assert "navaho cuff 1" not in client.get("/radar?show=relevant&kind=bin").text


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
    assert "navaho cuff 7" not in client.get("/radar?show=relevant&status=new").text
    assert "navaho cuff 7" in client.get("/radar?show=relevant&status=interested").text
    client.post("/radar/1/decision", data={"decision": "pass"})
    assert "navaho cuff 7" in client.get("/radar?show=relevant&status=passed").text
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


def _triage(client, item_id, relevant, score, category="NATIVE_AMERICAN_STYLE"):
    from radar.db import TriageResult

    with client.app.state.sessions.begin() as s:
        s.add(
            TriageResult(
                item_id=item_id,
                model="test",
                status="OK",
                relevant=relevant,
                score=score,
                category=category,
                data={
                    "relevant": relevant,
                    "score": score,
                    "category": category,
                    "piece_type": "cuff",
                    "quality_signals": ["stampwork visible"],
                    "concerns": [],
                    "reason": f"synthetic reason {item_id}",
                },
                cost_usd="0.0001",
            )
        )


def test_screening_filters_and_score_sort(client):
    _add(client, 11)
    _add(client, 12)
    _add(client, 13)  # unscreened
    _triage(client, 1, True, 82)
    _triage(client, 2, False, 15, "COSTUME_OR_PLATED")
    default = client.get("/radar?show=relevant").text
    assert "navaho cuff 11" in default and "navaho cuff 13" in default
    assert "navaho cuff 12" not in default  # screened out hidden by default
    assert default.index("navaho cuff 11") < default.index("navaho cuff 13")  # score sort
    assert "synthetic reason 1" in default and "stampwork visible" in default
    assert "navaho cuff 12" in client.get("/radar?show=irrelevant").text
    assert "navaho cuff 11" not in client.get("/radar?show=untriaged").text
    assert "navaho cuff 13" not in client.get("/radar?show=relevant&min_score=60").text
    assert "navaho cuff 11" in client.get("/radar?show=relevant&min_score=60").text


def test_auction_with_bin_shows_both_prices(client):
    _add(
        client,
        21,
        buying_options=["FIXED_PRICE", "AUCTION"],
        item_price="39.00",
        buy_it_now_price="89.00",
        bid_count=0,
    )
    html = client.get("/radar?show=relevant").text
    assert "Auction + BIN" in html
    assert "Starting bid" in html and "$39.00" in html
    assert "Buy It Now" in html and "$89.00" in html


def test_auction_with_bids_shows_bid_count(client):
    _add(client, 22, item_price="52.00", bid_count=3)
    html = client.get("/radar?show=relevant").text
    assert "Current bid · 3 bids" in html


def test_scan_button_writes_trigger_and_reflects_state(client, tmp_path, monkeypatch):
    from radar import scheduler

    monkeypatch.setenv("RADAR_DATABASE_URL", f"sqlite:///{tmp_path / 'x.db'}")
    # no state file -> scheduler shown as not running, button disabled
    html = client.get("/radar?show=relevant").text
    assert "scheduler not running" in html
    # live state -> button enabled with next-cycle hint
    scheduler.write_state(running=False, next_at="2999-01-01T00:00:00+00:00", interval_hours=1)
    html = client.get("/radar?show=relevant").text
    assert ">Scan now</button>" in html and "next scheduled" in html
    # click -> trigger file exists, page says requested
    r = client.post("/radar/scan", data={"back": "/radar?status=new"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/radar?status=new"
    assert scheduler.trigger_path().exists()
    assert "Scan requested" in client.get("/radar?show=relevant").text
    # running state wins
    scheduler.write_state(running=True, reason="requested")
    assert "Scanning…" in client.get("/radar?show=relevant").text


def test_same_origin_guard_is_proxy_aware(client, monkeypatch):
    _add(client, 31)
    # cross-site is always rejected
    r = client.post(
        "/radar/1/decision", data={"decision": "pass"}, headers={"Sec-Fetch-Site": "cross-site"}
    )
    assert r.status_code == 403
    # foreign Origin with no fetch-site signal is rejected
    r = client.post(
        "/radar/1/decision", data={"decision": "pass"}, headers={"Origin": "https://evil.invalid"}
    )
    assert r.status_code == 403
    # a tunnelled page: Origin differs from Host but the browser says same-origin -> allowed
    r = client.post(
        "/radar/1/decision",
        data={"decision": "pass"},
        headers={"Origin": "https://8080-abc.cloudshell.dev", "Sec-Fetch-Site": "same-origin"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    # explicitly trusted origin without the header is allowed
    monkeypatch.setenv("RADAR_TRUSTED_ORIGINS", "https://radar.example")
    r = client.post(
        "/radar/1/decision",
        data={"decision": "interested"},
        headers={"Origin": "https://radar.example"},
        follow_redirects=False,
    )
    assert r.status_code == 303


def test_queue_is_default_and_filters_hard(client):
    from datetime import UTC, datetime, timedelta

    _add(client, 41, buying_options=["FIXED_PRICE"])  # 1 queue
    _add(client, 42, buying_options=["FIXED_PRICE"])  # 2 fair -> out
    _add(client, 43, buying_options=["FIXED_PRICE"])  # 3 score 70 -> out
    _add(
        client, 44, item_end_time=(datetime.now(UTC) - timedelta(hours=1)).isoformat()
    )  # 4 ended -> out
    _add(client, 45)  # 5 unscreened -> out of queue, in relevant view
    _triage(client, 1, True, 88)
    _triage(client, 2, True, 90)
    _triage(client, 3, True, 70)
    _triage(client, 4, True, 95)
    from radar.db import TriageResult

    with client.app.state.sessions.begin() as s:
        for tid, read in ((1, "UNDERPRICED"), (2, "FAIR"), (3, "UNDERPRICED"), (4, "UNDERPRICED")):
            t = s.get(TriageResult, tid)
            t.data = {**t.data, "value_read": read}
    html = client.get("/radar").text
    assert "navaho cuff 41" in html
    for absent in ("navaho cuff 42", "navaho cuff 43", "navaho cuff 44", "navaho cuff 45"):
        assert absent not in html
    assert ">1</strong>" in html  # queue count tile
    assert "navaho cuff 45" in client.get("/radar?show=relevant").text


def test_sweep_archives_with_reasons(client):
    from datetime import UTC, datetime, timedelta

    from radar.db import DiscoveredItem, TriageResult
    from radar.queue import sweep

    now = datetime.now(UTC)
    _add(client, 51, buying_options=["FIXED_PRICE"])  # 1 irrelevant
    _add(client, 52, buying_options=["FIXED_PRICE"])  # 2 overpriced
    _add(client, 53, item_end_time=(now - timedelta(hours=2)).isoformat())  # 3 ended
    _add(client, 54, buying_options=["FIXED_PRICE"])  # 4 stale (first_seen backdated)
    _add(client, 55, buying_options=["FIXED_PRICE"])  # 5 keeps
    _triage(client, 1, False, 10)
    _triage(client, 2, True, 60)
    _triage(client, 5, True, 85)
    with client.app.state.sessions.begin() as s:
        s.get(TriageResult, 2).data = {**s.get(TriageResult, 2).data, "value_read": "OVERPRICED"}
        s.get(DiscoveredItem, 4).first_seen = (now - timedelta(days=10)).replace(tzinfo=None)
    counts = sweep(client.app.state.sessions, now=now)
    assert counts == {"irrelevant": 1, "overpriced": 1, "ended": 1, "stale": 1}
    with client.app.state.sessions() as s:
        assert s.get(DiscoveredItem, 5).inbox_status == "NEW"
        assert s.get(DiscoveredItem, 3).normalized["archive_reason"] == "ended"
    html = client.get("/radar?show=all&status=archived").text
    assert "ARCHIVED · ENDED" in html and "Restore" in html
    # restore works
    client.post("/radar/3/decision", data={"decision": "reset"})
    with client.app.state.sessions() as s:
        assert s.get(DiscoveredItem, 3).inbox_status == "NEW"
    # idempotent
    assert sweep(client.app.state.sessions, now=now) == {"ended": 1}


def test_purge_deletes_ended_and_old_archived_but_keeps_interested(client):
    from datetime import UTC, datetime, timedelta

    from radar.db import DiscoveredItem, TriageResult
    from radar.queue import purge

    now = datetime.now(UTC)
    old_end = (now - timedelta(days=20)).isoformat()
    _add(client, 61, item_end_time=old_end)  # 1 ended long ago -> purged
    _add(client, 62, item_end_time=old_end)  # 2 ended but INTERESTED -> kept
    _add(
        client, 63, buying_options=["FIXED_PRICE"], item_end_time=None
    )  # 3 archived long ago -> purged
    _add(
        client, 64, buying_options=["FIXED_PRICE"], item_end_time=None
    )  # 4 archived yesterday -> kept
    _add(client, 65, item_end_time=(now + timedelta(days=1)).isoformat())  # 5 live -> kept
    _triage(client, 1, True, 50)
    with client.app.state.sessions.begin() as s:
        s.get(DiscoveredItem, 2).inbox_status = "INTERESTED"
        for iid, when in ((3, now - timedelta(days=30)), (4, now - timedelta(days=1))):
            it = s.get(DiscoveredItem, iid)
            it.inbox_status = "ARCHIVED"
            it.normalized = {**it.normalized, "archived_at": when.isoformat()}
    counts = purge(client.app.state.sessions, now=now)
    assert counts == {"ended": 1, "archived": 1}
    with client.app.state.sessions() as s:
        assert {i.id for i in s.query(DiscoveredItem)} == {2, 4, 5}
        assert s.query(TriageResult).count() == 0  # verdict for item 1 went with it
    # disabled when purge_days is 0
    assert purge(client.app.state.sessions, {"purge_days": 0}, now=now) == {}


def test_pass_records_reason_and_redirects_to_next(client):
    from radar.db import DiscoveredItem

    _add(client, 71)
    _add(client, 72)
    r = client.post(
        "/radar/1/decision",
        data={"decision": "pass", "reason": "price", "back": "/radar?show=all", "next_id": 2},
        follow_redirects=False,
    )
    assert r.headers["location"] == "/radar?show=all#item-2"
    with client.app.state.sessions() as s:
        n = s.get(DiscoveredItem, 1).normalized
        assert n["pass_reason"] == "price" and n["passed_at"]
    html = client.get("/radar?show=all&status=passed").text
    assert "passed: Overpriced" in html
    # a note rides along with any reason and shows on the card
    client.post(
        "/radar/2/decision",
        data={"decision": "pass", "reason": "condition", "note": "  stone cracked\n in half "},
    )
    with client.app.state.sessions() as s:
        n = s.get(DiscoveredItem, 2).normalized
        assert n["pass_reason"] == "condition" and n["pass_note"] == "stone cracked in half"
    html = client.get("/radar?show=all&status=passed").text
    assert "Damaged / condition" in html and "stone cracked in half" in html
    # unknown reason -> other; reset clears reason and note
    client.post("/radar/2/decision", data={"decision": "pass", "reason": "zzz"})
    client.post("/radar/2/decision", data={"decision": "reset"})
    with client.app.state.sessions() as s:
        n = s.get(DiscoveredItem, 2).normalized
        assert "pass_reason" not in n and "pass_note" not in n


def test_condition_badge_from_seller_description(client):
    _add(client, 91, raw_description="Nice old cuff, one stone has a chip. Sold as is.")
    html = client.get("/radar?show=all").text
    assert "CONDITION · CHIP" in html


def test_card_shows_walk_away_and_links(client):
    _add(client, 81, buying_options=["FIXED_PRICE"], item_price="120", shipping_cost="8")
    _triage(client, 1, True, 85)
    from radar.db import TriageResult

    with client.app.state.sessions.begin() as s:
        t = s.get(TriageResult, 1)
        t.data = {**t.data, "value_read": "UNDERPRICED", "resale_estimate_usd": [250, 400]}
    html = client.get("/radar").text
    assert "Walk away above" in html and "$156.77" in html  # 250 -> 35% tier, minus $8 ship
    assert "LH_Sold=1" in html and "lens.google.com/uploadbyurl" in html
    assert 'data-key="i"' in html and "Pass…" in html


def test_comp_links_use_model_query_and_outcome_is_recorded(client):
    from radar.db import DiscoveredItem, TriageResult

    _add(client, 91, buying_options=["FIXED_PRICE"])
    _triage(client, 1, True, 90)
    with client.app.state.sessions.begin() as s:
        t = s.get(TriageResult, 1)
        t.data = {
            **t.data,
            "value_read": "UNDERPRICED",
            "comp_query": "navajo cluster cuff",
            "hallmark_read": "RW STERLING",
            "deep": True,
        }
    html = client.get("/radar").text
    assert "_nkw=navajo+cluster+cuff&amp;LH_Sold=1" in html
    assert "liveauctioneers.com/auction-results/?keyword=navajo+cluster+cuff" in html
    assert "worthpoint.com" in html and "invaluable.com" in html
    assert "hallmark read: <strong>RW STERLING</strong>" in html and "deep-reviewed" in html
    assert 'name="bought_for"' not in html  # only on Interested
    client.post("/radar/1/decision", data={"decision": "interested"})
    html = client.get("/radar?show=all&status=interested").text
    assert 'name="bought_for"' in html
    r = client.post(
        "/radar/1/outcome", data={"bought_for": "$1,250.00", "sold_for": ""}, follow_redirects=False
    )
    assert r.status_code == 303 and r.headers["location"].endswith("#item-1")
    with client.app.state.sessions() as s:
        n = s.get(DiscoveredItem, 1).normalized
        assert n["bought_for"] == "1250.00" and "sold_for" not in n and n["bought_for_at"]
    assert client.post("/radar/1/outcome", data={"bought_for": "abc"}).status_code == 400


def test_live_verdict_follows_current_price_and_owner_resale_overrides(client):
    from radar.db import DiscoveredItem, TriageResult

    _add(client, 95, item_price="290", shipping_cost="13.65", bid_count=17)
    _triage(client, 1, True, 88)
    with client.app.state.sessions.begin() as s:
        t = s.get(TriageResult, 1)
        t.data = {**t.data, "value_read": "UNDERPRICED", "resale_estimate_usd": [550, 1000]}
    assert "navaho cuff 95" in client.get("/radar").text  # 290+13.65 vs 550 low -> underpriced
    # the watch moves the bid to $460: same stored verdict, live read is FAIR -> out of the queue
    with client.app.state.sessions.begin() as s:
        it = s.get(DiscoveredItem, 1)
        it.normalized = {**it.normalized, "item_price": "460", "bid_count": 26}
    assert "navaho cuff 95" not in client.get("/radar").text
    html = client.get("/radar?show=all").text
    assert "Fair" in html and ">70<" in html  # capped score shown live
    # owner says it's really an $900-1,500 piece -> underpriced again, walk-away from their number
    r = client.post(
        "/radar/1/outcome",
        data={"resale_low": "900", "resale_high": "1500"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    html = client.get("/radar").text
    assert "navaho cuff 95" in html and "(yours)" in html and "resale ~$900–1500" in html
    assert client.post("/radar/1/outcome", data={"resale_low": "900"}).status_code == 400


def test_buy_now_badge_and_ordering(client):
    from radar.db import TriageResult

    _add(
        client, 101, buying_options=["FIXED_PRICE"], item_price="60", shipping_cost="5"
    )  # 1 BIN under walk-away
    _add(client, 102, item_price="60", shipping_cost="5")  # 2 auction, same numbers, higher score
    _triage(client, 1, True, 82)
    _triage(client, 2, True, 95)
    with client.app.state.sessions.begin() as s:
        for tid in (1, 2):
            t = s.get(TriageResult, tid)
            t.data = {**t.data, "value_read": "UNDERPRICED", "resale_estimate_usd": [200, 300]}
    html = client.get("/radar").text
    assert "BUY NOW" in html
    assert html.index("navaho cuff 101") < html.index(
        "navaho cuff 102"
    )  # BIN first despite lower score
    assert html.count("BUY NOW") == 1


def test_interested_list_is_not_hidden_by_queue_default(client):
    _add(client, 95)
    client.post("/radar/1/decision", data={"decision": "interested"})
    r = client.get("/radar?status=interested")
    assert 'id="item-1"' in r.text
    assert "<title>Radar finds</title>" in r.text
