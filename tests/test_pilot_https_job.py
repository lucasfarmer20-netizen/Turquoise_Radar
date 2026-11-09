"""Actual receiver and app schema; fixed dummy auth and mocked HTTPS only."""

import hashlib
import json
import sqlite3
from contextlib import nullcontext

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from test_source_image_deletion import ACCOUNT, source_database

from radar.db import DiscoveredItem, DiscoveryObservation, DiscoveryRun, SearchExecution, database
from radar.ebay_deletion.pilot_job import cycle, prepare
from radar.ebay_deletion.pilot_match import PilotMatcher, initialize_registration
from radar.ebay_deletion.protocol import digest
from radar.ebay_deletion.receiver import create_receiver
from radar.ebay_deletion.store import Queue
from radar.main import create_app

SECRET = b"SYNTHETIC-ledger-key-never-transferred-0000000"
TOKEN = "SYNTHETIC-pilot-token-not-production-0000000"
ENDPOINT = "https://receiver.example.invalid/pilot-deletion"
SELLER = {"userId": ACCOUNT, "username": "SYNTHETIC-SELLER"}


async def fixture_keys(_):
    return {}


@pytest.fixture
def env(tmp_path):
    queue = Queue(tmp_path / "receiver.db")
    queue.bind(SECRET)
    initialize_registration(tmp_path / "registration.db")
    matcher = PilotMatcher(
        queue, SECRET, hashlib.sha256(TOKEN.encode()).hexdigest(), tmp_path / "registration.db"
    )
    app = create_receiver(
        endpoint="https://receiver.example.invalid/ebay/account-deletion",
        token="SYNTHETIC-verification-token-00000000000",
        secret=SECRET,
        queue=queue,
        keys=fixture_keys,
        pilot_matcher=matcher,
    )
    with TestClient(app) as server:

        def route(request):
            response = server.post(
                request.url.path, content=request.content, headers=dict(request.headers)
            )
            return httpx.Response(response.status_code, content=response.content)

        with httpx.Client(transport=httpx.MockTransport(route)) as client:
            yield queue, matcher, server, client


def enroll(server, sellers=None, token=TOKEN):
    return server.post(
        "/pilot-deletion",
        headers={"Authorization": "Bearer " + token},
        json={"action": "enroll", "sellers": sellers or [SELLER]},
    )


def test_auth_defaults_and_enrollment_cannot_expand(env, tmp_path):
    queue, matcher, server, _ = env
    assert enroll(server, token="wrong").status_code == 401
    assert enroll(server).status_code == 200
    assert enroll(server).status_code == 200  # Safe retry after lost enrollment response.
    assert enroll(server, [{"userId": "OTHER", "username": "other"}]).status_code == 409
    restart = PilotMatcher(
        queue, SECRET, hashlib.sha256(TOKEN.encode()).hexdigest(), matcher.registration
    )
    with pytest.raises(ValueError):
        restart.operation({"action": "enroll", "sellers": [{"userId": "OTHER", "username": "x"}]})
    assert ACCOUNT.encode() not in matcher.registration.read_bytes()
    default = create_receiver(
        endpoint="https://receiver.example.invalid/ebay/account-deletion",
        token="SYNTHETIC-verification-token-00000000000",
        secret=SECRET,
        queue=queue,
        keys=fixture_keys,
    )
    with TestClient(default) as client:
        assert client.post("/pilot-deletion").status_code == 404


@pytest.mark.parametrize("sellers", [[{"username": "x"}], [{"userId": "x", "username": "y"}] * 4])
def test_reject_unbounded_or_username_only(env, sellers):
    assert enroll(env[2], sellers).status_code == 409


def test_body_limit_and_rate_limit(env):
    server = env[2]
    auth = {"Authorization": "Bearer " + TOKEN}
    assert server.post("/pilot-deletion", content=b"x" * 4097, headers=auth).status_code == 413
    for _ in range(11):
        enroll(server)
    assert enroll(server).status_code == 429


