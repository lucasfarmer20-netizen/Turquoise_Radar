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
VERSION_3 = [
    "CREATE TABLE discovery_groups (id VARCHAR PRIMARY KEY, data JSON NOT NULL)",
    """CREATE TABLE discovery_runs (
        id INTEGER PRIMARY KEY, mode VARCHAR NOT NULL, status VARCHAR NOT NULL,
        options JSON NOT NULL, started_at DATETIME NOT NULL, completed_at DATETIME,
        new_count INTEGER NOT NULL, duplicate_count INTEGER NOT NULL,
        invalid_count INTEGER NOT NULL, errors JSON NOT NULL)""",
    """CREATE TABLE search_executions (
        id INTEGER PRIMARY KEY, run_id INTEGER NOT NULL REFERENCES discovery_runs(id),
        source VARCHAR NOT NULL, mode VARCHAR NOT NULL, group_ids JSON NOT NULL,
        pattern_id VARCHAR REFERENCES search_patterns(id), original_query VARCHAR NOT NULL,
        transmitted_query VARCHAR NOT NULL, corrected_queries JSON NOT NULL,
        correction_state VARCHAR NOT NULL, parameters JSON NOT NULL,
        results_returned INTEGER NOT NULL, unique_listings INTEGER NOT NULL,
        new_count INTEGER NOT NULL, duplicate_count INTEGER NOT NULL, errors JSON NOT NULL,
        status VARCHAR NOT NULL, executed_at DATETIME NOT NULL)""",
    """CREATE TABLE discovered_items (
        id INTEGER PRIMARY KEY, identity VARCHAR NOT NULL UNIQUE, source VARCHAR NOT NULL,
        ebay_item_id VARCHAR NOT NULL, normalized JSON NOT NULL, original_data JSON NOT NULL,
        listing_id INTEGER REFERENCES listings(id), first_seen DATETIME NOT NULL,
        last_seen DATETIME NOT NULL)""",
    """CREATE TABLE discovery_observations (
        id INTEGER PRIMARY KEY, execution_id INTEGER NOT NULL REFERENCES search_executions(id),
        item_id INTEGER NOT NULL REFERENCES discovered_items(id), raw_data JSON NOT NULL,
        corrected_query VARCHAR, correction_state VARCHAR NOT NULL,
        observed_at DATETIME NOT NULL)""",
    "CREATE INDEX ix_discovery_obs_item ON discovery_observations(item_id)",
    "CREATE INDEX ix_search_execution_run ON search_executions(run_id)",
]
VERSION_4 = [
    "ALTER TABLE discovered_items ADD COLUMN inbox_status VARCHAR NOT NULL DEFAULT 'NEW'",
    """CREATE TABLE intake_sources (id VARCHAR PRIMARY KEY, data JSON NOT NULL,
        last_successful_import DATETIME)""",
    """CREATE TABLE alert_imports (id INTEGER PRIMARY KEY,
        source_id VARCHAR NOT NULL REFERENCES intake_sources(id), stats JSON NOT NULL,
        created_at DATETIME NOT NULL)""",
    """CREATE TABLE alert_messages (id INTEGER PRIMARY KEY,
        source_id VARCHAR NOT NULL REFERENCES intake_sources(id), digest VARCHAR NOT NULL UNIQUE,
        raw_b64 TEXT NOT NULL, safe_text TEXT NOT NULL, subject TEXT NOT NULL,
        message_type VARCHAR NOT NULL, data JSON NOT NULL, received_at DATETIME, sent_at DATETIME,
        imported_at DATETIME NOT NULL, last_imported_at DATETIME NOT NULL,
        import_count INTEGER NOT NULL, synthetic BOOLEAN NOT NULL)""",
    """CREATE TABLE alert_observations (id INTEGER PRIMARY KEY,
        message_id INTEGER NOT NULL REFERENCES alert_messages(id),
        item_id INTEGER NOT NULL REFERENCES discovered_items(id), data JSON NOT NULL,
        query_text TEXT, attribution_verified BOOLEAN NOT NULL, created_at DATETIME NOT NULL,
        UNIQUE(message_id,item_id))""",
    """CREATE TABLE alert_invoices (id INTEGER PRIMARY KEY,
        source_id VARCHAR NOT NULL REFERENCES intake_sources(id), identity VARCHAR NOT NULL UNIQUE,
        order_id VARCHAR NOT NULL, data JSON NOT NULL,
        listing_id INTEGER REFERENCES listings(id), confirmed_at DATETIME)""",
    """CREATE TABLE invoice_evidence (id INTEGER PRIMARY KEY,
        invoice_id INTEGER NOT NULL REFERENCES alert_invoices(id),
        message_id INTEGER NOT NULL REFERENCES alert_messages(id),
        UNIQUE(invoice_id,message_id))""",
    """CREATE TABLE saved_source_searches (id INTEGER PRIMARY KEY,
        source_id VARCHAR NOT NULL REFERENCES intake_sources(id), phrase TEXT NOT NULL,
        group_name VARCHAR NOT NULL, selected BOOLEAN NOT NULL, configured BOOLEAN NOT NULL,
        UNIQUE(source_id,phrase))""",
    "CREATE INDEX ix_alert_obs_item ON alert_observations(item_id)",
    "CREATE INDEX ix_alert_message_source ON alert_messages(source_id)",
]
VERSION_5 = [
    """CREATE TABLE vision_cases (id INTEGER PRIMARY KEY, owner_id VARCHAR NOT NULL,
        listing_id INTEGER UNIQUE REFERENCES listings(id),
        candidate_id INTEGER UNIQUE REFERENCES discovered_items(id), created_at DATETIME NOT NULL,
        CHECK (listing_id IS NOT NULL OR candidate_id IS NOT NULL))""",
    """CREATE TABLE vision_photos (id VARCHAR PRIMARY KEY,
        case_id INTEGER NOT NULL REFERENCES vision_cases(id), role VARCHAR NOT NULL,
        original_hash VARCHAR NOT NULL, content_hash VARCHAR NOT NULL, data JSON NOT NULL,
        active BOOLEAN NOT NULL, created_at DATETIME NOT NULL)""",
    """CREATE TABLE vision_runs (id INTEGER PRIMARY KEY,
        case_id INTEGER NOT NULL REFERENCES vision_cases(id), owner_id VARCHAR NOT NULL,
        provider VARCHAR NOT NULL, model VARCHAR NOT NULL, stage VARCHAR NOT NULL,
        status VARCHAR NOT NULL, cache_key VARCHAR NOT NULL, active_key VARCHAR UNIQUE,
        reserved_usd VARCHAR NOT NULL, accounted_usd VARCHAR NOT NULL, estimated_usd VARCHAR,
        confirmed_usd VARCHAR, cost_status VARCHAR NOT NULL, data JSON NOT NULL,
        created_at DATETIME NOT NULL, completed_at DATETIME)""",
    "CREATE INDEX ix_vision_runs_cache_key ON vision_runs(cache_key)",
    "CREATE INDEX ix_vision_runs_budget ON vision_runs(owner_id, provider, created_at)",
    """CREATE TABLE vision_findings (id INTEGER PRIMARY KEY,
        run_id INTEGER NOT NULL REFERENCES vision_runs(id), data JSON NOT NULL)""",
    """CREATE TABLE vision_sub_items (id INTEGER PRIMARY KEY,
        run_id INTEGER NOT NULL REFERENCES vision_runs(id), data JSON NOT NULL)""",
    """CREATE TABLE vision_corrections (id INTEGER PRIMARY KEY,
        run_id INTEGER NOT NULL REFERENCES vision_runs(id), owner_id VARCHAR NOT NULL,
        data JSON NOT NULL, created_at DATETIME NOT NULL)""",
    """CREATE TABLE vision_prices (id INTEGER PRIMARY KEY, model VARCHAR NOT NULL,
        data JSON NOT NULL, created_at DATETIME NOT NULL)""",
    """CREATE TABLE vision_billing (id INTEGER PRIMARY KEY,
        run_id INTEGER NOT NULL REFERENCES vision_runs(id), owner_id VARCHAR NOT NULL,
        amount_usd VARCHAR NOT NULL, evidence TEXT NOT NULL, created_at DATETIME NOT NULL)""",
]
VERSION_6 = [
    """CREATE TABLE maker_cases (id INTEGER PRIMARY KEY,
        listing_id INTEGER REFERENCES listings(id), data JSON NOT NULL,
        created_at DATETIME NOT NULL)""",
    """CREATE TABLE maker_photos (id VARCHAR PRIMARY KEY,
        case_id INTEGER NOT NULL REFERENCES maker_cases(id), role VARCHAR NOT NULL,
        content_hash VARCHAR NOT NULL, data JSON NOT NULL, created_at DATETIME NOT NULL)""",
    """CREATE TABLE maker_runs (id INTEGER PRIMARY KEY,
        case_id INTEGER NOT NULL REFERENCES maker_cases(id), data JSON NOT NULL,
        created_at DATETIME NOT NULL)""",
    """CREATE TABLE maker_decisions (id INTEGER PRIMARY KEY,
        case_id INTEGER NOT NULL REFERENCES maker_cases(id),
        run_id INTEGER REFERENCES maker_runs(id), candidate_id VARCHAR, action VARCHAR NOT NULL,
        data JSON NOT NULL, created_at DATETIME NOT NULL)""",
    """CREATE TABLE maker_scenarios (id INTEGER PRIMARY KEY,
        case_id INTEGER NOT NULL REFERENCES maker_cases(id), data JSON NOT NULL,
        created_at DATETIME NOT NULL)""",
    "CREATE INDEX ix_maker_runs_case ON maker_runs(case_id)",
    "CREATE INDEX ix_maker_decisions_case ON maker_decisions(case_id)",
]
CURRENT_VERSION = 6


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
            if version < 3:
                for statement in VERSION_3:
                    conn.exec_driver_sql(statement)
            if version < 4:
                for statement in VERSION_4:
                    conn.exec_driver_sql(statement)
            if version < 5:
                for statement in VERSION_5:
                    conn.exec_driver_sql(statement)
            if version < 6:
                for statement in VERSION_6:
                    conn.exec_driver_sql(statement)
            conn.exec_driver_sql(f"PRAGMA user_version={CURRENT_VERSION}")
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
