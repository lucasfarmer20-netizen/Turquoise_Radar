"""Separate durable queue. No workbench connection, raw bodies or plaintext identities."""

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path


class Conflict(ValueError):
    pass


class Queue:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY,value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS events (
                    id TEXT PRIMARY KEY, identifiers TEXT NOT NULL,
                    accepted_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
                CREATE TABLE IF NOT EXISTS receipts (
                    event_id TEXT NOT NULL, asset TEXT NOT NULL, status TEXT NOT NULL,
                    count INTEGER NOT NULL, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY(event_id, asset));
            """)
        self.path.chmod(0o600)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.execute("PRAGMA synchronous=FULL")
        db.execute("PRAGMA secure_delete=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def bind(self, secret):
        key_id = hashlib.sha256(secret).hexdigest()
        with self.connect() as db:
            row = db.execute("SELECT value FROM metadata WHERE key='hmac_key_id'").fetchone()
            if row and row[0] != key_id:
                raise Conflict("Deletion ledger key mismatch")
            db.execute("INSERT OR IGNORE INTO metadata VALUES ('hmac_key_id',?)", (key_id,))

    def accept(self, event_id, identifiers):
        encoded = json.dumps(identifiers, sort_keys=True)
        with self.connect() as db:
            row = db.execute("SELECT identifiers FROM events WHERE id=?", (event_id,)).fetchone()
            if row and row[0] != encoded:
                raise Conflict("Conflicting delivery")
            db.execute(
                "INSERT OR IGNORE INTO events(id, identifiers) VALUES (?,?)", (event_id, encoded)
            )

    def events(self):
        with self.connect() as db:
            return [
                (r[0], json.loads(r[1])) for r in db.execute("SELECT id,identifiers FROM events")
            ]

    def receipt(self, event_id, asset, status, count):
        with self.connect() as db:
            db.execute(
                "INSERT INTO receipts(event_id,asset,status,count) VALUES (?,?,?,?) "
                "ON CONFLICT(event_id,asset) DO UPDATE SET status=excluded.status, "
                "count=excluded.count,updated_at=CURRENT_TIMESTAMP",
                (event_id, asset, status, count),
            )
