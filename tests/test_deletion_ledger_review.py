"""Review regressions: only synthetic local ledgers and mocked authenticated pulls."""

import hashlib
import json
import sqlite3
import time

import pytest

from radar.ebay_deletion import sync
from radar.ebay_deletion.cli import verified_restore
from radar.ebay_deletion.processing import replay
from radar.ebay_deletion.store import Queue

SECRET = b"synthetic-ledger-review-secret-0000000000000000"


def bound(path):
    ledger = Queue(path)
    ledger.bind(SECRET)
    return ledger


def notice(ledger, number):
    ledger.accept(
        hashlib.sha256(f"synthetic-notification-{number}".encode()).hexdigest(),
        {"userId": hashlib.sha256(f"synthetic-account-{number}".encode()).hexdigest()},
    )


def manifest(tmp_path, destination=None):
    path = tmp_path / "manifest.json"
    asset = destination or tmp_path / "independent.txt"
    if destination is None:
        asset.write_text("Synthetic independent owner evidence")
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "inventory_complete": True,
                "assets": [
                    {
                        "kind": "sqlite" if destination else "independent",
                        "path": str(asset),
                        "review_evidence": "Synthetic independent asset review",
                    }
                ],
            }
        )
    )
    return path


def test_missing_ledger_never_created_by_replay_or_restore(tmp_path):
    missing = tmp_path / "missing.db"
    with pytest.raises(ValueError):
        replay(manifest(tmp_path), missing, SECRET)
    assert not missing.exists()
    source, destination = tmp_path / "source.db", tmp_path / "restored.db"
    with sqlite3.connect(source) as db:
        db.execute("CREATE TABLE synthetic(value TEXT)")
    with pytest.raises(ValueError):
        verified_restore(source, destination, manifest(tmp_path, destination), missing, SECRET)
    assert not missing.exists() and not destination.exists()


def test_wrong_or_absent_key_fails_before_pull(tmp_path, monkeypatch):
    def forbidden(_):
        raise AssertionError("No pull before local key verification")

    monkeypatch.setattr(sync, "_pull_receiver", forbidden)
    ledger = bound(tmp_path / "local.db")
    with pytest.raises(ValueError):
        replay(manifest(tmp_path), ledger.path, b"wrong-key" * 8)
    unbound = Queue(tmp_path / "unbound.db")
    with pytest.raises(ValueError):
        replay(manifest(tmp_path), unbound.path, SECRET)


def test_offline_old_snapshot_reimport_cannot_refresh_maintenance(tmp_path, monkeypatch):
    remote = bound(tmp_path / "receiver.db")
    notice(remote, 1)
    local = tmp_path / "local.db"
    sync.sync_ledger(remote.path, local, SECRET)
    old = sync.receiver_bundle(remote.path, SECRET, "a" * 64)
    # Even a legacy timestamp and an import at the current time cannot certify freshness.
    with Queue.existing(local).connect() as db:
        db.execute("INSERT INTO metadata VALUES ('last_sync',?)", (str(time.time()),))
    sync.sync_ledger(remote.path, local, SECRET)
    with Queue.existing(local).read() as db:
        assert not db.execute("SELECT value FROM metadata WHERE key='last_sync'").fetchone()
    monkeypatch.setattr(sync, "_pull_receiver", lambda _: old)
    with pytest.raises(ValueError, match="proof invalid"):
        replay(manifest(tmp_path), local, SECRET)


def test_new_receiver_event_is_included_after_earlier_snapshot(tmp_path, monkeypatch):
    remote = bound(tmp_path / "receiver.db")
    notice(remote, 1)
    old_snapshot = tmp_path / "older.db"
    with remote.read() as src, sqlite3.connect(old_snapshot) as dst:
        src.backup(dst)
    local = tmp_path / "local.db"
    sync.sync_ledger(old_snapshot, local, SECRET)
    notice(remote, 2)
    sync.sync_ledger(old_snapshot, local, SECRET)
    assert len(Queue.existing(local).events()) == 1
    monkeypatch.setattr(
        sync, "_pull_receiver", lambda nonce: sync.receiver_bundle(remote.path, SECRET, nonce)
    )
    assert replay(manifest(tmp_path), local, SECRET) == ["INDEPENDENT", "INDEPENDENT"]
    assert len(Queue.existing(local).events()) == 2


def test_freshly_verified_empty_ledger_allows_restore(tmp_path, monkeypatch):
    remote = bound(tmp_path / "receiver.db")
    local = tmp_path / "local.db"
    sync.sync_ledger(remote.path, local, SECRET)
    pulls = []

    def current(nonce):
        pulls.append(nonce)
        return sync.receiver_bundle(remote.path, SECRET, nonce)

    monkeypatch.setattr(sync, "_pull_receiver", current)
    assert replay(manifest(tmp_path), local, SECRET) == []
    source, destination = tmp_path / "source.db", tmp_path / "restored.db"
    with sqlite3.connect(source) as db:
        db.execute("CREATE TABLE synthetic(value TEXT)")
        db.execute("INSERT INTO synthetic VALUES ('independent owner work')")
    assert (
        verified_restore(source, destination, manifest(tmp_path, destination), local, SECRET) == []
    )
    assert len(pulls) == 3 and len(set(pulls)) == 3
    with sqlite3.connect(destination) as db:
        assert db.execute("SELECT value FROM synthetic").fetchone()[0] == "independent owner work"


def test_recorded_current_proof_cannot_be_reused_for_next_operation(tmp_path, monkeypatch):
    remote = bound(tmp_path / "receiver.db")
    local = tmp_path / "local.db"
    sync.sync_ledger(remote.path, local, SECRET)
    captured = []

    def current(nonce):
        raw = sync.receiver_bundle(remote.path, SECRET, nonce)
        captured.append(raw)
        return raw

    monkeypatch.setattr(sync, "_pull_receiver", current)
    replay(manifest(tmp_path), local, SECRET)
    notice(remote, 1)
    monkeypatch.setattr(sync, "_pull_receiver", lambda _: captured[0])
    with pytest.raises(ValueError):
        replay(manifest(tmp_path), local, SECRET)
