"""Synthetic-only fresh-pilot orchestration: no credentials, hosts or eBay traffic."""

import asyncio
import hashlib
import hmac
import json
import sqlite3

import httpx
import pytest

from radar.discovery import offline_pilot as pilot
from radar.ebay_deletion import sync
from radar.ebay_deletion.protocol import digest
from radar.ebay_deletion.store import Queue


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    async def deny(*args, **kwargs):
        raise AssertionError("Real HTTP forbidden")

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", deny)
    monkeypatch.setattr(sync, "_pull_receiver", lambda _: pytest.fail("Real SSH forbidden"))


@pytest.fixture
def ready(tmp_path):
    root = tmp_path / "SYNTHETIC-pilot"
    preview = pilot.prepare(root, "stable-fixture")
    return root, preview["sha256"]


def run(ready, **kwargs):
    return asyncio.run(pilot.execute(*ready, **kwargs))


def receipt(root):
    return json.loads((root / "receipt.json").read_text())


def disabled(root):
    with sqlite3.connect(root / "pilot.db") as db:
        return (
            json.loads(db.execute("SELECT data FROM settings WHERE id=3").fetchone()[0])["enabled"]
            is False
        )


def test_full_mock_workflow_exact_limits_and_three_distinct_challenges(ready):
    root, _ = ready
    challenges, requests = [], []
    handler = pilot.mock_handler("stable-fixture")

    def pull(nonce):
        challenges.append(nonce)
        return sync.receiver_bundle(root / "receiver.db", pilot.SECRET, nonce)

    def respond(request):
        requests.append(request)
        return handler(request)

    result = run(ready, receiver_pull=pull, transport=httpx.MockTransport(respond))
    assert result["state"] == "QUALIFIED_SYNTHETIC_ONLY"
    assert not result["display_authorized"] and result["live_requests"] == result["images"] == 0
    assert len(set(challenges)) == 3
    assert [g["phase"] for g in result["gates"]] == [
        "pre_collection",
        "post_collection",
        "pre_display",
    ]
    assert [r.url.params.get("q") for r in requests[1:]] == list(pilot.QUERIES)
    assert len(requests) == result["mock_requests"] == 5
    assert all(r.headers.get("x-ebay-c-marketplace-id") == "EBAY_US" for r in requests[1:])
    assert all("99669" in r.headers.get("x-ebay-c-enduserctx", "") for r in requests[1:])
    with sqlite3.connect(root / "pilot.db") as db:
        assert db.execute("SELECT count(*) FROM discovered_items").fetchone()[0] == 8
        assert db.execute("SELECT count(*) FROM discovery_observations").fetchone()[0] == 12
        assert db.execute("SELECT count(*) FROM listings").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM vision_photos").fetchone()[0] == 0
    assert disabled(root)
    assert set(p.name for p in root.iterdir()) <= pilot.FILES
    assert all(p.stat().st_mode & 0o077 == 0 for p in root.iterdir())
    with pytest.raises(ValueError, match="already consumed"):
        run(ready)


def test_username_only_never_qualifies_even_with_empty_ledger(tmp_path):
    for empty in (False, True):
        root = tmp_path / str(empty)
        p = pilot.prepare(root)
        if empty:
            with sqlite3.connect(root / "receiver.db") as db:
                db.execute("DELETE FROM events")
        with pytest.raises(ValueError, match="review required|Stable identity"):
            run((root, p["sha256"]))
        assert receipt(root)["state"] == "QUARANTINED" and disabled(root)
        assert (root / "authorization-consumed.json").exists()


@pytest.mark.parametrize("failure", ["wrong_nonce", "stale", "bad_signature", "rollback"])
def test_bad_fresh_proof_prevents_any_dispatch(ready, failure):
    root, _ = ready
    old = sync.receiver_bundle(root / "receiver.db", pilot.SECRET, "0" * 64)
    if failure == "rollback":
        Queue.existing(root / "ledger.db").accept("b" * 64, {"userId": "c" * 64})

    def pull(nonce):
        if failure == "wrong_nonce":
            return old
        raw = sync.receiver_bundle(root / "receiver.db", pilot.SECRET, nonce)
        data = json.loads(raw)
        if failure == "stale":
            data["payload"]["generated_at"] = 1
            data["signature"] = hmac.new(
                pilot.SECRET,
                b"receiver-current-v1\0" + sync._canonical(data["payload"]),
                hashlib.sha256,
            ).hexdigest()
        if failure == "bad_signature":
            data["signature"] = "0" * 64
        return json.dumps(data).encode()

    with pytest.raises(ValueError):
        run(ready, receiver_pull=pull)
    assert receipt(root)["mock_requests"] == 0 and disabled(root)
    assert not (root / "authorization-consumed.json").exists()


@pytest.mark.parametrize("failure", ["missing", "keyless", "wrong_key"])
def test_existing_key_bound_ledger_is_mandatory(ready, failure):
    root, _ = ready
    (root / "ledger.db").unlink()
    if failure != "missing":
        queue = Queue(root / "ledger.db")
        if failure == "wrong_key":
            queue.bind(b"SYNTHETIC-WRONG-KEY" * 3)
    with pytest.raises(ValueError):
        run(ready)
    assert receipt(root)["mock_requests"] == 0
    if failure == "missing":
        assert not (root / "ledger.db").exists()


@pytest.mark.parametrize("change", ["inventory", "extra_copy", "database", "symlink"])
def test_changed_workspace_fails_before_collection(ready, change, tmp_path):
    root, _ = ready
    if change == "inventory":
        (root / "inventory.json").write_text("{}")
    elif change == "extra_copy":
        (root / "unregistered-backup.db").write_bytes((root / "pilot.db").read_bytes())
    elif change == "database":
        with sqlite3.connect(root / "pilot.db") as db:
            db.execute("UPDATE discovery_groups SET data='{}'")
    else:
        target = tmp_path / "untouched"
        target.write_text("SYNTHETIC sentinel")
        (root / "inventory.json").unlink()
        (root / "inventory.json").symlink_to(target)
    with pytest.raises(ValueError):
        run(ready)
    assert not (root / "authorization-consumed.json").exists()


