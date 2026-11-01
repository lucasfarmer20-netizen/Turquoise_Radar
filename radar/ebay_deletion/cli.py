"""Offline explicit deletion/replay. Keep app stopped until this gate succeeds."""

import argparse
import json
import os
import sqlite3
from pathlib import Path

from radar.ebay_deletion.processing import replay


def verified_restore(source, destination, manifest, queue, secret):
    """Restore only into a NEW, quarantined path. Never open it before replay succeeds."""
    destination = Path(destination)
    if destination.exists():
        raise ValueError("Restore destination must be new")
    payload = json.loads(Path(manifest).read_text())
    if not any(
        a["kind"] == "sqlite" and Path(a["path"]).resolve() == destination.resolve()
        for a in payload["assets"]
    ):
        raise ValueError("Restored copy must be in replay inventory")
    with sqlite3.connect(f"file:{Path(source).resolve()}?mode=ro", uri=True) as src:
        # Prevent silent replacement even if another process created the destination.
        with destination.open("xb"):
            pass
        with sqlite3.connect(destination) as dst:
            src.backup(dst)
            if dst.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("Restore integrity check failed")
    statuses = replay(manifest, queue, secret)
    if any(s.startswith("REVIEW_") for s in statuses):
        raise ValueError("Restored copy remains quarantined: unresolved removal coverage")
    return statuses


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["replay", "restore", "status"])
    parser.add_argument("--manifest")
    parser.add_argument("--queue", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--require-fresh-ledger", action="store_true")
    parser.add_argument("--source")
    parser.add_argument("--destination")
    args = parser.parse_args()
    if args.command == "status":
        try:
            if not Path(args.queue).is_file():
                raise ValueError("Ledger unavailable")
            with sqlite3.connect(f"file:{Path(args.queue).resolve()}?mode=ro", uri=True) as db:
                accepted = db.execute("SELECT count(*) FROM events").fetchone()[0]
                statuses = dict(db.execute("SELECT status,count(*) FROM receipts GROUP BY status"))
            print(json.dumps({"accepted_events": accepted, "asset_receipts": statuses}))
            return
        except (ValueError, OSError, sqlite3.Error):
            print("Deletion status unavailable")
            raise SystemExit(2) from None
    if not args.manifest:
        parser.error("--manifest is required for maintenance")
    if args.command == "restore" and not (args.source and args.destination):
        parser.error("restore requires --source and --destination")
    if not args.apply:
        parser.error(
            "Explicit --apply is required; never run against owner data during preparation"
        )
    secret = os.environ.get("EBAY_DELETION_HMAC_KEY", "").encode()
    if len(secret) < 32:
        parser.error("EBAY_DELETION_HMAC_KEY must be configured")
    try:
        if args.require_fresh_ledger:
            from radar.ebay_deletion.sync import require_fresh

            require_fresh(args.queue)
        statuses = (
            verified_restore(args.source, args.destination, args.manifest, args.queue, secret)
            if args.command == "restore" and args.source and args.destination
            else replay(args.manifest, args.queue, secret)
        )
        print(
            json.dumps(
                {
                    "assets_checked": len(statuses),
                    "review_required": sum(s.startswith("REVIEW_") for s in statuses),
                }
            )
        )
        if any(s.startswith("REVIEW_") for s in statuses):
            raise SystemExit(2)
    except (ValueError, OSError, sqlite3.Error):
        # Fixed diagnostics only; file/identity values and DB errors stay private.
        print("Deletion maintenance incomplete; keep workbench/restore quarantined")
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()
