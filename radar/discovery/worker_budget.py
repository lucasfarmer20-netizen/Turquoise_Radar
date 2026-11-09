"""Durable attempt reservations in the existing database; no URLs or credentials."""

import json
import sqlite3
from datetime import UTC, datetime
from uuid import uuid4

SETTING_ID = 90
DAILY_LIMIT = 32
TOTAL_LIMIT = 224
RUN_LIMIT = 8
INTERVAL = 3600
QUERY = "blue stone ring"


def _read(db):
    row = db.execute("SELECT data FROM settings WHERE id=?", (SETTING_ID,)).fetchone()
    if not row:
        raise ValueError("Explicit worker activation required")
    value = json.loads(row[0])
    if value.get("schema") != "buyer-worker-1":
        raise ValueError("Unknown worker policy")
    return value


def _write(db, state):
    db.execute("UPDATE settings SET data=? WHERE id=?", (json.dumps(state), SETTING_ID))


def activate(path, now):
    """Explicit once-only activation; never reset counters or an existing policy."""
    with sqlite3.connect(path) as db:
        db.execute("BEGIN IMMEDIATE")
        old = db.execute("SELECT data FROM settings WHERE id=?", (SETTING_ID,)).fetchone()
        if old:
            state = _read(db)
            if state["status"] == "RUNNING":
                state["status"] = "INTERRUPTED"
                _write(db, state)
            return state
        state = dict(
            schema="buyer-worker-1",
            query=QUERY,
            expires=now + 7 * 86400,
            next_due=now,
            day="",
            daily=0,
            total=0,
            run_number=0,
            status="READY",
            paused=False,
            last_success=None,
            counts={},
        )
        db.execute("INSERT INTO settings(id,data) VALUES (?,?)", (SETTING_ID, json.dumps(state)))
        return state


def status(path):
    with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as db:
        return _read(db)


def _day(state, now):
    day = datetime.fromtimestamp(now, UTC).date().isoformat()
    if day < state["day"]:
        raise ValueError("Clock moved backwards")
    if day != state["day"]:
        state.update(day=day, daily=0)


class Budget:
    def __init__(self, path, clock):
        self.path, self.clock, self.run_id = path, clock, None

    def begin(self):
        with sqlite3.connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            state, now = _read(db), self.clock()
            _day(state, now)
            if (
                state["paused"]
                or now < state["next_due"]
                or now >= state["expires"]
                or state["daily"] + RUN_LIMIT > DAILY_LIMIT
                or state["total"] + RUN_LIMIT > TOTAL_LIMIT
            ):
                return None
            self.run_id = uuid4().hex
            state.update(
                run_id=self.run_id,
                status="RUNNING",
                next_due=now + INTERVAL,
                run_number=state["run_number"] + 1,
                counts={"oauth": 0, "search": 0, "detail": 0, "image": 0},
            )
            _write(db, state)
            return "AUCTION" if state["run_number"] % 2 else "FIXED_PRICE"

    def reserve(self, kind):
        with sqlite3.connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            state, now = _read(db), self.clock()
            _day(state, now)
            caps = {"oauth": 1, "search": 1, "detail": 3, "image": 3}
            if (
                not self.run_id
                or state.get("run_id") != self.run_id
                or state["status"] != "RUNNING"
                or now >= state["expires"]
                or kind not in caps
                or state["counts"][kind] >= caps[kind]
                or sum(state["counts"].values()) >= RUN_LIMIT
                or state["daily"] >= DAILY_LIMIT
                or state["total"] >= TOTAL_LIMIT
            ):
                raise ValueError("Request allowance exhausted")
            state.update(stage=kind.upper() + "_RESERVED", http_status=None, error_code=None)
            state["counts"][kind] += 1
            state["daily"] += 1
            state["total"] += 1
            _write(db, state)  # COMMIT precedes every network dispatch.

    def received(self, http_status, error_code=None):
        with sqlite3.connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            state = _read(db)
            if state.get("run_id") != self.run_id or state["status"] != "RUNNING":
                raise ValueError("Run changed")
            state.update(
                stage=state["stage"].replace("_RESERVED", "_RESPONSE"),
                http_status=http_status,
                error_code=error_code,
            )
            _write(db, state)

    def phase(self, name):
        with sqlite3.connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            state = _read(db)
            if state.get("run_id") != self.run_id or state["status"] != "RUNNING":
                raise ValueError("Run changed")
            state.update(stage=name, http_status=None, error_code=None)
            _write(db, state)

    def finish(self, success):
        with sqlite3.connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            state = _read(db)
            if state.get("run_id") != self.run_id:
                raise ValueError("Run changed")
            state.update(status="COMPLETED" if success else "PAUSED_ERROR", paused=not success)
            if success:
                state["last_success"] = self.clock()
            _write(db, state)


def projection(session):
    from radar.db import Setting

    row = session.get(Setting, SETTING_ID)
    if row is None or row.data.get("schema") != "buyer-worker-1":
        return None
    state = row.data
    return {
        key: state.get(key)
        for key in ("status", "day", "daily", "total", "stage", "http_status", "error_code")
    } | {
        "next_due": datetime.fromtimestamp(state["next_due"], UTC).isoformat(),
        "expires": datetime.fromtimestamp(state["expires"], UTC).isoformat(),
        "last_success": datetime.fromtimestamp(state["last_success"], UTC).isoformat()
        if state["last_success"]
        else "None yet",
    }
