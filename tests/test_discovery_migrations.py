import json
import sqlite3
from pathlib import Path

from sqlalchemy import select

from radar.db import DiscoveryGroup, Listing, database
from radar.migrations import CURRENT_VERSION, VERSION_1, VERSION_2


def test_populated_phase002_schema_and_json_preserved(tmp_path):
    path = tmp_path / "phase002.db"
    with sqlite3.connect(path) as c:
        for sql in [*VERSION_1, *VERSION_2]:
            c.execute(sql)
        c.execute("PRAGMA user_version=2")
        item = {
            "source": "owner",
            "raw_title": "Owner data",
            "shipping_cost": "8",
            "pasted_text": "Original paste evidence",
            "resale_basis": "Owner evidence",
        }
        c.execute(
            "INSERT INTO listings VALUES (?,?,?,?,?,?,?)",
            (
                1,
                "stable",
                json.dumps(item),
                json.dumps({"original": "unaltered"}),
                "2026-01-01",
                "2026-01-02",
                "WATCH",
            ),
        )
        c.execute(
            "INSERT INTO settings VALUES (?,?)",
            (1, json.dumps({"owner_preferences": "preserve exactly"})),
        )
        c.execute(
            "INSERT INTO feedback VALUES (?,?,?,?,?,?)",
            (1, 1, "Interested", "old note", None, "2026-01-01"),
        )
        c.execute(
            "INSERT INTO comparable_sales VALUES (?,?,?,?)",
            (1, json.dumps({"sale_price": "50", "evidence": "old sale"}), None, "2026-01-01"),
        )
        c.execute(
            "INSERT INTO listing_edits VALUES (?,?,?,?,?)",
            (1, 1, json.dumps({"before": "old"}), json.dumps({"after": "new"}), "2026-01-02"),
        )
        c.execute(
            "INSERT INTO search_patterns VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "p",
                "nature america",
                "Native American",
                "Native American",
                "autocorrect",
                "[]",
                1,
                100,
                1,
                1,
                0,
                "0",
            ),
        )
        tables = [
            "listings",
            "search_patterns",
            "settings",
            "feedback",
            "comparable_sales",
            "listing_edits",
        ]
        before = {table: c.execute(f"SELECT * FROM {table}").fetchall() for table in tables}
    engine, sessions = database(f"sqlite:///{path}")
    with sessions() as s:
        assert s.get(Listing, 1).review_state == "WATCH"
        assert not list(s.scalars(select(DiscoveryGroup)))  # schema migration does not seed data
    engine.dispose()
    with sqlite3.connect(path) as c:
        assert c.execute("PRAGMA user_version").fetchone()[0] == CURRENT_VERSION
        for table in tables:
            assert c.execute(f"SELECT * FROM {table}").fetchall() == before[table]
        tables_after = {
            row[0] for row in c.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert {
            "discovery_groups",
            "discovery_runs",
            "discovery_observations",
            "search_executions",
            "discovered_items",
        } <= tables_after
    assert Path(str(path) + f".pre-v{CURRENT_VERSION}.bak").exists()
    engine, _ = database(f"sqlite:///{path}")
    engine.dispose()


def test_version3_failure_rolls_back_without_touching_phase2(tmp_path, monkeypatch):
    import pytest
    from sqlalchemy.exc import OperationalError

    from radar import migrations

    path = tmp_path / "rollback3.db"
    with sqlite3.connect(path) as c:
        for sql in [*VERSION_1, *VERSION_2]:
            c.execute(sql)
        c.execute("PRAGMA user_version=2")
        c.execute("INSERT INTO settings VALUES (1,?)", (json.dumps({"important": "owner data"}),))
    monkeypatch.setattr(migrations, "VERSION_3", [*migrations.VERSION_3, "INVALID SQL"])
    with pytest.raises(OperationalError):
        database(f"sqlite:///{path}")
    with sqlite3.connect(path) as c:
        assert c.execute("PRAGMA user_version").fetchone()[0] == 2
        assert c.execute("SELECT data FROM settings WHERE id=1").fetchone()[0] == json.dumps(
            {"important": "owner data"}
        )
        assert not c.execute(
            "SELECT name FROM sqlite_master WHERE name='discovery_runs'"
        ).fetchone()
