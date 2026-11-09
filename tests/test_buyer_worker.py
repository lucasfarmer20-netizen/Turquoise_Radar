"""Finite repeatable worker, mocked marketplace/receiver and actual SQLite schema."""

import asyncio
import io
import json
import sqlite3
from datetime import UTC, datetime

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import func, select
from test_pilot_https_job import ENDPOINT, SECRET, SELLER, TOKEN, pilot
from test_pilot_https_job import env as env

from radar.db import DiscoveredItem, DiscoveryObservation, VisionPhoto, database
from radar.discovery import buyer_worker as worker
from radar.discovery.config import Connection
from radar.discovery.worker_budget import Budget, activate, status
from radar.ebay_deletion import pilot_job, retained_job
from radar.ebay_deletion.pilot_match import initialize_retained_registration
from radar.ebay_deletion.protocol import digest
from radar.main import create_app


@pytest.fixture
def setup(env, tmp_path, monkeypatch):  # noqa: F811
    monkeypatch.setenv("PILOT_MATCH_ENDPOINT", ENDPOINT)
    root, _ = pilot(tmp_path)
    pilot_job.cycle(root, env[3], ENDPOINT, TOKEN)
    initialize_retained_registration(env[1].registration)
    env[1].retained_enabled = True
    now = [datetime.now(UTC).replace(hour=12, minute=0, second=0, microsecond=0).timestamp()]
    activate(root / "pilot.db", now[0])
    stream = io.BytesIO()
    Image.new("RGB", (240, 240), "blue").save(stream, format="PNG")
    calls = []

    def route(request):
        calls.append(request)
        if request.url.path.endswith("/token"):
            return httpx.Response(
                200, json={"access_token": "SYNTHETIC-ACCESS-TOKEN", "expires_in": 7200}
            )
        if request.url.path.endswith("/item_summary/search"):
            mode = "AUCTION" if "AUCTION" in request.url.params["filter"] else "FIXED_PRICE"
            assert request.url.params["q"] == "blue stone ring"
            assert "99669" in request.headers["X-EBAY-C-ENDUSERCTX"]
            return httpx.Response(
                200,
                json={
                    "itemSummaries": [
                        {
                            "itemId": f"SYNTHETIC-NEW-{i}",
                            "title": f"SYNTHETIC worker ring {i}",
                            "seller": {"username": f"SYNTHETIC-user-{i}"},
                            "buyingOptions": [mode],
                            "price": {"value": "17.00", "currency": "USD"},
                            "currentBidPrice": {"value": "12.00", "currency": "USD"},
                            "itemEndDate": "2030-12-01T10:00:00Z",
                            "image": {
                                "imageUrl": f"https://i.ebayimg.com/images/g/SYNTHETIC{i}/s-l1600.png"
                            },
                        }
                        for i in range(3)
                    ],
                    "total": 3,
                },
            )
        if request.url.host == "api.ebay.com":
            i = request.url.path.rsplit("-", 1)[-1]
            return httpx.Response(
                200,
                json={
                    "itemId": f"SYNTHETIC-NEW-{i}",
                    "seller": {
                        "username": f"SYNTHETIC-user-{i}",
                        "userId": f"SYNTHETIC-stable-{i}",
                    },
                },
            )
        assert "authorization" not in request.headers and "cookie" not in request.headers
        return httpx.Response(200, content=stream.getvalue(), headers={"Content-Type": "image/png"})

    connection = Connection(
        mode="production",
        production_enabled=True,
        production_approved=True,
        client_id="SYNTHETIC-CLIENT",
        client_secret="SYNTHETIC-SECRET",
        retry_limit=0,
        request_interval=0.1,
    )
    return root, env, now, calls, connection, httpx.MockTransport(route)


def run(setup):
    root, environment, now, _, connection, transport = setup
    environment[1].calls.clear()  # Synthetic hourly passage; real rate limit remains enabled.
    return asyncio.run(
        worker.tick(
            root, connection, TOKEN, environment[3], clock=lambda: now[0], transport=transport
        )
    )


