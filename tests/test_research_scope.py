"""Synthetic temporary applications only; no owner cohort is initialized here."""

import io
import json
import sqlite3

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import event, select

from radar.db import (
    AlertMessage,
    DiscoveredItem,
    DiscoveryRun,
    Feedback,
    Listing,
    Setting,
    VisionPhoto,
    database,
)
from radar.main import create_app
from radar.research_scope import (
    NEW_ONLY_SCOPE,
    SCOPE_LABEL,
    SCOPE_SETTING_ID,
    inspect_scope,
    new_only_marker,
)


@pytest.fixture
def scoped_db(tmp_path, item):
    path = tmp_path / "synthetic-scope.db"
    engine, sessions = database(f"sqlite:///{path}")
    data = item.model_dump(mode="json") | dict(
        source="ShopGoodwill", raw_title="SYNTHETIC scoped research ring"
    )
    with sessions.begin() as session:
        session.add(Setting(id=SCOPE_SETTING_ID, data=new_only_marker()))
        listing = Listing(dedup_key="synthetic-scope", data=data, original_data={})
        session.add(listing)
        session.flush()
        session.add(
            DiscoveredItem(
                identity="synthetic-scope",
                source="ShopGoodwill",
                ebay_item_id="synthetic",
                normalized=data,
                original_data={},
                listing_id=listing.id,
            )
        )
    engine.dispose()
    return path


def test_expected_scope_never_creates_a_missing_application(tmp_path):
    path = tmp_path / "must-not-create.db"
    app = create_app(f"sqlite:///{path}", expected_scope=NEW_ONLY_SCOPE)
    with pytest.raises(ValueError, match="existing marked"):
        with TestClient(app):
            pass
    assert not path.exists()


def test_missing_marker_fails_before_startup_seeding(tmp_path):
    path = tmp_path / "unmarked.db"
    engine, _ = database(f"sqlite:///{path}")
    engine.dispose()
    with pytest.raises(ValueError, match="scope validation failed"):
        with TestClient(create_app(f"sqlite:///{path}", expected_scope=NEW_ONLY_SCOPE)):
            pass
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as db:
        assert db.execute("SELECT count(*) FROM search_patterns").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM settings").fetchone()[0] == 0


@pytest.mark.parametrize(
    "updates",
    [
        {"schema": "unexpected"},
        {"scope": "historical_restored"},
        {"historical_restoration": True},
        {"allowed_sources": ["shopgoodwill", "ebay"]},
        {"qualified_messages": 6},
        {"unrecognized": "SYNTHETIC PRIVATE VALIDATION VALUE"},
    ],
)
def test_incompatible_scope_marker_fails_without_exposing_values(scoped_db, updates):
    marker = new_only_marker() | updates
    with sqlite3.connect(scoped_db) as db:
        db.execute("UPDATE settings SET data=? WHERE id=?", (json.dumps(marker), SCOPE_SETTING_ID))
    with pytest.raises(ValueError, match="scope validation failed") as failure:
        inspect_scope(f"sqlite:///{scoped_db}")
    assert "PRIVATE VALIDATION VALUE" not in str(failure.value)
    with sqlite3.connect(scoped_db) as db:
        assert (
            json.loads(db.execute("SELECT data FROM settings WHERE id=4").fetchone()[0]) == marker
        )


@pytest.mark.parametrize(
    "contamination",
    ["listing", "candidate_source", "candidate_data", "fixture_run", "production_run", "message"],
)
def test_new_only_rejects_ebay_inventory_and_discovery_history(scoped_db, contamination):
    engine, sessions = database(f"sqlite:///{scoped_db}")
    with sessions.begin() as session:
        if contamination == "listing":
            row = session.get(Listing, 1)
            row.data = row.data | {"source": "eBay"}
        elif contamination == "candidate_source":
            session.get(DiscoveredItem, 1).source = "eBay"
        elif contamination == "candidate_data":
            row = session.get(DiscoveredItem, 1)
            row.normalized = row.normalized | {"source": "eBay"}
        elif contamination.endswith("_run"):
            session.add(DiscoveryRun(mode=contamination.removesuffix("_run"), options={}))
        else:
            session.add(
                AlertMessage(
                    source_id="ebay",
                    digest="synthetic",
                    raw_b64="",
                    safe_text="",
                    subject="",
                    message_type="UNKNOWN",
                    data={},
                    synthetic=False,
                )
            )
    engine.dispose()
    with pytest.raises(ValueError, match="scope validation failed"):
        with TestClient(create_app(f"sqlite:///{scoped_db}")):
            pass


def test_scope_is_visible_across_existing_pages_and_health(scoped_db):
    with TestClient(create_app(f"sqlite:///{scoped_db}", expected_scope=NEW_ONLY_SCOPE)) as client:
        for path in (
            "/feed",
            "/feed/evaluation",
            "/inbox/candidates/1",
            "/listings/1",
            "/listings/1/edit",
            "/queue",
            "/vision",
            "/settings",
        ):
            response = client.get(path)
            assert response.status_code == 200, path
            assert SCOPE_LABEL in response.text
            assert "not been restored" in response.text
            assert "does not exempt eBay data" in response.text
            assert response.headers["X-Radar-Research-Scope"] == NEW_ONLY_SCOPE
            assert response.headers["Cache-Control"] == "private, no-store"
            assert "img-src 'self';" in response.headers["Content-Security-Policy"]
        assert client.get("/health").json()["scope"] == new_only_marker()


