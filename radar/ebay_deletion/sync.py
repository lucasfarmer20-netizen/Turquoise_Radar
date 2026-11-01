"""Merge a trusted SSH snapshot without allowing ledger rollback or key substitution."""

import argparse
import hashlib
import json
import os
import sqlite3
import time
from pathlib import Path

from radar.ebay_deletion.store import Conflict, Queue


def sync_ledger(snapshot, destination, secret):
    if not Path(snapshot).is_file():
        raise ValueError("Ledger snapshot unavailable")
    with sqlite3.connect(f"file:{Path(snapshot).resolve()}?mode=ro", uri=True) as remote:
        key = remote.execute("SELECT value FROM metadata WHERE key='hmac_key_id'").fetchone()
        if not key or key[0] != hashlib.sha256(secret).hexdigest():
            raise Conflict("Ledger key mismatch")
        events = dict(remote.execute("SELECT id,identifiers FROM events"))
    queue = Queue(destination)
    queue.bind(secret)
    with queue.connect() as local:
        for key, identifiers in local.execute("SELECT id,identifiers FROM events"):
            if events.get(key) != identifiers:
                raise Conflict("Remote ledger rollback or conflicting event")
        for key, identifiers in events.items():
            values = json.loads(identifiers)
            if (
                len(key) != 64
                or not isinstance(values, dict)
                or any(
                    k not in ("userId", "eiasToken", "username")
                    or not isinstance(v, str)
                    or len(v) != 64
                    for k, v in values.items()
                )
            ):
                raise Conflict("Invalid ledger snapshot")
            local.execute(
                "INSERT OR IGNORE INTO events(id,identifiers) VALUES (?,?)", (key, identifiers)
            )
        local.execute(
            "INSERT INTO metadata VALUES ('last_sync',?) ON CONFLICT(key) "
            "DO UPDATE SET value=excluded.value",
            (str(time.time()),),
        )


def require_fresh(queue_path, maximum_age=900):
    with Queue(queue_path).connect() as db:
        row = db.execute("SELECT value FROM metadata WHERE key='last_sync'").fetchone()
        if not row or not 0 <= time.time() - float(row[0]) <= maximum_age:
            raise ValueError("Fresh private ledger synchronization required")


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
        print("Private deletion ledger synchronized")
    except (ValueError, OSError, sqlite3.Error):
        print("Ledger synchronization failed; keep workbench offline")
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()