def pilot(tmp_path):
    root = prepare(tmp_path / "pilot")
    fixture = tmp_path / "fixture"
    fixture.mkdir()
    db, photo, reviews = source_database(fixture)
    # Move ONLY generated fixtures into the exclusively prepared pilot.
    db.replace(root / "pilot.db")
    new_photo = root / "photos" / photo.name
    photo.replace(new_photo)
    for review in reviews.values():
        review["path"] = str(new_photo)
    manifest = json.loads((root / "inventory.json").read_text())
    manifest["photo_reviews"] = reviews
    (root / "inventory.json").write_text(json.dumps(manifest))
    engine, sessions = database("sqlite:///" + str(root / "pilot.db"))
    with sessions.begin() as s:
        row = s.scalar(select(DiscoveredItem))
        row.original_data = {"seller": SELLER}
        run = DiscoveryRun(mode="production", options={}, status="COMPLETED")
        s.add(run)
        s.flush()
        execution = SearchExecution(
            run_id=run.id,
            source="eBay",
            mode="production",
            group_ids=[],
            original_query="SYNTHETIC ring",
            transmitted_query="SYNTHETIC ring",
            parameters={},
        )
        s.add(execution)
        s.flush()
        s.add(
            DiscoveryObservation(
                execution_id=execution.id,
                item_id=row.id,
                raw_data={"seller": SELLER},
                correction_state="NONE",
            )
        )
    engine.dispose()
    return root, new_photo


@pytest.mark.parametrize("maintenance_only", [False, True])
def test_new_notice_sanitizes_before_existing_app_resumes(
    env, tmp_path, monkeypatch, maintenance_only
):
    queue, _, server, client = env
    root, photo = pilot(tmp_path)
    assert cycle(root, client, ENDPOINT, TOKEN) == 0
    assert photo.exists()
    # The actual receiver handler accepts the notification; only cryptographic
    # verification/key lookup is stubbed here (covered independently elsewhere).
    import radar.ebay_deletion.receiver as receiver

    monkeypatch.setattr(receiver, "signature_header", lambda _: ("fixture", "fixture"))
    monkeypatch.setattr(receiver, "verify", lambda *args: None)
    body = {
        "metadata": {
            "topic": "MARKETPLACE_ACCOUNT_DELETION",
            "schemaVersion": "1.0",
            "deprecated": False,
        },
        "notification": {
            "notificationId": "SYNTHETIC-new-event",
            "eventDate": "2026-11-05T00:00:00Z",
            "publishDate": "2026-11-05T00:00:00Z",
            "publishAttemptCount": 1,
            "data": SELLER,
        },
    }
    assert server.post("/ebay/account-deletion", json=body).status_code == 204
    assert cycle(root, client, ENDPOINT, TOKEN, maintenance_only=maintenance_only) == 1
    assert not photo.exists()
    manifest_text = (root / "inventory.json").read_text()
    assert ACCOUNT not in manifest_text and SELLER["username"] not in manifest_text
    assert "https://" not in manifest_text
    assert all(
        set(review) == {"kind", "path", "evidence"}
        for review in json.loads(manifest_text)["photo_reviews"].values()
    )
    with sqlite3.connect(root / "pilot.db") as db:
        assert db.execute("SELECT count(*) FROM vision_photos").fetchone()[0] == 0
        assert db.execute("SELECT raw_data FROM discovery_observations").fetchone()[0] == "{}"
        assert db.execute("SELECT original_data FROM discovered_items").fetchone()[0] == "{}"
    assert ACCOUNT not in (root / "job.json").read_text()
    assert cycle(root, client, ENDPOINT, TOKEN) == 0
    monkeypatch.setenv("VISION_IMAGE_DIR", str(root / "photos"))
    with TestClient(create_app("sqlite:///" + str(root / "pilot.db"))) as feed:
        response = feed.get("/feed?dataset=real")
        assert response.status_code == 200
        assert "SYNTHETIC jewelry" not in response.text
        assert feed.get("/feed/photos/" + "f" * 32).status_code == 404


def test_outage_preserves_data_and_raises_before_resume(env, tmp_path):
    root, photo = pilot(tmp_path)
    with pytest.raises(ValueError):
        cycle(root, env[3], ENDPOINT, "wrong")
    assert photo.exists()


def test_unknown_copy_blocks_job(env, tmp_path):
    root, _ = pilot(tmp_path)
    (root / "unreviewed-backup.db").write_bytes(b"synthetic")
    with pytest.raises(ValueError, match="Uninventoried"):
        cycle(root, env[3], ENDPOINT, TOKEN)


