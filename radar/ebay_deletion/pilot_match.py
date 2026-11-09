"""Optional single-cohort matcher. No routes or registration exist unless explicitly configured."""

import asyncio
import hashlib
import hmac
import json
import os
import sqlite3
import time
from collections import deque
from pathlib import Path

from fastapi import Request
from fastapi.responses import JSONResponse

from radar.ebay_deletion.identity import hashed_identity_state
from radar.ebay_deletion.protocol import digest


def cohort_hash(sellers):
    return hashlib.sha256(
        json.dumps(sellers, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def validate_sellers(value):
    if not isinstance(value, list) or not 1 <= len(value) <= 3:
        raise ValueError("Invalid cohort")
    for seller in value:
        if not isinstance(seller, dict) or set(seller) != {"userId", "username"}:
            raise ValueError("Stable seller evidence required")
        if any(not isinstance(v, str) or not v.strip() or len(v) > 256 for v in seller.values()):
            raise ValueError("Invalid identity")
    if len({s["userId"] for s in value}) != len(value):
        raise ValueError("Duplicate stable identity")
    return value


def initialize_registration(path):
    """Explicit one-time provisioning only; startup must never recreate a lost registry."""
    path = Path(path)
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError("Unsafe registration path")
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    os.close(fd)
    with sqlite3.connect(path) as db:
        db.execute(
            "CREATE TABLE cohort (id INTEGER PRIMARY KEY CHECK(id=1), "
            "digest TEXT NOT NULL, key_id TEXT NOT NULL, sellers TEXT NOT NULL)"
        )


class PilotMatcher:
    def __init__(self, queue, secret, token_sha256, registration, *, retained_enabled=False):
        if len(token_sha256) != 64 or any(c not in "0123456789abcdef" for c in token_sha256):
            raise ValueError("Configure a separate pilot credential digest")
        self.queue, self.secret, self.token_sha256 = queue, secret, token_sha256
        self.registration = Path(registration)
        if any(p.is_symlink() for p in (self.registration, *self.registration.parents)):
            raise ValueError("Unsafe registration path")
        self.retained_enabled = retained_enabled
        self.calls = deque()
        queue.require_key(secret)
        if not self.registration.is_file() or self.registration.stat().st_mode & 0o077:
            raise ValueError("Existing private registration required")
        with sqlite3.connect(self.registration.resolve().as_uri() + "?mode=rw", uri=True) as db:
            db.execute("SELECT digest,key_id,sellers FROM cohort WHERE id=1").fetchone()
            if retained_enabled:
                db.execute("SELECT handle,key_id,seller FROM retained_sellers LIMIT 1").fetchone()

    def operation(self, body):
        self.queue.require_key(self.secret)
        if body.get("action") in ("register_retained", "check_retained"):
            if not self.retained_enabled:
                raise ValueError("Retained registration disabled")
            return self.retained_operation(body)
        with sqlite3.connect(
            self.registration.resolve().as_uri() + "?mode=rw", uri=True, timeout=2
        ) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT digest,key_id,sellers FROM cohort WHERE id=1").fetchone()
            key_id = hashlib.sha256(self.secret).hexdigest()
            if body.get("action") == "enroll" and set(body) == {"action", "sellers"}:
                sellers = validate_sellers(body["sellers"])
                fingerprint = cohort_hash(sellers)
                hashed = [{k: digest(self.secret, k, v) for k, v in s.items()} for s in sellers]
                if row is None:
                    db.execute(
                        "INSERT INTO cohort VALUES (1,?,?,?)",
                        (fingerprint, key_id, json.dumps(hashed)),
                    )
                    row = fingerprint, key_id, json.dumps(hashed)
                elif row[:2] != (fingerprint, key_id):
                    raise ValueError("Cohort already enrolled")
            elif body.get("action") != "check" or set(body) != {"action", "cohort"}:
                raise ValueError("Invalid operation")
            if row is None or row[1] != key_id:
                raise ValueError("Enrollment unavailable")
            if body["action"] == "check" and body["cohort"] != row[0]:
                raise ValueError("Wrong cohort")
        # Accepted receiver events are the source; no remote pull or per-page proof.
        events = self.queue.events()
        states = []
        for seller in json.loads(row[2]):
            states.append(matching_state(seller, events))
        return {"cohort": row[0], "states": states}

    def retained_operation(self, body):
        """One opt-in bounded registry; the original cohort is never changed."""
        key_id = hashlib.sha256(self.secret).hexdigest()
        with sqlite3.connect(self.registration.resolve().as_uri() + "?mode=rw", uri=True) as db:
            db.execute("BEGIN IMMEDIATE")
            if body.get("action") == "register_retained" and set(body) == {"action", "sellers"}:
                requested = validate_sellers(body["sellers"])
                rows = [
                    (
                        cohort_hash([seller]),
                        key_id,
                        json.dumps({k: digest(self.secret, k, v) for k, v in seller.items()}),
                    )
                    for seller in requested
                ]
                existing = dict(db.execute("SELECT handle,key_id FROM retained_sellers"))
                if any(value != key_id for value in existing.values()):
                    raise ValueError("Registry key differs")
                if len(set(existing) | {row[0] for row in rows}) > 100:
                    raise ValueError("Retained seller capacity reached")
                db.executemany("INSERT OR IGNORE INTO retained_sellers VALUES (?,?,?)", rows)
            elif body != {"action": "check_retained"}:
                raise ValueError("Invalid retained operation")
            rows = db.execute("SELECT handle,key_id,seller FROM retained_sellers").fetchall()
            if len(rows) > 100 or any(row[1] != key_id for row in rows):
                raise ValueError("Invalid retained registry")
        events = self.queue.events()
        return {
            "retained": {
                handle: matching_state(json.loads(seller), events) for handle, _, seller in rows
            }
        }

    def install(self, app):
        @app.post("/pilot-deletion")
        async def match(request: Request):
            headers = {"Cache-Control": "no-store"}
            auth = request.headers.get("authorization", "")
            if (
                not 39 <= len(auth) <= 256
                or not auth.startswith("Bearer ")
                or not hmac.compare_digest(
                    hashlib.sha256(auth[7:].encode()).hexdigest(), self.token_sha256
                )
            ):
                return JSONResponse({"error": "Unauthorized"}, 401, headers=headers)
            now = time.monotonic()
            while self.calls and self.calls[0] <= now - 60:
                self.calls.popleft()
            if len(self.calls) >= 12:
                return JSONResponse({"error": "Try later"}, 429, headers=headers)
            self.calls.append(now)
            try:
                raw = b""
                async with asyncio.timeout(5):
                    async for chunk in request.stream():
                        raw += chunk
                        if len(raw) > 4096:
                            return JSONResponse({"error": "Body too large"}, 413, headers=headers)
                body = json.loads(raw)
                if not isinstance(body, dict):
                    raise ValueError()
                return JSONResponse(self.operation(body), headers=headers)
            except (ValueError, TypeError, KeyError, sqlite3.Error, OSError, TimeoutError):
                return JSONResponse({"error": "Pilot check unavailable"}, 409, headers=headers)


def initialize_retained_registration(path):
    """Explicit additive receiver provisioning, never implicit on app startup."""
    path = Path(path)
    if any(p.is_symlink() for p in (path, *path.parents)) or not path.is_file():
        raise ValueError("Existing registry required")
    with sqlite3.connect(path.resolve().as_uri() + "?mode=rw", uri=True) as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("SELECT digest,key_id,sellers FROM cohort WHERE id=1").fetchone()
        db.execute(
            "CREATE TABLE IF NOT EXISTS retained_sellers ("
            "handle TEXT PRIMARY KEY, key_id TEXT NOT NULL, seller TEXT NOT NULL)"
        )
        if [r[1] for r in db.execute("PRAGMA table_info(retained_sellers)")] != [
            "handle",
            "key_id",
            "seller",
        ]:
            raise ValueError("Unexpected retained registry schema")


def matching_state(seller, events):
    matches = [
        hashed_identity_state({("userId", seller["userId"])}, {seller["username"]}, identifiers, {})
        for _, identifiers in events
    ]
    return (
        "MATCH"
        if "MATCH" in matches
        else "HOLD"
        if any(value not in ("NONE", "MATCH") for value in matches)
        else "CLEAR"
    )
