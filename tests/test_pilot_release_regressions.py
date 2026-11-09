"""Synthetic actual-schema regressions for the independent release audit."""

import json

import pytest
from sqlalchemy import select
from test_original_source_images import source_photo
from test_pilot_https_job import ENDPOINT, SECRET, SELLER, TOKEN, pilot
from test_pilot_https_job import env as env

from radar.db import DiscoveredItem, DiscoveryObservation, Listing, database
from radar.discovery.service import promote
from radar.ebay_deletion.pilot_job import cycle, sellers_in
from radar.ebay_deletion.protocol import digest


@pytest.mark.parametrize("decision", ["GO", "NO_GO"])
@pytest.mark.parametrize("hydrated", [False, True])
def test_promoted_pilot_remains_maintainable_and_deletable(
    env,  # noqa: F811
    tmp_path,
    monkeypatch,
    decision,
    hydrated,
):
    from fastapi.testclient import TestClient

    from radar.main import create_app

    root, photo = pilot(tmp_path)
    if hydrated:
        from radar.discovery.identity_hydration import FIELD, evidence_for
        from radar.schemas import utcnow

        engine, sessions = database("sqlite:///" + str(root / "pilot.db"))
        with sessions.begin() as db:
            row = db.scalar(select(DiscoveredItem))
            raw = {"seller": SELLER, "itemId": row.ebay_item_id}
            raw = raw | {FIELD: evidence_for(raw, raw, utcnow()).model_dump(mode="json")}
            row.original_data = raw
            db.scalar(select(DiscoveryObservation)).raw_data = raw
        engine.dispose()
    assert cycle(root, env[3], ENDPOINT, TOKEN) == 0
    monkeypatch.setenv("VISION_IMAGE_DIR", str(root / "photos"))
    with TestClient(create_app("sqlite:///" + str(root / "pilot.db"))) as app:
        with app.app.state.sessions() as db:
            candidate = db.scalar(select(DiscoveredItem)).id
        response = app.post(
            f"/feed/review/candidate/{candidate}",
            data={"decision": decision},
            headers={"Accept": "application/json"},
        )
        assert response.status_code == 200
    assert sellers_in(root) == [SELLER]
    assert cycle(root, env[3], ENDPOINT, TOKEN, maintenance_only=True) == 0
    env[0].accept("a" * 64, {"userId": digest(SECRET, "userId", SELLER["userId"])})
    cycle(root, env[3], ENDPOINT, TOKEN, maintenance_only=True)
    assert sellers_in(root) == []
    assert not photo.exists()
    engine, sessions = database("sqlite:///" + str(root / "pilot.db"))
    with sessions() as db:
        assert db.scalar(select(Listing)).original_data == {}
        assert db.scalar(select(DiscoveredItem)).original_data == {}
        assert db.scalar(select(DiscoveryObservation)).raw_data == {}
    engine.dispose()
    assert SELLER["userId"] not in json.dumps(json.loads((root / "job.json").read_text()))


@pytest.mark.parametrize("age", [1, 72])
def test_production_legacy_views_never_bypass_feed(client, tmp_path, monkeypatch, age):
    candidate, _, photo, _, _ = source_photo(client, tmp_path, monkeypatch, observed_hours=age)
    with client.app.state.sessions.begin() as db:
        listing = promote(db, candidate).id
    title = "SYNTHETIC official source ring"
    for path in ["/", "/discovery", f"/discovery/items/{candidate}", f"/listings/{listing}"]:
        response = client.get(path, follow_redirects=False)
        assert title not in response.text
        assert "private, no-store" == response.headers["cache-control"]
        assert '<img src="https://' not in response.text
    feed = client.get("/feed?scope=all")
    assert (title in feed.text) == (age == 1)
    assert client.get("/feed/photos/" + photo).status_code == (200 if age == 1 else 404)
    if age == 72:
        response = client.post(f"/feed/review/listing/{listing}", data={"decision": "GO"})
        assert response.status_code == 409


def test_combined_template_has_withdrawal_notice_and_countdown(client, tmp_path, monkeypatch):
    source_photo(client, tmp_path, monkeypatch)
    text = client.get("/opportunities").text
    assert 'id="feed-expiry-status"' in text
    assert "compact-card" in text
    assert "data-ended=" in text


def test_supervised_pilot_gates_other_research_surfaces(client, monkeypatch):
    monkeypatch.setenv("RADAR_PILOT_VIEW_ONLY", "true")
    for path in [
        "/",
        "/discovery",
        "/queue",
        "/listings/1/edit",
        "/vision",
        "/identify",
        "/identify/cases/1/export",
        "/capture",
    ]:
        response = client.get(path)
        assert response.status_code == 403
        assert response.headers["cache-control"] == "private, no-store"
    for path in ["/feed", "/opportunities", "/sources"]:
        assert client.get(path).status_code == 200


@pytest.mark.parametrize("bad", ["provider", "mode", "item"])
def test_promoted_identity_envelope_is_not_arbitrary_fallback(tmp_path, bad):
    root, _ = pilot(tmp_path)
    engine, sessions = database("sqlite:///" + str(root / "pilot.db"))
    with sessions.begin() as db:
        row = promote(db, db.scalar(select(DiscoveredItem)).id)
        raw = dict(row.original_data)
        raw[bad] = "invalid"
        row.original_data = raw
    engine.dispose()
    with pytest.raises(ValueError):
        sellers_in(root)