def test_username_event_holds_instead_of_false_clear(env, tmp_path):
    root, photo = pilot(tmp_path)
    env[0].accept("b" * 64, {"username": digest(SECRET, "username", SELLER["username"])})
    with pytest.raises(ValueError, match="Identity review"):
        cycle(root, env[3], ENDPOINT, TOKEN)
    assert photo.exists()


def test_only_matching_seller_removed(env, tmp_path):
    root, photo = pilot(tmp_path)
    other = {"userId": "SYNTHETIC-OTHER", "username": "SYNTHETIC-OTHER-NAME"}
    engine, sessions = database("sqlite:///" + str(root / "pilot.db"))
    with sessions.begin() as db:
        first = db.scalar(select(DiscoveredItem))
        db.add(
            DiscoveredItem(
                identity="SYNTHETIC-OTHER-ITEM",
                ebay_item_id="SYNTHETIC-OTHER-ITEM",
                source="eBay",
                normalized=first.normalized,
                original_data={"seller": other},
            )
        )
    engine.dispose()
    cycle(root, env[3], ENDPOINT, TOKEN)
    env[0].accept("c" * 64, {"userId": digest(SECRET, "userId", ACCOUNT)})
    assert cycle(root, env[3], ENDPOINT, TOKEN) == 1
    with sqlite3.connect(root / "pilot.db") as db:
        raws = [json.loads(r[0]) for r in db.execute("SELECT original_data FROM discovered_items")]
    assert {"seller": other} in raws
    assert not photo.exists()


def test_retry_after_local_removal_interruption(env, tmp_path, monkeypatch):
    import radar.ebay_deletion.pilot_job as job

    root, photo = pilot(tmp_path)
    env[0].accept("d" * 64, {"userId": digest(SECRET, "userId", ACCOUNT)})
    original = job.sanitize_database

    def interrupted(*args, **kwargs):
        original(*args, **kwargs)
        raise OSError("SYNTHETIC interrupted after DB commit")

    monkeypatch.setattr(job, "sanitize_database", interrupted)
    with pytest.raises(OSError):
        cycle(root, env[3], ENDPOINT, TOKEN)
    assert not photo.exists()
    monkeypatch.setattr(job, "sanitize_database", original)
    assert cycle(root, env[3], ENDPOINT, TOKEN) == 0
    assert ACCOUNT not in (root / "job.json").read_text()


@pytest.mark.parametrize("failure", [ValueError, sqlite3.OperationalError])
def test_supervisor_does_not_resume_after_failed_check(tmp_path, monkeypatch, failure):
    import radar.ebay_deletion.pilot_job as job

    root = prepare(tmp_path / "pilot")
    actions = []

    class Process:
        def poll(self):
            return None

        def terminate(self):
            actions.append("stop")

        def wait(self, **kwargs):
            actions.append("wait")

    def launch(*args, **kwargs):
        actions.append("start")
        return Process()

    def checked(*args):
        actions.append("check")
        if actions.count("check") == 2:
            raise failure("SYNTHETIC unavailable")

    def sleep(_):
        if actions.count("check") == 2:
            raise KeyboardInterrupt()

    monkeypatch.setattr(job.subprocess, "Popen", launch)
    monkeypatch.setattr(job, "cycle", checked)
    monkeypatch.setattr(job.time, "sleep", sleep)
    with pytest.raises(KeyboardInterrupt):
        job.run(root, ENDPOINT, TOKEN)
    assert actions == ["check", "start", "stop", "wait", "check"]


def test_original_application_path_refused(tmp_path):
    with pytest.raises(ValueError):
        prepare(tmp_path / "new-only-research")


@pytest.mark.parametrize("state", [None, {"enrolled": False}, {"enrolled": True}])
def test_maintenance_refuses_missing_or_invalid_job_without_network(tmp_path, state):
    root = prepare(tmp_path / "pilot")
    if state is not None:
        (root / "job.json").write_text(json.dumps(state))
    before = (root / "job.json").read_bytes() if state is not None else None
    with pytest.raises((ValueError, FileNotFoundError)):
        cycle(root, None, ENDPOINT, TOKEN, maintenance_only=True)
    assert ((root / "job.json").read_bytes() if state is not None else None) == before
    assert (root / "job.json").exists() == (state is not None)


