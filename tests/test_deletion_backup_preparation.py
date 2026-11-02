"""Local synthetic backups only; no cloud writes, owner data, restore or deployment."""

import importlib.util
import json
import os
import sqlite3
import subprocess
from pathlib import Path

import pytest

from radar.ebay_deletion.protocol import digest
from radar.ebay_deletion.store import Queue

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "deploy/ebay-deletion/backup-ledger.py"
spec = importlib.util.spec_from_file_location("backup_ledger_preparation", SCRIPT)
backup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backup)
SECRET = b"SYNTHETIC-BACKUP-KEY-000000000000000000000"


def ledger(path):
    queue = Queue(path)
    queue.bind(SECRET)
    queue.accept(
        digest(SECRET, "notification", "SYNTHETIC-first-event"),
        {"userId": digest(SECRET, "userId", "SYNTHETIC-first-account")},
    )
    return queue


def test_backup_captures_committed_wal_and_private_key_bound_manifest(tmp_path):
    queue = ledger(tmp_path / "source.db")
    destination = tmp_path / "new-backup"
    # Keep the writer open so committed events remain in WAL, not just the main DB.
    with sqlite3.connect(queue.path) as writer:
        assert writer.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute(
            "INSERT INTO events(id,identifiers) VALUES (?,?)",
            (
                digest(SECRET, "notification", "SYNTHETIC-second-event"),
                json.dumps({"userId": digest(SECRET, "userId", "SYNTHETIC-second-account")}),
            ),
        )
        writer.commit()
        assert Path(str(queue.path) + "-wal").stat().st_size > 0
        report = backup.backup_ledger(queue.path, destination, SECRET)
        assert report == {
            "event_count": 2,
            "receipt_count": 0,
            "key_bound": True,
            "integrity": "ok",
            "offline_copy_only": True,
            "current_receiver_proof": False,
        }
        assert len(Queue.existing(destination / "ledger.db").events()) == 2
    assert destination.stat().st_mode & 0o777 == 0o700
    for name in ("ledger.db", "manifest.json"):
        assert (destination / name).stat().st_mode & 0o777 == 0o600
    manifest = (destination / "manifest.json").read_text()
    assert SECRET.decode() not in manifest
    assert "SYNTHETIC" not in manifest
    assert "source.db" not in manifest
    assert backup.verify_backup(destination, SECRET) == report


def test_existing_destination_and_symlink_paths_are_never_replaced(tmp_path):
    queue = ledger(tmp_path / "source.db")
    existing = tmp_path / "existing"
    existing.mkdir()
    (existing / "keep").write_text("SYNTHETIC owner content")
    with pytest.raises(FileExistsError):
        backup.backup_ledger(queue.path, existing, SECRET)
    assert (existing / "keep").read_text() == "SYNTHETIC owner content"
    alias = tmp_path / "alias"
    alias.symlink_to(existing, target_is_directory=True)
    for target in (alias, alias / "new-backup"):
        with pytest.raises(OSError):
            backup.backup_ledger(queue.path, target, SECRET)
    assert not (existing / "new-backup").exists()
    source_link = tmp_path / "source-link.db"
    source_link.symlink_to(queue.path)
    with pytest.raises(ValueError):
        backup.backup_ledger(source_link, tmp_path / "not-created", SECRET)
    assert not (tmp_path / "not-created").exists()


def test_missing_wrong_key_corrupt_and_changed_backups_fail_closed(tmp_path):
    with pytest.raises((ValueError, OSError)):
        backup.backup_ledger(tmp_path / "missing.db", tmp_path / "missing-copy", SECRET)
    assert not (tmp_path / "missing.db").exists()
    queue = ledger(tmp_path / "source.db")
    with pytest.raises(ValueError):
        backup.backup_ledger(queue.path, tmp_path / "wrong-key", b"SYNTHETIC-WRONG-KEY" * 3)
    assert not (tmp_path / "wrong-key").exists()
    destination = tmp_path / "copy"
    backup.backup_ledger(queue.path, destination, SECRET)
    with pytest.raises(ValueError):
        backup.verify_backup(destination, b"SYNTHETIC-WRONG-KEY" * 3)
    with sqlite3.connect(destination / "ledger.db") as db:
        db.execute("DELETE FROM events")
    with pytest.raises(ValueError):
        backup.verify_backup(destination, SECRET)
    (destination / "ledger.db").write_bytes(b"SYNTHETIC corrupt backup")
    with pytest.raises(ValueError):
        backup.verify_backup(destination, SECRET)


def test_backup_cli_reports_only_safe_health_and_has_no_restore_command(tmp_path):
    queue = ledger(tmp_path / "source.db")
    # A caller directory containing a fake radar package must not supply imports.
    (tmp_path / "radar").mkdir()
    (tmp_path / "radar/__init__.py").write_text("raise RuntimeError('UNTRUSTED CWD IMPORT')")
    environment = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    environment["EBAY_DELETION_HMAC_KEY"] = SECRET.decode()
    result = subprocess.run(
        [
            str(ROOT / ".venv/bin/python"),
            str(SCRIPT),
            "backup",
            "--source",
            str(queue.path),
            "--destination",
            str(tmp_path / "copy"),
        ],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    report = json.loads(result.stdout)
    assert report["status"] == "verified"
    assert report["current_receiver_proof"] is False
    assert "SYNTHETIC" not in result.stdout + result.stderr
    assert str(queue.path) not in result.stdout + result.stderr
    # An old successful backup remains an offline artifact after newer acceptance.
    queue.accept(
        digest(SECRET, "notification", "SYNTHETIC-new-event"),
        {"userId": digest(SECRET, "userId", "SYNTHETIC-new-account")},
    )
    stale = backup.verify_backup(tmp_path / "copy", SECRET)
    assert stale["event_count"] == 1
    assert stale["current_receiver_proof"] is False


@pytest.mark.parametrize(
    ("relative_path", "mode"),
    [
        ("ledger.db", 0o640),
        ("ledger.db", 0o644),
        ("manifest.json", 0o640),
        ("manifest.json", 0o644),
        (".", 0o750),
        (".", 0o755),
    ],
)
def test_verify_rejects_group_or_world_access(tmp_path, relative_path, mode):
    queue = ledger(tmp_path / "source.db")
    destination = tmp_path / "copy"
    backup.backup_ledger(queue.path, destination, SECRET)
    target = destination / relative_path
    original_mode = target.stat().st_mode & 0o777
    target.chmod(mode)
    with pytest.raises(ValueError, match="group/world access"):
        backup.verify_backup(destination, SECRET)
    target.chmod(original_mode)
    assert backup.verify_backup(destination, SECRET)["integrity"] == "ok"
