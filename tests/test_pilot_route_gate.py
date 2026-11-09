"""Actual application routes/schema, invented fixtures and mocked authenticated matching."""

import io
import secrets
import threading

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import select

from radar.db import Listing, VisionPhoto
from radar.discovery.local_deletion_slice import SyntheticRunner
from radar.discovery.pilot_route_gate import PilotRouteGate
from radar.jewelry.images import clean_image
from radar.jewelry.service import add_photos, open_case
from radar.main import create_app
from radar.schemas import ListingInput


@pytest.fixture
def setup(tmp_path, monkeypatch):
    key = secrets.token_bytes(32)
    runner = SyntheticRunner(tmp_path / "runner", key)
    root = tmp_path / "pilot"
    monkeypatch.setenv("VISION_IMAGE_DIR", str(root / "photos"))
    gate = PilotRouteGate(root, key, runner.exchange, timeout=1)
    app = create_app("sqlite:///" + str(root / "pilot.db"), pilot_gate=gate)
    with TestClient(app) as client:
        with app.state.sessions.begin() as db:
            row = Listing(
                dedup_key="SYNTHETIC-route",
                review_state="RESEARCH",
                data=ListingInput(
                    source="SYNTHETIC route fixture",
                    synthetic=True,
                    raw_title="SYNTHETIC guarded ring",
                ).model_dump(mode="json"),
                original_data={
                    "seller": {"userId": "SYNTHETIC-account", "username": "SYNTHETIC-seller"}
                },
            )
            db.add(row)
            db.flush()
            listing_id = row.id
        case = open_case(app.state.sessions, "listing", listing_id)
        raw = io.BytesIO()
        Image.new("RGB", (64, 48), "turquoise").save(raw, "PNG")
        add_photos(
            app.state.sessions, root / "photos", case, [(clean_image(raw.getvalue()), "FRONT")]
        )
        with app.state.sessions() as db:
            photo_id = db.scalar(select(VisionPhoto.id))
        gate.freeze(app.state.sessions)
        yield client, gate, runner, listing_id, photo_id


def routes(photo):
    return ["/feed?dataset=fixture", f"/feed/photos/{photo}", f"/vision/photos/{photo}"]


def test_actual_feed_and_both_photo_routes_are_guarded(setup):
    client, gate, runner, _, photo = setup
    for path in routes(photo):
        response = client.get(path)
        assert response.status_code == 200
        assert response.headers["cache-control"] == "private, no-store"
    runner.notice()
    for path in routes(photo):
        response = client.get(path)
        assert response.status_code == 503
        assert "SYNTHETIC guarded ring" not in response.text
        assert not response.content.startswith(b"\x89PNG")
    assert gate.blocked


@pytest.mark.parametrize("route", range(3))
def test_outage_releases_no_feed_or_photo_bytes(setup, route):
    client, gate, _, _, photo = setup
    gate.exchange = lambda _: (_ for _ in ()).throw(OSError("SYNTHETIC outage"))
    response = client.get(routes(photo)[route])
    assert response.status_code == 503
    assert b"SYNTHETIC guarded ring" not in response.content
    assert not response.content.startswith(b"\x89PNG")


@pytest.mark.parametrize(
    "path",
    [
        "/",
        "/listings/1",
        "/discovery",
        "/vision",
        "/feed/evaluation",
        "/openapi.json",
        "/docs",
        "/feed/",
        "/feed/photos/x/remove",
    ],
)
def test_alternate_routes_do_not_bypass_gate(setup, path):
    client, _, _, _, _ = setup
    assert client.get(path).status_code in (403, 404)


def test_mutations_and_head_do_not_bypass_gate(setup):
    client, _, _, listing, photo = setup
    assert (
        client.post(f"/feed/review/listing/{listing}", data={"decision": "GO"}).status_code == 403
    )
    assert client.head(f"/vision/photos/{photo}").status_code == 403


def test_database_write_during_match_denies_actual_schema(setup):
    client, gate, runner, listing, _ = setup

    def exchange(frame):
        answer = runner.exchange(frame)
        with client.app.state.sessions.begin() as db:
            row = db.get(Listing, listing)
            row.data = row.data | {"raw_title": "SYNTHETIC concurrent change"}
        return answer

    gate.exchange = exchange
    response = client.get("/feed?dataset=fixture")
    assert response.status_code == 503
    assert "concurrent change" not in response.text