def test_maintenance_only_checks_and_preserves_markers(env, tmp_path, monkeypatch):
    import radar.ebay_deletion.pilot_job as job

    root, _ = pilot(tmp_path)
    cycle(root, env[3], ENDPOINT, TOKEN)  # Synthetic setup only.
    expected = job.enrolled_state(root)["cohort"]
    marker = tmp_path / "one-search-consumed.json"
    marker.write_bytes(b"SYNTHETIC consumed marker")
    original = job.exchange
    actions = []

    def checked(client, endpoint, token, body):
        actions.append(body["action"])
        assert body == {"action": "check", "cohort": expected}
        return original(client, endpoint, token, body)

    monkeypatch.setattr(job, "exchange", checked)
    assert (
        cycle(root, env[3], ENDPOINT, TOKEN, maintenance_only=True, expected_cohort=expected) == 0
    )
    assert actions == ["check"]

    assert marker.read_bytes() == b"SYNTHETIC consumed marker"
    state = json.loads((root / "job.json").read_text())
    state["enrolled"] = False
    (root / "job.json").write_text(json.dumps(state))
    with pytest.raises(ValueError):
        cycle(root, env[3], ENDPOINT, TOKEN, maintenance_only=True, expected_cohort=expected)
    assert actions == ["check"]


def test_maintenance_supervisor_stops_on_lost_job_and_drops_secrets(env, tmp_path, monkeypatch):
    import radar.ebay_deletion.pilot_job as job

    root, _ = pilot(tmp_path)
    cycle(root, env[3], ENDPOINT, TOKEN)
    actions = []

    class Process:
        def poll(self):
            return None

        def terminate(self):
            actions.append("stop")

        def wait(self, **kwargs):
            pass

    def launch(*args, **kwargs):
        for key in (
            "EBAY_CLIENT_ID",
            "EBAY_CLIENT_SECRET",
            "ANTHROPIC_API_KEY",
            "PILOT_MATCH_TOKEN",
        ):
            assert key not in kwargs["env"]
        actions.append("start")
        return Process()

    original = job.exchange

    def checked(client, endpoint, token, body):
        assert body["action"] == "check"
        actions.append("check")
        return original(client, endpoint, token, body)

    sleeps = 0

    def sleep(_):
        nonlocal sleeps
        sleeps += 1
        if sleeps == 1:
            (root / "job.json").unlink()
        else:
            raise KeyboardInterrupt()

    for key in ("EBAY_CLIENT_ID", "EBAY_CLIENT_SECRET", "ANTHROPIC_API_KEY", "PILOT_MATCH_TOKEN"):
        monkeypatch.setenv(key, "SYNTHETIC-secret")
    monkeypatch.setattr(job.httpx, "Client", lambda: nullcontext(env[3]))
    monkeypatch.setattr(job, "exchange", checked)
    monkeypatch.setattr(job.subprocess, "Popen", launch)
    monkeypatch.setattr(job.time, "sleep", sleep)
    with pytest.raises(KeyboardInterrupt):
        job.run(root, ENDPOINT, TOKEN, maintenance_only=True)
    assert actions == ["check", "start", "stop"]
    assert not (root / "job.json").exists()


def test_lost_registration_is_not_silently_reenrolled(env):
    queue, matcher, server, _ = env
    assert enroll(server).status_code == 200
    matcher.registration.unlink()
    with pytest.raises(ValueError):
        PilotMatcher(
            queue, SECRET, hashlib.sha256(TOKEN.encode()).hexdigest(), matcher.registration
        )


