"""Explicit pilot-only background maintenance and existing-feed supervisor.

No activation on import. No receiver HMAC key is transferred. The public local
selector below only adapts authenticated match results to existing sanitation.
"""

import argparse
import fcntl
import json
import os
import re
import signal
import socket
import sqlite3
import stat
import subprocess
import sys
import time
from urllib.parse import urlsplit

import httpx
from sqlalchemy import select

from radar.db import DiscoveredItem, DiscoveryObservation, Listing, database
from radar.discovery.identity_hydration import FIELD, retained_identity
from radar.discovery.image_pilot import _safe_path, _write_json
from radar.ebay_deletion.pilot_match import cohort_hash, validate_sellers
from radar.ebay_deletion.processing import sanitize_database
from radar.ebay_deletion.protocol import digest

SELECTOR = b"LOCAL-IDENTITY-SELECTOR-NOT-AUTHENTICATION"


def prepare(root):
    root = _safe_path(root)
    if "new-only-research" in root.parts:
        raise ValueError("Original application excluded")
    root.mkdir(mode=0o700, exist_ok=False)
    (root / "photos").mkdir(mode=0o700)
    engine, _ = database("sqlite:///" + str(root / "pilot.db"))
    engine.dispose()
    (root / "pilot.db").chmod(0o600)
    _write_json(root / "pilot-scope.json", {"version": 1, "root": str(root)}, exclusive=True)
    _write_json(
        root / "inventory.json",
        {
            "version": 1,
            "inventory_complete": True,
            "assets": [{"kind": "sqlite", "path": str(root / "pilot.db")}],
            "aliases": {},
            "photo_reviews": {},
        },
        exclusive=True,
    )
    return root


def inventory(root):
    root = _safe_path(root)
    if "new-only-research" in root.parts or root.stat().st_mode & 0o077:
        raise ValueError("Private separate pilot required")
    if json.loads((root / "pilot-scope.json").read_text()) != {"version": 1, "root": str(root)}:
        raise ValueError("Wrong pilot")
    if not (root / "pilot.db").is_file():
        raise ValueError("Existing pilot database required")
    allowed = {"pilot.db", "photos", "pilot-scope.json", "inventory.json", "job.json", ".job-lock"}
    if any(p.name not in allowed or p.is_symlink() for p in root.iterdir()):
        raise ValueError("Uninventoried pilot copy")
    value = json.loads((root / "inventory.json").read_text())
    if (
        value.get("inventory_complete") is not True
        or value.get("assets") != [{"kind": "sqlite", "path": str(root / "pilot.db")}]
        or value.get("aliases") != {}
    ):
        raise ValueError("Pilot inventory differs")
    paths = set()
    for review in value["photo_reviews"].values():
        path = _safe_path(review["path"])
        if review["kind"] != "seller_copy" or path.parent != root / "photos":
            raise ValueError("Photo outside pilot")
        paths.add(path)
    for path in root.rglob("*"):
        if path.is_symlink() or (path.is_file() and path.stat().st_nlink != 1):
            raise ValueError("Unsafe pilot copy")
    if any(not p.is_file() or p not in paths for p in (root / "photos").iterdir()):
        raise ValueError("Uninventoried photo")
    return value


def sellers_in(root):
    engine, sessions = database("sqlite:///" + str(root / "pilot.db"))
    sellers = {}
    try:
        with sessions() as db:
            values = list(db.scalars(select(DiscoveredItem.original_data)))
            values += list(db.scalars(select(DiscoveryObservation.raw_data)))
            values += list(db.scalars(select(Listing.original_data)))
            for raw in values:
                if not raw:  # Already sanitized rows have empty originals.
                    continue
                # promote() preserves Browse evidence in this exact envelope.
                # Do not search arbitrary nested keys or fall back from invalid evidence.
                if isinstance(raw, dict) and "provider" in raw:
                    if (
                        set(raw) != {"provider", "mode", "item"}
                        or raw["provider"] != "eBay Browse"
                        or raw["mode"] != "production"
                        or not isinstance(raw["item"], dict)
                    ):
                        raise ValueError("Unrecognized promoted identity envelope")
                    raw = raw["item"]
                seller = retained_identity(raw) if FIELD in raw else raw.get("seller")
                validate_sellers([seller])
                old = sellers.setdefault(seller["userId"], seller)
                if old != seller:
                    raise ValueError("Conflicting identity")
    finally:
        engine.dispose()
    return list(sellers.values())


def exchange(client, endpoint, token, body):
    url = urlsplit(endpoint)
    if (
        url.scheme != "https"
        or not url.hostname
        or url.username
        or url.password
        or url.query
        or url.fragment
        or url.path != "/pilot-deletion"
    ):
        raise ValueError("Exact HTTPS pilot operation required")
    with client.stream(
        "POST",
        endpoint,
        headers={"Authorization": "Bearer " + token},
        json=body,
        timeout=10,
        follow_redirects=False,
    ) as response:
        if response.status_code != 200:
            raise ValueError("Pilot maintenance unavailable")
        raw = b""
        for chunk in response.iter_bytes():
            raw += chunk
            cap = 16384 if body.get("action") in ("register_retained", "check_retained") else 4096
            if len(raw) > cap:
                raise ValueError("Oversized maintenance result")
    return json.loads(raw)