def test_new_only_blocks_new_intake_and_execution_without_writing(scoped_db):
    with TestClient(create_app(f"sqlite:///{scoped_db}")) as client:
        for path in (
            "/discovery/run",
            "/discovery/settings",
            "/vision/cases/1/analyze",
            "/inbox/import",
            "/inbox/import-data",
            "/inbox/manual",
            "/capture/save",
            "/sources/shopgoodwill",
            "/listings",
            "/import",
            "/identify/cases",
        ):
            response = client.post(path, data={"mode": "production", "approved_run": "yes"})
            assert response.status_code == 403, path
            assert SCOPE_LABEL in response.text
        with client.app.state.sessions() as session:
            assert list(session.scalars(select(DiscoveryRun))) == []
            assert list(session.scalars(select(AlertMessage))) == []
            assert len(list(session.scalars(select(Listing)))) == 1


def test_existing_evidence_research_state_feedback_and_photos_work(scoped_db):
    with TestClient(create_app(f"sqlite:///{scoped_db}")) as client:
        assert (
            client.post(
                "/listings/1/edit",
                data={
                    "condition": "SYNTHETIC owner-reviewed wear",
                    "source": "eBay",
                },
                follow_redirects=False,
            ).status_code
            == 303
        )
        assert (
            client.post(
                "/listings/1/state", data={"state": "RESEARCH"}, follow_redirects=False
            ).status_code
            == 303
        )
        assert (
            client.post(
                "/listings/1/feedback", data={"action": "Interested"}, follow_redirects=False
            ).status_code
            == 303
        )
        assert client.get("/vision/for/listing/1", follow_redirects=False).status_code == 303
        image = io.BytesIO()
        Image.new("RGB", (32, 32), "blue").save(image, "PNG")
        response = client.post(
            "/vision/cases/1/photos",
            files={"files": ("synthetic.png", image.getvalue(), "image/png")},
            data={"roles": "FRONT"},
            follow_redirects=False,
        )
        assert response.status_code == 303
        with client.app.state.sessions() as session:
            listing = session.get(Listing, 1)
            assert listing.data["source"] == "ShopGoodwill"
            assert listing.data["condition"] == "SYNTHETIC owner-reviewed wear"
            assert listing.review_state == "WATCH"
            assert session.scalar(select(Feedback)).action == "Interested"
            photo = session.scalar(select(VisionPhoto))
            assert photo.active
            photo_id = photo.id
        assert client.get(f"/vision/photos/{photo_id}").status_code == 200
        assert (
            client.post(f"/vision/photos/{photo_id}/remove", follow_redirects=False).status_code
            == 303
        )
        with client.app.state.sessions() as session:
            assert not session.get(VisionPhoto, photo_id).active


def test_feed_render_does_not_write_scope_or_owner_data(scoped_db):
    with TestClient(create_app(f"sqlite:///{scoped_db}")) as client:
        statements = []

        def record(connection, cursor, statement, parameters, context, executemany):
            statements.append(statement)

        event.listen(client.app.state.engine, "before_cursor_execute", record)
        try:
            assert client.get("/feed").status_code == 200
            assert client.get("/feed/evaluation").status_code == 200
        finally:
            event.remove(client.app.state.engine, "before_cursor_execute", record)
        assert statements and all(
            statement.lstrip().upper().startswith("SELECT") for statement in statements
        )


def test_plain_sqlite_backup_preserves_scope_and_owner_feedback(scoped_db, tmp_path):
    with TestClient(create_app(f"sqlite:///{scoped_db}")) as client:
        assert (
            client.post(
                "/listings/1/feedback", data={"action": "Passed"}, follow_redirects=False
            ).status_code
            == 303
        )
    snapshot = tmp_path / "transferred-snapshot.db"
    with sqlite3.connect(f"file:{scoped_db}?mode=ro", uri=True) as source:
        with sqlite3.connect(snapshot) as destination:
            source.backup(destination)
            assert destination.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            assert destination.execute("PRAGMA foreign_key_check").fetchall() == []
    with TestClient(create_app(f"sqlite:///{snapshot}", expected_scope=NEW_ONLY_SCOPE)) as client:
        assert client.get("/health").json()["scope"] == new_only_marker()
        archive = client.get("/feed?scope=archive")
        assert "NO GO ✓" in archive.text
        assert archive.context["cards"][0]["latest_feedback"]["action"] == "Passed"
        with client.app.state.sessions() as session:
            assert session.get(Listing, 1).review_state == "PASSED"
            assert len(list(session.scalars(select(Feedback)))) == 1


def test_unmarked_application_retains_existing_behavior(client, item):
    assert "scope" not in client.get("/health").json()
    assert SCOPE_LABEL not in client.get("/feed").text
    ebay = item.model_dump(mode="json") | {"source": "eBay"}
    assert (
        client.post(
            "/listings", data={"payload": json.dumps(ebay)}, follow_redirects=False
        ).status_code
        == 303
    )
