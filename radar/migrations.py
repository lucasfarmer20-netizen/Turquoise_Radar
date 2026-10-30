"""Small ordered SQLite migrations. DDL is transactional; never replace existing data."""

import sqlite3
from pathlib import Path

VERSION_1 = [
    """CREATE TABLE IF NOT EXISTS listings (
        id INTEGER PRIMARY KEY, dedup_key VARCHAR NOT NULL UNIQUE,
        data JSON NOT NULL, original_data JSON NOT NULL,
        first_seen DATETIME NOT NULL, last_seen DATETIME NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS search_patterns (
        id VARCHAR PRIMARY KEY, phrase VARCHAR NOT NULL UNIQUE, original_phrase VARCHAR NOT NULL,
        canonical_concept VARCHAR NOT NULL, anomaly_category VARCHAR NOT NULL,
        sources JSON NOT NULL,
        active BOOLEAN NOT NULL, priority_weight INTEGER NOT NULL, appearances INTEGER NOT NULL,
        useful_matches INTEGER NOT NULL, successful_purchases INTEGER NOT NULL,
        realized_profit VARCHAR NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS feedback (
        id INTEGER PRIMARY KEY, listing_id INTEGER NOT NULL REFERENCES listings(id),
        action VARCHAR NOT NULL, note TEXT NOT NULL, realized_profit VARCHAR,
        created_at DATETIME NOT NULL)""",
    "CREATE TABLE IF NOT EXISTS settings (id INTEGER PRIMARY KEY, data JSON NOT NULL)",
]
VERSION_2 = [
    "ALTER TABLE listings ADD COLUMN review_state VARCHAR NOT NULL DEFAULT 'NEW'",
    """UPDATE listings SET review_state = CASE
        (SELECT action FROM feedback WHERE feedback.listing_id=listings.id
            ORDER BY id DESC LIMIT 1)
        WHEN 'Purchased' THEN 'PURCHASED' WHEN 'Sold' THEN 'PURCHASED'
        WHEN 'Passed' THEN 'PASSED' WHEN 'False positive' THEN 'PASSED'
        WHEN 'Interested' THEN 'WATCH' ELSE 'NEW' END""",
    """CREATE TABLE comparable_sales (
        id INTEGER PRIMARY KEY, data JSON NOT NULL, listing_id INTEGER REFERENCES listings(id),
        created_at DATETIME NOT NULL)""",
    """CREATE TABLE listing_edits (
        id INTEGER PRIMARY KEY, listing_id INTEGER NOT NULL REFERENCES listings(id),
        before_data JSON NOT NULL, after_data JSON NOT NULL, created_at DATETIME NOT NULL)""",
]
CURRENT_VERSION = 2


def migrate(engine):
    path = engine.url.database
    with engine.connect() as conn:
        version = conn.exec_driver_sql("PRAGMA user_version").scalar_one()
        populated = conn.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='listings'"
        ).first()
    if version > CURRENT_VERSION:
        raise RuntimeError("Database is newer than this application; refusing downgrade")
    if version == CURRENT_VERSION:
        return
    if populated and path and path != ":memory:":
        backup = Path(str(path) + f".pre-v{CURRENT_VERSION}.bak")
        if not backup.exists():
            with sqlite3.connect(path) as src, sqlite3.connect(backup) as dest:
                src.backup(dest)
    with engine.connect() as conn:
        conn.exec_driver_sql("BEGIN IMMEDIATE")
        try:
            if version < 1:
                for statement in VERSION_1:
                    conn.exec_driver_sql(statement)
            if version < 2:
                for statement in VERSION_2:
                    conn.exec_driver_sql(statement)
            conn.exec_driver_sql(f"PRAGMA user_version={CURRENT_VERSION}")
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