def enrolled_state(root, expected_cohort=None):
    """Read only: maintenance must never repair or infer enrollment."""
    fd = os.open(_safe_path(root) / "job.json", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError("Regular job state required")
        raw = stream.read(4097)
    if len(raw) > 4096:
        raise ValueError("Invalid job state size")
    state = json.loads(raw)
    if (
        not isinstance(state, dict)
        or set(state) != {"cohort", "sellers", "enrolled"}
        or state["enrolled"] is not True
        or not isinstance(state["cohort"], str)
        or not re.fullmatch(r"[0-9a-f]{64}", state["cohort"])
        or not isinstance(state["sellers"], list)
        or not 1 <= len(state["sellers"]) <= 3
        or (expected_cohort is not None and state["cohort"] != expected_cohort)
    ):
        raise ValueError("Existing enrolled pilot required")
    retained = [seller for seller in state["sellers"] if seller is not None]
    if retained:
        validate_sellers(retained)
    if all(seller is not None for seller in state["sellers"]):
        if cohort_hash(state["sellers"]) != state["cohort"]:
            raise ValueError("Cohort differs")
    return state


def cycle(root, client, endpoint, token, *, maintenance_only=False, expected_cohort=None):
    """Caller must stop its pilot process first; an error must never resume it."""
    root = _safe_path(root)
    manifest = inventory(root)
    current = sellers_in(root)
    state_path = root / "job.json"
    if maintenance_only or state_path.exists():
        state = (
            enrolled_state(root, expected_cohort)
            if maintenance_only
            else json.loads(state_path.read_text())
        )
        if any(s not in state["sellers"] for s in current):
            raise ValueError("Cohort changed; no automatic re-enrollment")
    else:
        validate_sellers(current)
        state = {"cohort": cohort_hash(current), "sellers": current, "enrolled": False}
        _write_json(state_path, state, exclusive=True)
    body = (
        {"action": "check", "cohort": state["cohort"]}
        if state["enrolled"]
        else {"action": "enroll", "sellers": state["sellers"]}
    )
    answer = exchange(client, endpoint, token, body)
    if (
        not isinstance(answer, dict)
        or set(answer) != {"cohort", "states"}
        or answer["cohort"] != state["cohort"]
        or not isinstance(answer["states"], list)
        or len(answer["states"]) != len(state["sellers"])
        or any(s not in ("CLEAR", "MATCH", "HOLD") for s in answer["states"])
    ):
        raise ValueError("Invalid maintenance result")
    state["enrolled"] = True
    _write_json(state_path, state)
    removed = 0
    for index, status in enumerate(answer["states"]):
        seller = state["sellers"][index]
        if status == "MATCH" and seller is not None:
            # This is local selection after authenticated HTTPS, NOT receiver proof.
            status, count = sanitize_database(
                root / "pilot.db",
                {"userId": digest(SELECTOR, "userId", seller["userId"])},
                SELECTOR,
                photo_reviews=manifest["photo_reviews"],
            )
            if status.startswith("REVIEW_"):
                raise ValueError("Deletion incomplete; keep pilot stopped")
            if any(s["userId"] == seller["userId"] for s in sellers_in(root)):
                raise ValueError("Retained identity remains; keep pilot stopped")
            state["sellers"][index] = None
            _write_json(state_path, state)
            removed += count
    if any(
        status == "HOLD" and seller is not None
        for status, seller in zip(answer["states"], state["sellers"], strict=True)
    ):
        raise ValueError("Identity review required; keep pilot stopped")
    return removed


def stop(process):
    if process is not None and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def run(root, endpoint, token, interval=300, *, maintenance_only=False):
    root = _safe_path(root)
    inventory(root)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 8001))
    fd = os.open(root / ".job-lock", os.O_CREAT | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    process = None
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        expected_cohort = enrolled_state(root)["cohort"] if maintenance_only else None
        env = dict(
            os.environ,
            RADAR_DATABASE_URL="sqlite:///" + str(root / "pilot.db"),
            RADAR_PILOT_VIEW_ONLY="true",
            VISION_IMAGE_DIR=str(root / "photos"),
            EBAY_SOURCE_ENABLED="false",
            EBAY_PRODUCTION_ENABLED="false",
            EBAY_PRODUCTION_APPROVED="false",
            VISION_LIVE_ENABLED="false",
        )
        for key in (
            "PILOT_MATCH_TOKEN",
            "EBAY_CLIENT_ID",
            "EBAY_CLIENT_SECRET",
            "ANTHROPIC_API_KEY",
        ):
            env.pop(key, None)
        with httpx.Client() as client:
            while True:
                stop(process)
                process = None
                try:
                    if maintenance_only:
                        cycle(
                            root,
                            client,
                            endpoint,
                            token,
                            maintenance_only=True,
                            expected_cohort=expected_cohort,
                        )
                    else:
                        cycle(root, client, endpoint, token)
                    process = subprocess.Popen(
                        [
                            sys.executable,
                            "-m",
                            "radar.ebay_deletion.pilot_server",
                            str(os.getpid()),
                        ],
                        env=env,
                    )
                except (ValueError, OSError, sqlite3.Error, httpx.HTTPError):
                    print("Pilot stopped: maintenance unavailable or review required", flush=True)
                time.sleep(interval)
    finally:
        stop(process)
        os.close(fd)


def shutdown_signal(signum, frame):
    raise KeyboardInterrupt()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["prepare", "run"])
    parser.add_argument("--root", required=True)
    parser.add_argument("--endpoint")
    parser.add_argument("--enable", action="store_true")
    parser.add_argument("--maintenance-only", action="store_true")
    args = parser.parse_args()
    if args.command == "prepare":
        prepare(args.root)
    elif args.enable and args.endpoint and os.environ.get("PILOT_MATCH_TOKEN"):
        signal.signal(signal.SIGTERM, shutdown_signal)
        signal.signal(signal.SIGHUP, shutdown_signal)
        run(
            args.root,
            args.endpoint,
            os.environ["PILOT_MATCH_TOKEN"],
            maintenance_only=args.maintenance_only,
        )
    else:
        parser.error("Run requires explicit --enable, --endpoint and PILOT_MATCH_TOKEN")


if __name__ == "__main__":
    main()
