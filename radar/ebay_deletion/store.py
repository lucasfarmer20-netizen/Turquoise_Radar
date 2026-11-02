"""Separate durable queue. No workbench connection, raw bodies or plaintext identities."""

import hashlib
import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path


class Conflict(ValueError):
    pass


class Queue:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.path.is_symlink():
            raise ValueError("Ledger must be a regular private file")
        if not self.path.exists():
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(fd)
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

    @classmethod
    def existing(cls, path):
        queue = cls.__new__(cls)
        queue.path = Path(path)
        if not queue.path.is_file() or queue.path.is_symlink():
            raise ValueError("Existing private deletion ledger required")
        return queue

    @contextmanager
    def read(self):
        if not self.path.is_file() or self.path.is_symlink():
            raise ValueError("Existing private deletion ledger required")
        db = sqlite3.connect(self.path.resolve().as_uri() + "?mode=ro", uri=True, timeout=5)
        try:
            yield db
        finally:
            db.close()

    def require_key(self, secret):
        if len(secret) < 32:
            raise Conflict("Deletion ledger key must be configured")
        with self.read() as db:
            row = db.execute("SELECT value FROM metadata WHERE key='hmac_key_id'").fetchone()
            if not row or row[0] != hashlib.sha256(secret).hexdigest():
                raise Conflict("Deletion ledger key mismatch or absent binding")

    @contextmanager
    def connect(self):
        if not self.path.is_file() or self.path.is_symlink():
            raise ValueError("Existing private deletion ledger required")
        db = sqlite3.connect(self.path.resolve().as_uri() + "?mode=rw", uri=True, timeout=5)
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
            # Reserve the writer before checking identity so concurrent deliveries cannot
            # both observe absence and acknowledge conflicting payloads.
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT identifiers FROM events WHERE id=?", (event_id,)).fetchone()
            if row and row[0] != encoded:
                raise Conflict("Conflicting delivery")
            db.execute(
                "INSERT OR IGNORE INTO events(id, identifiers) VALUES (?,?)", (event_id, encoded)
            )

    def events(self):
        with self.read() as db:
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
