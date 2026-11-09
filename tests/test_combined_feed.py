"""Synthetic data only; combined view must not copy or refresh source records."""

import json
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage

import pytest
from sqlalchemy import select
from test_search_evaluation import record

from radar.combined_feed import email_cards
from radar.db import DiscoveryObservation, Listing, database
from radar.intake.registry import seed_sources
from radar.intake.service import import_alerts
from radar.schemas import Preferences


def email_db(tmp_path, item):
    root = tmp_path / "email-review-nov08"
    root.mkdir(mode=0o700)
    (root / "ready.json").write_text(
        json.dumps({"version": 1, "samples": {"shopgoodwill": "synthetic", "hibid": "synthetic"}})
    )
    path = root / "review.db"
    engine, sessions = database("sqlite:///" + str(path))
    with sessions.begin() as session:
        for source in ("ShopGoodwill", "HiBid"):
            data = item.model_dump(mode="json") | {
                "source": source,
                "raw_title": "SYNTHETIC " + source,
                "shipping_status": "UNKNOWN",
                "shipping_cost": None,
            }
            session.add(Listing(dedup_key=source, data=data, original_data={}))
    engine.dispose()
    return path


def test_one_page_three_sources_without_database_writes(client, tmp_path, item, monkeypatch):
    path = email_db(tmp_path, item)
    before = path.read_bytes()
    monkeypatch.setenv("RADAR_EMAIL_REVIEW_DB", str(path))
    with client.app.state.sessions.begin() as s:
        record(
            s,
            item.model_copy(update={"raw_title": "SYNTHETIC EBAY"}),
            "ebay",
            query="SYNTHETIC query",
        )
    response = client.get("/opportunities")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "private, no-store"
    for text in (
        "SYNTHETIC EBAY",
        "SYNTHETIC ShopGoodwill",
        "SYNTHETIC HiBid",
        "Missing costs",
        "Email snapshot",
        "shipping_cost",
        "UNKNOWN",
    ):
        assert text in response.text
    assert "data-source-expiry" in response.text
    assert path.read_bytes() == before
    filtered = client.get("/opportunities?source=HiBid").text
    assert "SYNTHETIC HiBid" in filtered and "SYNTHETIC EBAY" not in filtered


def test_stale_ebay_is_withheld_not_refreshed(client, item):
    with client.app.state.sessions.begin() as s:
        record(s, item.model_copy(update={"raw_title": "SYNTHETIC EXPIRED"}), "ebay", query="q")
        s.scalar(select(DiscoveryObservation)).observed_at = datetime.now(UTC) - timedelta(days=2)
    response = client.get("/opportunities")
    assert "SYNTHETIC EXPIRED" not in response.text
    assert "1 stale pilot item(s) withheld" in response.text


def test_unavailable_email_does_not_break_pilot(client, monkeypatch):
    monkeypatch.setenv("RADAR_EMAIL_REVIEW_DB", "/missing/review.db")
    response = client.get("/opportunities")
    assert response.status_code == 200 and "Email review unavailable" in response.text
    assert "/missing" not in response.text


def test_wrong_source_or_symlink_refused(tmp_path, item):
    path = email_db(tmp_path, item)
    engine, sessions = database("sqlite:///" + str(path))
    with sessions.begin() as s:
        row = s.scalar(select(Listing))
        row.data = row.data | {"source": "eBay"}
    engine.dispose()
    with pytest.raises(ValueError):
        email_cards(path, Preferences())
    link = tmp_path / "other"
    link.symlink_to(path.parent)
    with pytest.raises(ValueError):
        email_cards(link / "review.db", Preferences())


def test_retained_email_omissions_recovered_without_reimport(client, tmp_path, item, monkeypatch):
    path = email_db(tmp_path, item)
    engine, sessions = database("sqlite:///" + str(path))
    bodies = {
        "shopgoodwill": (
            "Your Personal Shopper",
            """Personal Shopper
Search Keyword(s): SYNTHETIC lot
Item #: 501
Title: SYNTHETIC jewelry
Price: $9.99
Bids: 2 bids
Started: 11/6/2026 12:03:41 PM
URL: https://shopgoodwill.com/item/501
""",
        ),
        "hibid": (
            "New lots for your saved SYNTHETIC lot search",
            """Your saved search
***
SYNTHETIC lot
***
SYNTHETIC title (
https://hibid.com/lot/502 )
Ends 11/09
https://hibid.com/lots?q=SYNTHETIC%20lot
""",
        ),
    }
    with sessions.begin() as session:
        seed_sources(session)
        for source, (subject, body) in bodies.items():
            msg = EmailMessage()
            msg["Subject"] = subject
            msg["Date"] = "Sun, 08 Nov 2026 10:01:55 +0000"
            msg.set_content(body)
            import_alerts(session, source, [msg.as_bytes()])
    engine.dispose()
    before = path.read_bytes()
    cards = email_cards(path, Preferences())
    shop = next(c for c in cards if c["item"].source_listing_id == "501")
    hibid = next(c for c in cards if c["item"].source_listing_id == "502")
    assert shop["email_snapshot"]["bids"] == "2 bids"
    assert shop["email_snapshot"]["price_quote"] == "$9.99"
    assert hibid["email_snapshot"]["closing_hint"] == "Ends 11/09"
    assert hibid["item"].item_end_time is None
    assert hibid["compact"]["price_text"] == "UNKNOWN"
    assert "2026-11-08" in hibid["compact"]["observed_label"]
    monkeypatch.setenv("RADAR_EMAIL_REVIEW_DB", str(path))
    page = client.get("/opportunities").text
    assert "2 bids" in page and "Ends 11/09" in page
    assert "not a verified deadline" in page
    assert path.read_bytes() == before
