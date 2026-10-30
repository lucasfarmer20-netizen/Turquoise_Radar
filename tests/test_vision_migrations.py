import json
import sqlite3
from pathlib import Path

import pytest
from sqlalchemy.exc import OperationalError
from test_intake_migrations import phase3

from radar.db import database
from radar.migrations import VERSION_4


def populated_v4(path):
    phase3(path)
    with sqlite3.connect(path) as db:
        for statement in VERSION_4:
            db.execute(statement)
        db.execute("PRAGMA user_version=4")
        db.execute(
            "INSERT INTO intake_sources VALUES (?,?,?)", ("synthetic", '{"name":"SYNTHETIC"}', None)
        )
        db.execute(
            "INSERT INTO alert_imports VALUES (?,?,?,?)",
            (1, "synthetic", '{"new_candidates":1}', "2026-01-01"),
        )
        db.execute(
            "INSERT INTO alert_messages VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                1,
                "synthetic",
                "SYNTHETIC_DIGEST",
                "U1lOVEhFVElD",
                "SYNTHETIC escaped text",
                "SYNTHETIC payment receipt",
                "PAYMENT_RECEIVED",
                '{"order_id":"SYNTHETIC_ORDER"}',
                "2026-01-01",
                "2026-01-01",
                "2026-01-01",
                "2026-01-02",
                2,
                1,
            ),
        )
        db.execute(
            "INSERT INTO alert_observations VALUES (?,?,?,?,?,?,?)",
            (1, 1, 1, '{"evidence":"SYNTHETIC preserved"}', "nature america", 1, "2026-01-01"),
        )
        db.execute(
            "INSERT INTO alert_invoices VALUES (?,?,?,?,?,?,?)",
            (
                1,
                "synthetic",
                "SYNTHETIC_INVOICE",
                "SYNTHETIC_ORDER",
                '{"status":"RECONCILED"}',
                1,
                "2026-01-01",
            ),
        )
        db.execute("INSERT INTO invoice_evidence VALUES (?,?,?)", (1, 1, 1))
        db.execute(
            "INSERT INTO saved_source_searches VALUES (?,?,?,?,?,?)",
            (1, "synthetic", "nature america", "SYNTHETIC", 1, 1),
        )
        tables = [row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        before = {
            table: db.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()
            for table in tables
        }
    return before


def test_v5_preserves_every_populated_v4_table_and_repeat_is_noop(tmp_path):
    path = tmp_path / "v4.db"
    before = populated_v4(path)
    engine, _ = database(f"sqlite:///{path}")
    engine.dispose()
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 5
        for table, rows in before.items():
            assert db.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall() == rows
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {
            "vision_cases",
            "vision_photos",
            "vision_runs",
            "vision_findings",
            "vision_sub_items",
            "vision_corrections",
            "vision_prices",
            "vision_billing",
        } <= tables
        assert db.execute("SELECT count(*) FROM vision_runs").fetchone()[0] == 0
        assert all(
            "BLOB" not in str(r[2]).upper()
            for t in tables
            for r in db.execute(f"PRAGMA table_info({t})")
        )
    assert Path(str(path) + ".pre-v5.bak").exists()
    with sqlite3.connect(str(path) + ".pre-v5.bak") as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 4
    engine, _ = database(f"sqlite:///{path}")
    engine.dispose()


def test_v5_failure_rolls_back_populated_v4_and_ddl(tmp_path, monkeypatch):
    from radar import migrations

    path = tmp_path / "rollback.db"
    before = populated_v4(path)
    monkeypatch.setattr(migrations, "VERSION_5", [*migrations.VERSION_5, "INVALID MIGRATION"])
    with pytest.raises(OperationalError):
        database(f"sqlite:///{path}")
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 4
        assert not db.execute("SELECT name FROM sqlite_master WHERE name='vision_runs'").fetchone()
        for table, rows in before.items():
            assert db.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall() == rows


def test_v5_refuses_future_version_without_mutation(tmp_path):
    path = tmp_path / "future.db"
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA user_version=6")
        db.execute("CREATE TABLE future (data TEXT)")
        db.execute("INSERT INTO future VALUES (?)", (json.dumps({"SYNTHETIC": "preserve"}),))
    with pytest.raises(RuntimeError, match="newer"):
        database(f"sqlite:///{path}")
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 6
        assert db.execute("SELECT data FROM future").fetchone()[0] == '{"SYNTHETIC": "preserve"}'