def test_repeatable_dedup_daily_cap_old_cohort_and_ui(setup, monkeypatch):
    root, environment, now, calls, _, _ = setup
    original = (root / "job.json").read_bytes()
    with sqlite3.connect(environment[1].registration) as db:
        cohort = db.execute("SELECT * FROM cohort").fetchall()
    assert run(setup) == "COMPLETED"
    assert len(calls) == 8
    assert run(setup) == "MAINTENANCE_ONLY"
    assert len(calls) == 8
    for _ in range(3):
        now[0] += 3600
        assert run(setup) == "COMPLETED"
    now[0] += 3600
    assert run(setup) == "MAINTENANCE_ONLY"
    assert len(calls) == 32
    assert status(root / "pilot.db")["daily"] == 32
    assert activate(root / "pilot.db", now[0])["total"] == 32  # No restart reset.
    assert (root / "job.json").read_bytes() == original
    with sqlite3.connect(environment[1].registration) as db:
        assert db.execute("SELECT * FROM cohort").fetchall() == cohort
    engine, sessions = database("sqlite:///" + str(root / "pilot.db"))
    with sessions() as db:
        assert db.scalar(select(func.count()).select_from(DiscoveredItem)) == 4
        assert db.scalar(select(func.count()).select_from(DiscoveryObservation)) == 13
        assert (
            db.scalar(select(func.count()).select_from(VisionPhoto).where(VisionPhoto.active)) == 4
        )
    engine.dispose()
    monkeypatch.setenv("VISION_IMAGE_DIR", str(root / "photos"))
    with TestClient(create_app("sqlite:///" + str(root / "pilot.db"))) as app:
        text = app.get("/opportunities").text
        assert "SYNTHETIC worker ring" in text and "/feed/photos/" in text
        assert '<img src="https://' not in text
    environment[0].accept("new-delete", {"userId": digest(SECRET, "userId", "SYNTHETIC-stable-0")})
    environment[1].calls.clear()
    retained_job.cycle(root, environment[3], ENDPOINT, TOKEN)
    assert all(s["userId"] != "SYNTHETIC-stable-0" for s in pilot_job.sellers_in(root))
    # Four generations of this seller's photos are removed, not just the active one.
    assert len(list((root / "photos").iterdir())) == 9


def test_missing_receiver_extension_makes_zero_marketplace_calls(setup):
    root, environment, _, calls, _, _ = setup
    environment[1].retained_enabled = False
    with pytest.raises(ValueError):
        run(setup)
    assert calls == [] and status(root / "pilot.db")["total"] == 0


def test_crash_reservations_no_reset_no_same_hour_replay(setup):
    root, _, now, _, _, _ = setup
    b = Budget(root / "pilot.db", lambda: now[0])
    assert b.begin() == "AUCTION"
    b.reserve("oauth")
    assert Budget(root / "pilot.db", lambda: now[0]).begin() is None
    now[0] += 7200  # Skip missed interval, never replay two runs.
    newer = Budget(root / "pilot.db", lambda: now[0])
    assert newer.begin() == "FIXED_PRICE"
    with pytest.raises(ValueError):
        b.reserve("search")
    newer.reserve("oauth")
    assert status(root / "pilot.db")["total"] == 2


def test_auth_failure_pauses_without_secret_or_retry(setup):
    root, environment, now, calls, connection, _ = setup

    def fail(request):
        calls.append(request)
        return httpx.Response(401, json={"error": "invalid_client", "private": "DO-NOT-LOG"})

    with pytest.raises(ValueError, match="Worker paused"):
        asyncio.run(
            worker.tick(
                root,
                connection,
                TOKEN,
                environment[3],
                clock=lambda: now[0],
                transport=httpx.MockTransport(fail),
            )
        )
    assert len(calls) == 1
    now[0] += 7200
    assert run(setup) == "MAINTENANCE_ONLY"
    saved = json.dumps(status(root / "pilot.db"))
    assert "DO-NOT-LOG" not in saved and "SYNTHETIC-SECRET" not in saved


def test_new_match_removed_before_original_hold(setup):
    root, environment, _, _, _, _ = setup
    run(setup)
    environment[0].accept("old-hold", {"username": digest(SECRET, "username", SELLER["username"])})
    environment[0].accept("new-match", {"userId": digest(SECRET, "userId", "SYNTHETIC-stable-0")})
    environment[1].calls.clear()
    with pytest.raises(ValueError, match="unresolved"):
        retained_job.cycle(root, environment[3], ENDPOINT, TOKEN)
    assert all(s["userId"] != "SYNTHETIC-stable-0" for s in pilot_job.sellers_in(root))


