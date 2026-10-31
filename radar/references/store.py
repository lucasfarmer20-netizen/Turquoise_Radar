"""Separate private SQLite database with ordered transactional migrations."""

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from radar.references.schema import Source

VERSION_1 = [
    "CREATE TABLE sources (id TEXT PRIMARY KEY, data TEXT NOT NULL)",
    "CREATE TABLE source_reviews (id INTEGER PRIMARY KEY, source TEXT, data TEXT, created_at TEXT)",
    "CREATE TABLE pairs (id INTEGER PRIMARY KEY, left_item TEXT REFERENCES items(id), "
    "right_item TEXT REFERENCES items(id), data TEXT NOT NULL)",
    """CREATE TABLE streams (id TEXT PRIMARY KEY, source TEXT NOT NULL REFERENCES sources(id),
       query TEXT NOT NULL, cursor TEXT NOT NULL, exhausted INTEGER NOT NULL DEFAULT 0)""",
    """CREATE TABLE queue (id INTEGER PRIMARY KEY, stream TEXT NOT NULL REFERENCES streams(id),
       external_id TEXT NOT NULL, payload TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'PENDING',
       reason TEXT, UNIQUE(stream,external_id))""",
    "CREATE INDEX queue_pending ON queue(stream,state,id)",
    """CREATE TABLE items (id TEXT PRIMARY KEY, source TEXT NOT NULL REFERENCES sources(id),
       external_id TEXT NOT NULL, data TEXT NOT NULL, excluded INTEGER NOT NULL DEFAULT 0,
       created_at TEXT NOT NULL, UNIQUE(source,external_id))""",
    """CREATE TABLE assets (id TEXT PRIMARY KEY, item_id TEXT NOT NULL REFERENCES items(id),
       url TEXT NOT NULL, data TEXT NOT NULL, original_hash TEXT NOT NULL,
       normalized_hash TEXT NOT NULL, dhash TEXT NOT NULL, removed INTEGER NOT NULL DEFAULT 0,
       UNIQUE(item_id,url))""",
    "CREATE INDEX asset_original ON assets(original_hash)",
    "CREATE INDEX asset_normalized ON assets(normalized_hash)",
    """CREATE TABLE hash_bands (band INTEGER, value TEXT, asset_id TEXT REFERENCES assets(id),
       PRIMARY KEY(band,value,asset_id))""",
    """CREATE TABLE duplicates (left_id TEXT, right_id TEXT, kind TEXT, distance INTEGER,
       PRIMARY KEY(left_id,right_id))""",
    """CREATE TABLE assertions (id INTEGER PRIMARY KEY, item_id TEXT REFERENCES items(id),
       data TEXT NOT NULL, fingerprint TEXT NOT NULL, created_at TEXT NOT NULL,
       UNIQUE(item_id,fingerprint))""",
    """CREATE TABLE marks (id INTEGER PRIMARY KEY, item_id TEXT REFERENCES items(id),
       data TEXT NOT NULL, created_at TEXT NOT NULL)""",
    """CREATE TABLE crops (id TEXT PRIMARY KEY, asset_id TEXT REFERENCES assets(id),
       data TEXT NOT NULL, removed INTEGER NOT NULL DEFAULT 0)""",
    """CREATE TABLE embeddings (id INTEGER PRIMARY KEY, asset_id TEXT REFERENCES assets(id),
       crop_id TEXT REFERENCES crops(id), model_revision TEXT NOT NULL, private_path TEXT NOT NULL,
       metadata TEXT NOT NULL)""",
    """CREATE TABLE batches (id INTEGER PRIMARY KEY, source TEXT REFERENCES sources(id),
       stream TEXT, started_at TEXT NOT NULL, finished_at TEXT, status TEXT, data TEXT NOT NULL)""",
    """CREATE TABLE audit (id INTEGER PRIMARY KEY, event TEXT NOT NULL, subject TEXT NOT NULL,
       data TEXT NOT NULL, created_at TEXT NOT NULL)""",
    """CREATE TABLE rate_state (host TEXT PRIMARY KEY, next_at REAL NOT NULL)""",
    """CREATE TABLE exports (id TEXT PRIMARY KEY, created_at TEXT NOT NULL, data TEXT NOT NULL)""",
]


def now():
    return datetime.now(UTC).isoformat()


def dump(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def private_root(root):
    root = Path(root).resolve()
    repo = Path(__file__).resolve().parents[2]
    # Private datasets may only live under ignored data/ or outside the checkout.
    if root.is_relative_to(repo) and not root.is_relative_to(repo / "data"):
        raise ValueError("Reference data must be outside source control and public static storage")
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    root.chmod(0o700)
    return root


def connect(root):
    root = private_root(root)
    path = root / "references.db"
    c = sqlite3.connect(path, timeout=30)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    c.execute("PRAGMA busy_timeout=30000")
    path.chmod(0o600)
    version = c.execute("PRAGMA user_version").fetchone()[0]
    tables = c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    if version > 1 or (version == 0 and tables):
        c.close()
        raise ValueError("Not a supported JRI database; refusing to replace existing tables")
    if version == 0:
        try:
            c.execute("BEGIN IMMEDIATE")
            for sql in VERSION_1:
                c.execute(sql)
            c.execute("PRAGMA user_version=1")
            c.commit()
        except BaseException:
            c.rollback()
            c.close()
            raise
    return c


@contextmanager
def exclusive(root):
    # OS lock is released on interruption; no stale lock file can authorize concurrent jobs.
    import fcntl

    root = private_root(root)
    fd = os.open(root / ".writer.lock", os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)


def audit(c, event, subject, **counts):
    # Internal event codes/identifiers and numeric counters only; never remote error text.
    if any(type(v) not in (int, bool, type(None)) for v in counts.values()):
        raise ValueError("Audit values must be safe counters")
    c.execute(
        "INSERT INTO audit(event,subject,data,created_at) VALUES (?,?,?,?)",
        (event, subject, dump(counts), now()),
    )


def put_source(c, source):
    source = Source.model_validate(source)
    old = c.execute("SELECT data FROM sources WHERE id=?", (source.id,)).fetchone()
    # Review history retains prior configuration privately, separate from safe event logs.
    if old and old["data"] != dump(source.model_dump(mode="json")):
        c.execute(
            "INSERT INTO source_reviews(source,data,created_at) VALUES (?,?,?)",
            (source.id, old["data"], now()),
        )
    c.execute(
        "INSERT INTO sources VALUES (?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data",
        (source.id, dump(source.model_dump(mode="json"))),
    )
    audit(c, "SOURCE_REVIEW", source.id)


def get_source(c, source_id):
    row = c.execute("SELECT data FROM sources WHERE id=?", (source_id,)).fetchone()
    if not row:
        raise ValueError("Unknown reference source")
    return Source.model_validate_json(row["data"])
