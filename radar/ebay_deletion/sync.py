"""Offline imports never prove freshness; maintenance requires a fresh receiver pull."""

import argparse
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import subprocess
import time

from radar.ebay_deletion.store import Conflict, Queue

MAX_BUNDLE = 16 * 1024 * 1024
MAX_PULL_SECONDS = 30


def _events(db):
    return dict(db.execute("SELECT id,identifiers FROM events ORDER BY id"))


def _validate_events(events):
    if not isinstance(events, dict):
        raise Conflict("Invalid ledger events")
    for key, identifiers in events.items():
        if not isinstance(identifiers, str):
            raise Conflict("Invalid ledger snapshot")
        values = json.loads(identifiers)
        if (
            not isinstance(key, str)
            or not re.fullmatch(r"[a-f0-9]{64}", key)
            or not isinstance(values, dict)
            or not values
            or any(
                k not in ("userId", "eiasToken", "username")
                or not isinstance(v, str)
                or not re.fullmatch(r"[a-f0-9]{64}", v)
                for k, v in values.items()
            )
        ):
            raise Conflict("Invalid ledger snapshot")


def _merge(queue, events):
    _validate_events(events)
    with queue.connect() as local:
        for key, identifiers in _events(local).items():
            if events.get(key) != identifiers:
                raise Conflict("Remote ledger rollback or conflicting event")
        for key, identifiers in events.items():
            local.execute(
                "INSERT OR IGNORE INTO events(id,identifiers) VALUES (?,?)", (key, identifiers)
            )
        # Legacy timestamp metadata is not evidence of a current authenticated receiver pull.
        local.execute("DELETE FROM metadata WHERE key='last_sync'")


def sync_ledger(snapshot, destination, secret):
    """Explicit offline bootstrap/import only. Never creates a freshness assertion."""
    source = Queue.existing(snapshot)
    source.require_key(secret)
    with source.read() as remote:
        events = _events(remote)
    _validate_events(events)
    queue = Queue(destination)
    queue.bind(secret)
    _merge(queue, events)


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def receiver_bundle(queue_path, secret, nonce):
    """Forced SSH command: sign a consistent CURRENT event snapshot for this nonce."""
    if not re.fullmatch(r"[a-f0-9]{64}", nonce):
        raise ValueError("Invalid synchronization challenge")
    queue = Queue.existing(queue_path)
    queue.require_key(secret)
    with queue.read() as db:
        db.execute("BEGIN")
        events = _events(db)
    payload = {
        "version": 1,
        "nonce": nonce,
        "key_id": hashlib.sha256(secret).hexdigest(),
        "generated_at": int(time.time()),
        "events": events,
    }
    signature = hmac.new(secret, b"receiver-current-v1\0" + _canonical(payload), hashlib.sha256)
    bundle = _canonical({"payload": payload, "signature": signature.hexdigest()})
    if len(bundle) > MAX_BUNDLE:
        raise ValueError("Receiver ledger exceeds transfer limit")
    return bundle


def _pull_receiver(nonce):
    target = os.environ.get("EBAY_DELETION_SSH_TARGET", "")
    # SSH host alias from an operator-controlled config; no command/option injection.
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", target):
        raise ValueError("Configure the approved receiver SSH host alias")
    try:
        result = subprocess.run(
            ["ssh", "-T", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes", target],
            input=(nonce + "\n").encode(),
            capture_output=True,
            timeout=MAX_PULL_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise ValueError("Current receiver synchronization unavailable") from None
    if result.returncode or len(result.stdout) > MAX_BUNDLE:
        raise ValueError("Current receiver synchronization unavailable")
    return result.stdout


def maintenance_ledger(queue_path, secret):
    """Mandatory per-operation live barrier; missing/keyless ledgers never bootstrap here.

    Freshness is the receiver's consistent read during this challenge, not a promise
    that no later notification can arrive. Every subsequent maintenance invocation
    performs another challenge, so replaying a saved bundle cannot omit newer events.
    """
    queue = Queue.existing(queue_path)
    queue.require_key(secret)
    nonce = secrets.token_hex(32)
    started = time.monotonic()
    wall_started = time.time()
    raw = _pull_receiver(nonce)
    if len(raw) > MAX_BUNDLE or time.monotonic() - started > MAX_PULL_SECONDS:
        raise ValueError("Current receiver synchronization expired")
    try:
        envelope = json.loads(raw)
        payload, signature = envelope["payload"], envelope["signature"]
        expected = hmac.new(
            secret, b"receiver-current-v1\0" + _canonical(payload), hashlib.sha256
        ).hexdigest()
        if (
            not isinstance(signature, str)
            or not hmac.compare_digest(signature, expected)
            or payload["version"] != 1
            or payload["nonce"] != nonce
            or payload["key_id"] != hashlib.sha256(secret).hexdigest()
            or type(payload["generated_at"]) is not int
            or not wall_started - 30 <= payload["generated_at"] <= time.time() + 30
        ):
            raise ValueError()
        _merge(queue, payload["events"])
    except (KeyError, TypeError, ValueError):
        raise ValueError("Current receiver proof invalid") from None
    return queue


def require_fresh(queue_path, secret):
    """Compatibility name; current verification is always a new receiver challenge."""
    return maintenance_ledger(queue_path, secret)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot")
    parser.add_argument("destination")
    args = parser.parse_args()
    secret = os.environ.get("EBAY_DELETION_HMAC_KEY", "").encode()
    if len(secret) < 32:
        parser.error("Configure the deletion HMAC key")
    try:
        sync_ledger(args.snapshot, args.destination, secret)
        print("Offline ledger imported; maintenance still requires a fresh receiver pull")
    except (ValueError, OSError, sqlite3.Error):
        print("Ledger import failed; keep workbench offline")
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()