def test_receiver_bounded_opt_in_and_preserves_original(setup):
    _, environment, _, _, _, _ = setup
    matcher = environment[1]
    with sqlite3.connect(matcher.registration) as db:
        original = db.execute("SELECT * FROM cohort").fetchall()
    for i in range(100):
        matcher.operation(
            {
                "action": "register_retained",
                "sellers": [{"userId": f"SYNTHETIC-{i}", "username": f"SYNTHETIC-{i}"}],
            }
        )
    with pytest.raises(ValueError, match="capacity"):
        matcher.operation(
            {"action": "register_retained", "sellers": [{"userId": "EXCESS", "username": "EXCESS"}]}
        )
    assert len(matcher.operation({"action": "check_retained"})["retained"]) == 100
    with sqlite3.connect(matcher.registration) as db:
        assert db.execute("SELECT * FROM cohort").fetchall() == original
    assert b"SYNTHETIC-99" not in matcher.registration.read_bytes()


def test_total_limit_rollover_expiry_and_backwards_clock(setup):
    root, _, now, _, _, _ = setup
    start = now[0]
    for day in range(7):
        for run_index in range(4):
            now[0] = start + day * 86400 + run_index * 3600
            budget = Budget(root / "pilot.db", lambda: now[0])
            assert budget.begin() is not None
            for kind in [
                "oauth",
                "search",
                "detail",
                "detail",
                "detail",
                "image",
                "image",
                "image",
            ]:
                budget.reserve(kind)
            with pytest.raises(ValueError):
                budget.reserve("image")
            budget.finish(True)
        assert status(root / "pilot.db")["daily"] == 32
    assert status(root / "pilot.db")["total"] == 224
    now[0] = start + 7 * 86400
    assert Budget(root / "pilot.db", lambda: now[0]).begin() is None
    now[0] = start - 86400
    with pytest.raises(ValueError, match="backwards"):
        Budget(root / "pilot.db", lambda: now[0]).begin()


@pytest.mark.parametrize("kind", ["userId", "username"])
def test_new_matching_or_ambiguous_seller_never_retained(setup, kind):
    root, environment, _, _, _, _ = setup
    value = "SYNTHETIC-stable-0" if kind == "userId" else "SYNTHETIC-user-0"
    environment[0].accept("preexisting-notice", {kind: digest(SECRET, kind, value)})
    with pytest.raises(ValueError, match="paused|unresolved"):
        run(setup)
    assert pilot_job.sellers_in(root) == [SELLER]
    assert len(list((root / "photos").iterdir())) == 1


def test_definite_old_deletion_processed_even_when_extension_outages(setup):
    root, environment, _, _, _, _ = setup
    environment[0].accept("original-delete", {"userId": digest(SECRET, "userId", SELLER["userId"])})
    environment[1].retained_enabled = False
    with pytest.raises(ValueError, match="unresolved"):
        retained_job.cycle(root, environment[3], ENDPOINT, TOKEN)
    assert pilot_job.sellers_in(root) == []
    assert json.loads((root / "job.json").read_text())["sellers"] == [None]


def test_oversized_image_stops_without_untracked_retention(setup):
    root, environment, now, _, connection, transport = setup

    def route(request):
        if request.url.host == "i.ebayimg.com":
            return httpx.Response(
                200,
                content=b"x" * (worker.MAX_FILE_BYTES + 1),
                headers={"Content-Type": "image/png"},
            )
        return transport.handler(request)

    with pytest.raises(ValueError, match="paused"):
        asyncio.run(
            worker.tick(
                root,
                connection,
                TOKEN,
                environment[3],
                clock=lambda: now[0],
                transport=httpx.MockTransport(route),
            )
        )
    assert pilot_job.sellers_in(root) == [SELLER]
    assert len(list((root / "photos").iterdir())) == 1
    assert status(root / "pilot.db")["total"] <= 8


@pytest.mark.parametrize("endpoint", [None, "", "http://receiver.invalid/pilot-deletion", "https://receiver.invalid/wrong"])
def test_receiver_configuration_required_before_enable(monkeypatch, capsys, endpoint):
    if endpoint is None:
        monkeypatch.delenv("PILOT_MATCH_ENDPOINT", raising=False)
    else:
        monkeypatch.setenv("PILOT_MATCH_ENDPOINT", endpoint)
    monkeypatch.setattr("sys.argv", ["buyer_worker", "--enable"])
    with pytest.raises(SystemExit) as stopped:
        worker.main()
    assert stopped.value.code == 2
    assert "Set PILOT_MATCH_ENDPOINT" in capsys.readouterr().err


def test_preview_needs_no_receiver_configuration(monkeypatch, capsys):
    monkeypatch.delenv("PILOT_MATCH_ENDPOINT", raising=False)
    monkeypatch.setattr("sys.argv", ["buyer_worker", "--preview"])
    worker.main()
    assert json.loads(capsys.readouterr().out) == worker.PREVIEW