def test_prepare_never_opens_existing_workspace(tmp_path):
    root = tmp_path / "existing"
    root.mkdir()
    old = root / "owner.db"
    old.write_bytes(b"SYNTHETIC sentinel for unrelated old data")
    before = hashlib.sha256(old.read_bytes()).hexdigest()
    with pytest.raises(FileExistsError):
        pilot.prepare(root)
    assert hashlib.sha256(old.read_bytes()).hexdigest() == before
    assert list(root.iterdir()) == [old]


def test_post_collection_new_deletion_is_applied_and_final_gate_is_fresh(ready):
    root, _ = ready
    calls = []

    def pull(nonce):
        calls.append(nonce)
        if len(calls) == 2:
            Queue.existing(root / "receiver.db").accept(
                "b" * 64, {"userId": digest(pilot.SECRET, "userId", "SYNTHETIC-account-0")}
            )
        if len(calls) == 3:
            raise ValueError("SYNTHETIC final receiver unavailable")
        return sync.receiver_bundle(root / "receiver.db", pilot.SECRET, nonce)

    with pytest.raises(ValueError, match="final receiver"):
        run(ready, receiver_pull=pull)
    assert len(calls) == 3 and receipt(root)["state"] == "QUARANTINED"
    with sqlite3.connect(root / "pilot.db") as db:
        rows = [json.loads(row[0]) for row in db.execute("SELECT normalized FROM discovered_items")]
        assert sum(row.get("raw_title") == "eBay account data removed" for row in rows) == 1
    assert disabled(root)


def test_interruption_consumes_authorization_and_disables_discovery(ready):
    root, _ = ready

    def interrupt(request):
        assert (root / "authorization-consumed.json").exists()
        raise KeyboardInterrupt("SYNTHETIC interruption")

    with pytest.raises(KeyboardInterrupt):
        run(ready, transport=httpx.MockTransport(interrupt))
    assert receipt(root)["state"] == "QUARANTINED" and disabled(root)
    with pytest.raises(ValueError, match="already consumed"):
        run(ready)


def test_live_transport_and_wrong_approval_rejected(ready):
    with pytest.raises(ValueError, match="MockTransport"):
        run(ready, transport=httpx.AsyncHTTPTransport())
    with pytest.raises(ValueError, match="approval"):
        run((ready[0], "0" * 64))
    assert not (ready[0] / "authorization-consumed.json").exists()


def test_retry_limit_stops_after_three_mock_oauth_attempts(ready):
    root, _ = ready
    with pytest.raises(ValueError, match="Incomplete discovery"):
        run(ready, transport=httpx.MockTransport(lambda _: httpx.Response(503)))
    result = receipt(root)
    assert result["mock_requests"] == result["mock_oauth_attempts"] == 3
    assert result["mock_browse_attempts"] == 0
    assert result["state"] == "QUARANTINED" and disabled(root)


def test_invalid_items_still_consume_twelve_observation_allowance(ready):
    root, _ = ready
    handler = pilot.mock_handler("stable-fixture")

    def respond(request):
        if request.method == "POST":
            return handler(request)
        return httpx.Response(200, json={"itemSummaries": [{}, {}, {}], "total": 100})

    with pytest.raises(ValueError, match="Incomplete discovery"):
        run(ready, transport=httpx.MockTransport(respond))
    with sqlite3.connect(root / "pilot.db") as db:
        assert db.execute("SELECT invalid_count FROM discovery_runs").fetchone()[0] == 12
        assert db.execute("SELECT count(*) FROM discovered_items").fetchone()[0] == 0
    assert receipt(root)["mock_browse_attempts"] == 4


def test_new_copy_during_collection_blocks_post_gate(ready):
    root, _ = ready
    handler = pilot.mock_handler("stable-fixture")

    def respond(request):
        extra = root / "unregistered-export.txt"
        extra.write_text("SYNTHETIC copied evidence")
        extra.chmod(0o600)
        return handler(request)

    with pytest.raises(ValueError, match="Uninventoried"):
        run(ready, transport=httpx.MockTransport(respond))
    assert receipt(root)["state"] == "QUARANTINED" and disabled(root)


@pytest.mark.parametrize("conflict", [False, True])
def test_empty_ledger_cannot_hide_missing_or_conflicting_observation_identity(ready, conflict):
    root, _ = ready
    with sqlite3.connect(root / "receiver.db") as db:
        db.execute("DELETE FROM events")
    handler = pilot.mock_handler("stable-fixture")

    def respond(request):
        response = handler(request)
        if request.url.params.get("q") == pilot.QUERIES[1]:
            body = response.json()
            seller = body["itemSummaries"][0]["seller"]
            if conflict:
                seller["userId"] = "SYNTHETIC-conflicting-identity"
            else:
                del seller["userId"]
            return httpx.Response(200, json=body)
        return response

    with pytest.raises(ValueError, match="identity evidence"):
        run(ready, transport=httpx.MockTransport(respond))
    assert receipt(root)["state"] == "QUARANTINED"


def test_concurrent_workspace_lock_prevents_dispatch(ready):
    import fcntl
    import os

    root, _ = ready
    fd = os.open(root / ".lock", os.O_CREAT | os.O_WRONLY, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(BlockingIOError):
            run(ready)
    finally:
        os.close(fd)
    assert not (root / "authorization-consumed.json").exists()
