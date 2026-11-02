"""Local-only consistent receiver-ledger backups; never upload, install or restore.

The destination must be new and private. The HMAC key is read from the environment
only for binding/integrity checks and is never copied into the backup manifest.
A verified backup is an offline copy, not a current-receiver proof. Keep recovered
copies quarantined and use the existing authenticated ledger/replay workflow;
this tool deliberately implements no restore or receiver activation command.
"""

import argparse
import hashlib
import json
import os
import sqlite3
import stat
import sys
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

# This script ships two levels below the reviewed installation root. Resolve imports
# from that trusted root, never the caller's working directory or PYTHONPATH.
PACKAGE_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PACKAGE_ROOT))

from radar.ebay_deletion.store import Queue  # noqa: E402
from radar.ebay_deletion.sync import _validate_events  # noqa: E402

SCHEMA = "ebay-receiver-ledger-backup-v1"


@contextmanager
def private_parent(path):
    """Pin the parent directory with no symlink traversal, including ancestor links."""
    path = Path(os.path.abspath(path))
    directory = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parent.parts[1:]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = child
        yield directory, path.name
    finally:
        os.close(directory)


def inspect_ledger(path, secret):
    if len(secret) < 32:
        raise ValueError("Configure the deletion HMAC key")
    with private_parent(path) as (parent, name):
        info = os.stat(name, dir_fd=parent, follow_symlinks=False)
        if not stat.S_ISREG(info.st_mode):
            raise ValueError("Ledger must be a regular private file")
    queue = Queue.existing(path)
    queue.require_key(secret)
    with queue.read() as db:
        db.execute("BEGIN")
        if db.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
            raise ValueError("Ledger integrity failed")
        if db.execute("PRAGMA foreign_key_check").fetchall():
            raise ValueError("Ledger foreign keys failed")
        events = dict(db.execute("SELECT id,identifiers FROM events"))
        _validate_events(events)
        receipts = db.execute("SELECT count(*) FROM receipts").fetchone()[0]
    return {"event_count": len(events), "receipt_count": receipts, "key_bound": True}


def file_hash(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def verify_backup(directory, secret):
    directory = Path(directory)
    with private_parent(directory) as (parent, name):
        info = os.stat(name, dir_fd=parent, follow_symlinks=False)
        if not stat.S_ISDIR(info.st_mode) or stat.S_IMODE(info.st_mode) & 0o077:
            raise ValueError("Backup must be a private directory without group/world access")
    # Validate both fixed filenames; the manifest cannot redirect reads elsewhere.
    for filename in ("ledger.db", "manifest.json"):
        path = directory / filename
        with private_parent(path) as (parent, name):
            info = os.stat(name, dir_fd=parent, follow_symlinks=False)
            if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) & 0o077:
                raise ValueError("Backup file must be private without group/world access")
    raw = (directory / "manifest.json").read_bytes()
    if len(raw) > 4096:
        raise ValueError("Invalid backup manifest")
    manifest = json.loads(raw)
    if (
        not isinstance(manifest, dict)
        or manifest.get("schema") != SCHEMA
        or manifest.get("database_sha256") != file_hash(directory / "ledger.db")
    ):
        raise ValueError("Backup integrity mismatch")
    report = inspect_ledger(directory / "ledger.db", secret)
    if any(manifest.get(key) != value for key, value in report.items()):
        raise ValueError("Backup metadata mismatch")
    return {
        **report,
        "integrity": "ok",
        "offline_copy_only": True,
        "current_receiver_proof": False,
    }


def backup_ledger(source, destination, secret):
    """Backup API captures committed WAL state; a verified copy is fsynced before success."""
    inspect_ledger(source, secret)
    destination = Path(destination)
    with private_parent(destination) as (parent, name):
        # Exclusive creation; existing paths, directories and symlinks cannot be replaced.
        os.mkdir(name, mode=0o700, dir_fd=parent)
        os.fsync(parent)
    target = destination / "ledger.db"
    # Failed operations leave only this new directory as an incomplete, unverified copy.
    with private_parent(target) as (parent, name):
        fd = os.open(
            name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent
        )
        os.close(fd)
    source_queue = Queue.existing(source)
    with source_queue.read() as src:
        with sqlite3.connect(target) as dst:
            dst.execute("PRAGMA synchronous=FULL")
            src.backup(dst)
    report = inspect_ledger(target, secret)
    manifest = {
        "schema": SCHEMA,
        "created_at": datetime.now(UTC).isoformat(),
        "database_sha256": file_hash(target),
        **report,
    }
    manifest_path = destination / "manifest.json"
    with private_parent(manifest_path) as (parent, name):
        fd = os.open(
            name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent
        )
        with os.fdopen(fd, "w") as stream:
            json.dump(manifest, stream, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        ledger_fd = os.open("ledger.db", os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent)
        try:
            os.fsync(ledger_fd)
        finally:
            os.close(ledger_fd)
        os.fsync(parent)
    return verify_backup(destination, secret)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["backup", "verify"])
    parser.add_argument("--source")
    parser.add_argument("--destination", required=True)
    args = parser.parse_args()
    if args.command == "backup" and not args.source:
        parser.error("backup requires --source")
    secret = os.environ.get("EBAY_DELETION_HMAC_KEY", "").encode()
    try:
        report = (
            backup_ledger(args.source, args.destination, secret)
            if args.command == "backup"
            else verify_backup(args.destination, secret)
        )
        print(json.dumps({"status": "verified", **report}, sort_keys=True))
    except (ValueError, OSError, sqlite3.Error):
        print(json.dumps({"status": "unverified", "error": "LEDGER_BACKUP_FAILED"}))
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()
