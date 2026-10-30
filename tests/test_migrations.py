import json
import sqlite3
from pathlib import Path

import pytest
from sqlalchemy import select

from radar.db import Feedback, Listing, SearchPattern, Setting, database
from radar.migrations import VERSION_1


def phase001_database(path):
    with sqlite3.connect(path) as conn:
        for sql in VERSION_1:
            conn.execute(sql)
        data = json.loads((Path(__file__).parent.parent / "fixtures/demo.json").read_text())[0]
        # Genuine Phase 001 shape: no new evidence/status fields.
        for key in [
            "cost_status",
            "resale_confirmed",
            "resale_buyer_shipping",
            "resale_buyer_tax",
            "resale_shipping_status",
            "resale_tax_status",
        ]:
            data.pop(key, None)
        conn.execute(
            "INSERT INTO listings VALUES (?,?,?,?,?,?)",
            (
                1,
                "stable-id",
                json.dumps(data),
                json.dumps({"original": "unchanged"}),
                "2026-01-01",
                "2026-01-02",
            ),
        )
        conn.execute(
            "INSERT INTO settings VALUES (?,?)", (1, json.dumps({"owner_setting": "preserve"}))
        )
        conn.execute(
            "INSERT INTO feedback VALUES (?,?,?,?,?,?)",
            (1, 1, "Purchased", "old history", None, "2026-01-02"),
        )
        conn.execute(
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
                0,
                1,
                "25",
            ),
        )


def test_populated_phase001_copy_migrates_preserving_all_data(tmp_path):
    path = tmp_path / "phase001.db"
    phase001_database(path)
    with sqlite3.connect(path) as c:
        before = {
            table: c.execute(f"SELECT * FROM {table}").fetchall()
            for table in ("listings", "search_patterns", "settings", "feedback")
        }
    engine, sessions = database(f"sqlite:///{path}")
    with sessions() as s:
        row = s.get(Listing, 1)
        assert row.review_state == "PURCHASED"
        assert row.original_data == {"original": "unchanged"}
        assert row.data == json.loads(before["listings"][0][2])
        assert s.get(Setting, 1).data == {"owner_setting": "preserve"}
        assert s.get(SearchPattern, "p").realized_profit == "25"
        assert s.get(Feedback, 1).note == "old history"
    engine.dispose()
    with sqlite3.connect(path) as c:
        assert c.execute("PRAGMA user_version").fetchone()[0] == 2
        for table in ("search_patterns", "settings", "feedback"):
            assert c.execute(f"SELECT * FROM {table}").fetchall() == before[table]
    assert Path(str(path) + ".pre-v2.bak").exists()
    engine, _ = database(f"sqlite:///{path}")  # repeated migration is a no-op
    engine.dispose()


def test_fresh_database_migrates(tmp_path):
    path = tmp_path / "fresh.db"
    engine, sessions = database(f"sqlite:///{path}")
    with sessions() as s:
        assert list(s.scalars(select(Listing))) == []
    engine.dispose()
    with sqlite3.connect(path) as c:
        assert c.execute("PRAGMA user_version").fetchone()[0] == 2
        assert {"listing_edits", "comparable_sales"} <= {
            row[0] for row in c.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }


def test_future_schema_refused(tmp_path):
    path = tmp_path / "future.db"
    with sqlite3.connect(path) as c:
        c.execute("PRAGMA user_version=999")
    with pytest.raises(RuntimeError, match="newer"):
        database(f"sqlite:///{path}")


def test_failed_migration_rolls_back_structure_and_version(tmp_path, monkeypatch):
    from radar import migrations

    path = tmp_path / "rollback.db"
    phase001_database(path)
    monkeypatch.setattr(migrations, "VERSION_2", [*migrations.VERSION_2, "INVALID SQL STATEMENT"])
    from sqlalchemy.exc import OperationalError

    with pytest.raises(OperationalError):
        database(f"sqlite:///{path}")
    with sqlite3.connect(path) as c:
        assert c.execute("PRAGMA user_version").fetchone()[0] == 0
        assert "review_state" not in {r[1] for r in c.execute("PRAGMA table_info(listings)")}
        assert c.execute("SELECT count(*) FROM listings").fetchone()[0] == 1
        assert c.execute("SELECT count(*) FROM feedback").fetchone()[0] == 1


@pytest.mark.parametrize(
    "action,state", [("Passed", "PASSED"), ("Interested", "WATCH"), ("Sold", "PURCHASED")]
)
def test_migration_uses_latest_historical_decision(tmp_path, action, state):
    path = tmp_path / "history.db"
    phase001_database(path)
    with sqlite3.connect(path) as c:
        c.execute(
            "INSERT INTO feedback VALUES (?,?,?,?,?,?)",
            (2, 1, action, "newer decision", None, "2026-02-01"),
        )
    engine, sessions = database(f"sqlite:///{path}")
    with sessions() as s:
        assert s.get(Listing, 1).review_state == state
        assert len(list(s.scalars(select(Feedback)))) == 2
    engine.dispose()