def test_write_while_route_renders_is_buffered_and_discarded(setup, monkeypatch):
    import radar.sourcing_routes as sourcing

    client, gate, _, listing, _ = setup
    original = sourcing.build_feed

    def changed(*args, **kwargs):
        cards = original(*args, **kwargs)
        with client.app.state.sessions.begin() as db:
            row = db.get(Listing, listing)
            row.data = row.data | {"raw_title": "SYNTHETIC changed during render"}
        return cards

    monkeypatch.setattr(sourcing, "build_feed", changed)
    response = client.get("/feed?dataset=fixture")
    assert response.status_code == 503
    assert b"SYNTHETIC guarded ring" not in response.content
    assert gate.blocked


def test_extra_copy_blocks_photo_release(setup):
    client, gate, _, _, photo = setup
    (gate.root / "extra-export.json").write_text("SYNTHETIC copy")
    assert client.get(f"/feed/photos/{photo}").status_code == 503


def test_hung_exchange_has_real_timeout_and_no_retry(setup):
    client, gate, _, _, photo = setup
    release = threading.Event()
    calls = []

    def exchange(_):
        calls.append(1)
        release.wait(2)
        return b""

    gate.exchange, gate.timeout = exchange, 0.02
    try:
        assert client.get(f"/feed/photos/{photo}").status_code == 503
        assert client.get("/feed?dataset=fixture").status_code == 503
        assert len(calls) == 1
    finally:
        release.set()


def test_gate_cannot_attach_to_existing_owner_directory(tmp_path):
    owner = tmp_path / "new-only-research"
    owner.mkdir()
    with pytest.raises(ValueError):
        PilotRouteGate(owner, b"fixture", lambda _: None)


def test_wrong_database_configuration_rejected(tmp_path, monkeypatch):
    gate = PilotRouteGate(tmp_path / "pilot", b"fixture", lambda _: None)
    monkeypatch.setenv("VISION_IMAGE_DIR", str(gate.root / "photos"))
    with pytest.raises(ValueError):
        create_app("sqlite:///" + str(tmp_path / "owner.db"), pilot_gate=gate)


def test_default_application_still_serves_without_transport(client):
    assert client.get("/feed?dataset=fixture").status_code == 200
    assert client.get("/listings").status_code != 503


def test_deletion_during_render_denies_buffered_feed(setup, monkeypatch):
    import radar.sourcing_routes as sourcing

    client, _, runner, _, _ = setup
    original = sourcing.build_feed

    def changed(*args, **kwargs):
        cards = original(*args, **kwargs)
        runner.notice()
        return cards

    monkeypatch.setattr(sourcing, "build_feed", changed)
    response = client.get("/feed?dataset=fixture")
    assert response.status_code == 503
    assert b"SYNTHETIC guarded ring" not in response.content


def test_saved_clear_response_cannot_release_another_route(setup):
    client, gate, runner, _, photo = setup
    saved = []

    def exchange(frame):
        if len(saved) < 2:
            answer = runner.exchange(frame)
            saved.append(answer)
            return answer
        return saved[-1]

    gate.exchange = exchange
    assert client.get("/feed?dataset=fixture").status_code == 200
    assert client.get(f"/vision/photos/{photo}").status_code == 503


def test_unfrozen_pilot_never_serves(setup):
    client, gate, _, _, _ = setup
    gate.expected = None
    assert client.get("/feed?dataset=fixture").status_code == 503


def test_non_synthetic_actual_rows_cannot_be_frozen(setup):
    client, gate, _, listing, _ = setup
    gate.expected = None
    with client.app.state.sessions.begin() as db:
        row = db.get(Listing, listing)
        row.data = row.data | {"synthetic": False}
    with pytest.raises(ValueError, match="Synthetic"):
        gate.freeze(client.app.state.sessions)


def test_photo_root_reconfiguration_fails_closed(setup, monkeypatch, tmp_path):
    client, _, _, _, photo = setup
    monkeypatch.setenv("VISION_IMAGE_DIR", str(tmp_path / "unrelated-photos"))
    assert client.get(f"/vision/photos/{photo}").status_code == 503