@pytest.mark.parametrize("maintenance_only", [False, True])
def test_definite_match_is_removed_and_durable_while_other_seller_holds(
    env, tmp_path, maintenance_only
):
    root, photo = pilot(tmp_path)
    other = {"userId": "SYNTHETIC-OTHER", "username": "SYNTHETIC-OTHER-NAME"}
    engine, sessions = database("sqlite:///" + str(root / "pilot.db"))
    with sessions.begin() as db:
        first = db.scalar(select(DiscoveredItem))
        db.add(
            DiscoveredItem(
                identity="SYNTHETIC-OTHER-ITEM",
                ebay_item_id="SYNTHETIC-OTHER-ITEM",
                source="eBay",
                normalized=first.normalized,
                original_data={"seller": other},
            )
        )
    engine.dispose()
    cycle(root, env[3], ENDPOINT, TOKEN)
    env[0].accept("e" * 64, {"userId": digest(SECRET, "userId", ACCOUNT)})
    # The other seller's username agrees but its stable ID conflicts: genuinely ambiguous.
    env[0].accept(
        "f" * 64,
        {
            "userId": digest(SECRET, "userId", "SYNTHETIC-THIRD"),
            "username": digest(SECRET, "username", other["username"]),
        },
    )
    for _ in range(2):  # A new cycle reloads durable state; HOLD must not undo removal.
        with pytest.raises(ValueError, match="Identity review"):
            cycle(root, env[3], ENDPOINT, TOKEN, maintenance_only=maintenance_only)
        state = json.loads((root / "job.json").read_text())
        assert state["sellers"] == [None, other]
        assert ACCOUNT not in (root / "job.json").read_text()
        assert not photo.exists()
        with sqlite3.connect(root / "pilot.db") as db:
            raws = [
                json.loads(r[0])
                for r in db.execute("SELECT original_data FROM discovered_items ORDER BY id")
            ]
            assert raws == [{}, {"seller": other}]
            assert db.execute("SELECT raw_data FROM discovery_observations").fetchone()[0] == "{}"
            assert db.execute("SELECT count(*) FROM vision_photos").fetchone()[0] == 0


def test_definite_match_wins_over_unrelated_incompatible_event(env, tmp_path):
    root, photo = pilot(tmp_path)
    env[0].accept("a" * 64, {"userId": digest(SECRET, "userId", ACCOUNT)})
    env[0].accept("b" * 64, {"eiasToken": digest(SECRET, "eiasToken", "SYNTHETIC-UNRELATED")})
    assert cycle(root, env[3], ENDPOINT, TOKEN) == 1
    assert not photo.exists()
    assert json.loads((root / "job.json").read_text())["sellers"] == [None]


def test_conflicting_stable_id_and_matching_username_without_match_still_holds(env, tmp_path):
    root, photo = pilot(tmp_path)
    env[0].accept(
        "a" * 64,
        {
            "userId": digest(SECRET, "userId", "SYNTHETIC-OTHER"),
            "username": digest(SECRET, "username", SELLER["username"]),
        },
    )
    with pytest.raises(ValueError, match="Identity review"):
        cycle(root, env[3], ENDPOINT, TOKEN)
    assert photo.exists()
    assert json.loads((root / "job.json").read_text())["sellers"] == [SELLER]


def test_already_removed_slot_does_not_hold_remaining_seller(env, tmp_path, monkeypatch):
    import radar.ebay_deletion.pilot_job as job

    root, photo = pilot(tmp_path)
    other = {"userId": "SYNTHETIC-OTHER", "username": "SYNTHETIC-OTHER-NAME"}
    engine, sessions = database("sqlite:///" + str(root / "pilot.db"))
    with sessions.begin() as db:
        first = db.scalar(select(DiscoveredItem))
        db.add(
            DiscoveredItem(
                identity="SYNTHETIC-OTHER-ITEM",
                ebay_item_id="SYNTHETIC-OTHER-ITEM",
                source="eBay",
                normalized=first.normalized,
                original_data={"seller": other},
            )
        )
    engine.dispose()
    env[0].accept("e" * 64, {"userId": digest(SECRET, "userId", ACCOUNT)})
    assert cycle(root, env[3], ENDPOINT, TOKEN) == 1
    state = json.loads((root / "job.json").read_text())
    assert state["sellers"] == [None, other]
    # An immutable registration still has a slot after local removal. Its later
    # HOLD must not re-block an already completed removal.
    monkeypatch.setattr(
        job, "exchange", lambda *args: {"cohort": state["cohort"], "states": ["HOLD", "CLEAR"]}
    )
    assert cycle(root, env[3], ENDPOINT, TOKEN) == 0
    assert not photo.exists()
