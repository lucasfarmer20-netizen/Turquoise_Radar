import json
import sqlite3
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from radar.db import DiscoveredItem, IntakeSource, database
from radar.migrations import CURRENT_VERSION, VERSION_1, VERSION_2, VERSION_3


def phase3(path):
    def insert(c, table, values):
        columns = ",".join(values)
        marks = ",".join("?" for _ in values)
        c.execute(f"INSERT INTO {table} ({columns}) VALUES ({marks})", tuple(values.values()))

    with sqlite3.connect(path) as c:
        for sql in [*VERSION_1, *VERSION_2, *VERSION_3]:
            c.execute(sql)
        c.execute("PRAGMA user_version=3")
        data = json.dumps(
            {
                "source": "eBay FIXTURE",
                "raw_title": "SYNTHETIC preserved record",
                "item_price": "18",
            }
        )
        insert(
            c,
            "listings",
            dict(
                id=1,
                dedup_key="original-key",
                data=data,
                original_data='{"original":"preserved"}',
                first_seen="2026-01-01",
                last_seen="2026-01-02",
                review_state="PURCHASED",
            ),
        )
        insert(
            c,
            "search_patterns",
            dict(
                id="p",
                phrase="nature america",
                original_phrase="Native American",
                canonical_concept="Native American",
                anomaly_category="autocorrect",
                sources="[]",
                active=1,
                priority_weight=100,
                appearances=1,
                useful_matches=1,
                successful_purchases=1,
                realized_profit="20",
            ),
        )
        insert(c, "settings", dict(id=1, data='{"owner":"preserve"}'))
        insert(
            c,
            "feedback",
            dict(
                id=1,
                listing_id=1,
                action="Purchased",
                note="Preserve owner note",
                realized_profit=None,
                created_at="2026-01-01",
            ),
        )
        insert(
            c,
            "comparable_sales",
            dict(id=1, data='{"sale_price":"50"}', listing_id=1, created_at="2026-01-01"),
        )
        insert(
            c,
            "listing_edits",
            dict(id=1, listing_id=1, before_data="{}", after_data=data, created_at="2026-01-01"),
        )
        insert(c, "discovery_groups", dict(id="old", data='{"owner":"group"}'))
        insert(
            c,
            "discovery_runs",
            dict(
                id=1,
                mode="fixture",
                status="COMPLETED",
                options="{}",
                started_at="2026-01-01",
                completed_at="2026-01-01",
                new_count=1,
                duplicate_count=0,
                invalid_count=0,
                errors="[]",
            ),
        )
        insert(
            c,
            "search_executions",
            dict(
                id=1,
                run_id=1,
                source="eBay FIXTURE",
                mode="fixture",
                group_ids='["old"]',
                pattern_id="p",
                original_query="Native American",
                transmitted_query="nature america",
                corrected_queries="[]",
                correction_state="UNKNOWN",
                parameters='{"q":"nature america"}',
                results_returned=1,
                unique_listings=1,
                new_count=1,
                duplicate_count=0,
                errors="[]",
                status="COMPLETED",
                executed_at="2026-01-01",
            ),
        )
        insert(
            c,
            "discovered_items",
            dict(
                id=1,
                identity="identity",
                source="eBay FIXTURE",
                ebay_item_id="v1|SYNTHETIC|0",
                normalized=data,
                original_data='{"item":"raw"}',
                listing_id=1,
                first_seen="2026-01-01",
                last_seen="2026-01-02",
            ),
        )
        insert(
            c,
            "discovery_observations",
            dict(
                id=1,
                execution_id=1,
                item_id=1,
                raw_data='{"evidence":"original"}',
                corrected_query=None,
                correction_state="UNKNOWN",
                observed_at="2026-01-01",
            ),
        )
        tables = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        columns = {t: [r[1] for r in c.execute(f"PRAGMA table_info({t})")] for t in tables}
        before = {t: c.execute(f"SELECT * FROM {t}").fetchall() for t in tables}
    return columns, before


def test_v4_preserves_all_populated_phase3_tables(tmp_path):
    path = tmp_path / "old.db"
    columns, before = phase3(path)
    engine, sessions = database(f"sqlite:///{path}")
    with sessions() as s:
        assert s.get(DiscoveredItem, 1).inbox_status == "NEW"
        assert not list(s.scalars(select(IntakeSource)))
    engine.dispose()
    with sqlite3.connect(path) as c:
        assert c.execute("PRAGMA user_version").fetchone()[0] == CURRENT_VERSION
        for t in before:
            names = ",".join(columns[t])
            assert c.execute(f"SELECT {names} FROM {t}").fetchall() == before[t]
    assert Path(str(path) + f".pre-v{CURRENT_VERSION}.bak").exists()
    engine, _ = database(f"sqlite:///{path}")
    engine.dispose()


def test_v4_failure_rolls_back_column_and_new_tables(tmp_path, monkeypatch):
    from radar import migrations

    path = tmp_path / "rollback.db"
    columns, before = phase3(path)
    monkeypatch.setattr(migrations, "VERSION_4", [*migrations.VERSION_4, "INVALID MIGRATION"])
    with pytest.raises(OperationalError):
        database(f"sqlite:///{path}")
    with sqlite3.connect(path) as c:
        assert c.execute("PRAGMA user_version").fetchone()[0] == 3
        assert "inbox_status" not in [
            r[1] for r in c.execute("PRAGMA table_info(discovered_items)")
        ]
        assert not c.execute(
            "SELECT name FROM sqlite_master WHERE name='alert_messages'"
        ).fetchone()
        for t in before:
            assert c.execute(f"SELECT * FROM {t}").fetchall() == before[t]
