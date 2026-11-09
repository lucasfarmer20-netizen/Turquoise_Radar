"""Synthetic-only matching/enforcement slice. No SSH, network or environment credentials.

Channel MACs model SSH integrity using disposable test material, NOT a proposed
production signing service. The ledger key is used only by SyntheticRunner.
"""

import hashlib
import hmac
import io
import json
import os
import secrets
import sqlite3
import threading
import time
from pathlib import Path
from unittest.mock import patch

from PIL import Image
from pydantic import BaseModel, ConfigDict, Field

from radar.discovery.image_pilot import QUERIES
from radar.ebay_deletion import sync
from radar.ebay_deletion.processing import identity_state
from radar.ebay_deletion.protocol import digest
from radar.ebay_deletion.store import Queue

LIMIT = 65536
DEADLINE = 5


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def sha(value):
    return hashlib.sha256(canonical(value)).hexdigest()


class Identity(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    userId: str = Field(pattern=r"^SYNTHETIC-[A-Za-z0-9-]{1,80}$")
    username: str = Field(pattern=r"^SYNTHETIC-[A-Za-z0-9-]{1,80}$")


class Query(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    session: str = Field(pattern=r"^[a-f0-9]{64}$")
    nonce: str = Field(pattern=r"^[a-f0-9]{64}$")
    inventory: str = Field(pattern=r"^[a-f0-9]{64}$")
    plan: str = Field(pattern=r"^[a-f0-9]{64}$")
    identities: list[Identity] = Field(min_length=1, max_length=12)


def seal(key, direction, value):
    body = canonical(value)
    return canonical({"body": value, "mac": hmac.digest(key, direction + body, "sha256").hex()})


def unseal(key, direction, raw):
    if not isinstance(raw, bytes) or len(raw) > LIMIT:
        raise ValueError("Invalid channel frame")
    envelope = json.loads(raw)
    if set(envelope) != {"body", "mac"} or not hmac.compare_digest(
        envelope["mac"], hmac.digest(key, direction + canonical(envelope["body"]), "sha256").hex()
    ):
        raise ValueError("Channel integrity failure")
    return envelope["body"]


class SyntheticRunner:
    def __init__(self, root, channel_key):
        root.mkdir(mode=0o700)
        self.__key = secrets.token_bytes(32)  # Never returned to the local gate.
        self.__channel = channel_key
        self.receiver = root / "receiver.db"
        self.ledger = root / "ledger.db"
        for path in (self.receiver, self.ledger):
            Queue(path).bind(self.__key)
        self.seen = set()
        self.pull = lambda nonce: sync.receiver_bundle(self.receiver, self.__key, nonce)

    def notice(self, user="SYNTHETIC-account", field="userId"):
        if field not in ("userId", "username") or not user.startswith("SYNTHETIC-"):
            raise ValueError("Synthetic identities only")
        Queue.existing(self.receiver).accept(
            sha([field, user]), {field: digest(self.__key, field, user)}
        )

    def exchange(self, frame):
        value = unseal(self.__channel, b"request", frame)
        query = Query.model_validate(value)
        if query.nonce in self.seen or len(self.seen) >= 128:
            raise ValueError("Consumed challenge or session bound reached")
        self.seen.add(query.nonce)
        # Only the receiver transport is mocked: real nonce/HMAC/rollback checks run.
        with patch.object(sync, "_pull_receiver", self.pull):
            queue = sync.maintenance_ledger(self.ledger, self.__key)
        states = []
        for event in queue.events():
            identifiers = event[1]
            if isinstance(identifiers, str):
                identifiers = json.loads(identifiers)
            for identity in query.identities:
                states.append(
                    identity_state([{"seller": identity.model_dump()}], identifiers, self.__key, {})
                )
        result = (
            "MATCH"
            if "MATCH" in states
            else "HOLD"
            if any(state != "NONE" for state in states)
            else "CLEAR"
        )
        return seal(self.__channel, b"response", {"request": sha(value), "result": result})


class SyntheticPilot:
    """Owns only a newly created, closed synthetic cohort; no attach-to-existing API.

    Every read rechecks the channel and complete file snapshot under an exclusive
    application lock. No clearance survives calls, restart, failure or cleanup.
    """

    def __init__(self, root, channel_key, exchange, *, clock=time.monotonic):
        root = Path(root).absolute()
        if "new-only-research" in root.parts or any(p.is_symlink() for p in (root, *root.parents)):
            raise ValueError("Separate new synthetic root required")
        root.mkdir(mode=0o700)
        self.root, self.__channel, self.exchange = root, channel_key, exchange
        self.clock = clock
        self.session = secrets.token_hex(32)
        self.lock = threading.Lock()
        self.blocked = False
        self.identities = [{"userId": "SYNTHETIC-account", "username": "SYNTHETIC-seller"}]
        payload = canonical({"synthetic": True, "seller": self.identities[0]}).decode()
        with sqlite3.connect(root / "pilot.db") as db:
            db.execute("CREATE TABLE originals (payload TEXT)")
            db.execute("INSERT INTO originals VALUES (?)", (payload,))
            db.execute("CREATE TABLE observations (payload TEXT)")
            db.execute("INSERT INTO observations VALUES (?)", (payload,))
        (root / "backup.db").write_bytes((root / "pilot.db").read_bytes())
        (root / "export.json").write_text(payload)
        stream = io.BytesIO()
        Image.new("RGB", (32, 32), "turquoise").save(stream, "PNG")
        (root / "photo.png").write_bytes(stream.getvalue())
        (root / "photo-copy.png").write_bytes(stream.getvalue())
        for path in root.iterdir():
            path.chmod(0o600)
        self.cohort = sha(self.identities)
        self.expected = self.snapshot()
        self.plan = sha({"queries": list(QUERIES), "synthetic_only": True, "operation": "read"})

    def snapshot(self):
        result = {}
        for path in self.root.iterdir():
            stat = path.lstat()
            if path.is_symlink() or not path.is_file() or stat.st_nlink != 1:
                raise ValueError("Unsafe or untracked copy")
            result[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
        return result

    def _suppress(self):
        self.blocked = True  # Set before attempting any disk operation.
        with (self.root / ".blocked").open("xb") as out:
            out.write(b"SYNTHETIC suppressed; never reopen this cohort\n")
            out.flush()
            os.fsync(out.fileno())

    def read(self, name="photo.png"):
        with self.lock:
            if self.blocked or (self.root / ".blocked").exists():
                raise ValueError("Pilot suppressed")
            try:
                if (
                    self.snapshot() != self.expected
                    or name not in self.expected
                    or sha(self.identities) != self.cohort
                ):
                    raise ValueError("Inventory changed")
                query = Query(
                    session=self.session,
                    nonce=secrets.token_hex(32),
                    inventory=sha(self.expected),
                    plan=self.plan,
                    identities=self.identities,
                ).model_dump()
                started, wall = self.clock(), time.time()
                frame = self.exchange(seal(self.__channel, b"request", query))
                reply = unseal(self.__channel, b"response", frame)
                elapsed, wall_elapsed = self.clock() - started, time.time() - wall
                if (
                    not 0 <= elapsed <= DEADLINE
                    or not 0 <= wall_elapsed <= DEADLINE
                    or set(reply) != {"request", "result"}
                    or reply["request"] != sha(query)
                    or self.snapshot() != self.expected
                ):
                    raise ValueError("Stale or mismatched decision")
                if reply["result"] != "CLEAR":
                    raise ValueError("Deletion match or identity hold")
                # Read only the bytes whose hash was matched, never a changed file.
                raw = (self.root / name).read_bytes()
                if hashlib.sha256(raw).hexdigest() != self.expected[name]:
                    raise ValueError("Copy changed during release")
                return raw
            except Exception:
                self._suppress()
                raise ValueError("Pilot suppressed; no content released") from None

    def cleanup(self):
        """Explicit whole-cohort cleanup, only for this instance's synthetic assets."""
        with self.lock:
            if not self.blocked:
                self._suppress()
            actual = self.snapshot()
            if set(actual) != set(self.expected) | {".blocked"}:
                raise ValueError("Unknown copies: cleanup incomplete")
            for name in self.expected:
                (self.root / name).unlink()
            if set(self.snapshot()) != {".blocked"}:
                raise ValueError("Cleanup incomplete")


def demo():
    import tempfile

    with tempfile.TemporaryDirectory(prefix="jri020-match-") as tmp:
        root = Path(tmp)
        channel = secrets.token_bytes(32)  # Disposable authenticated-channel simulation.
        runner = SyntheticRunner(root / "runner", channel)
        pilot = SyntheticPilot(root / "pilot", channel, runner.exchange)
        assert pilot.read().startswith(b"\x89PNG")
        runner.notice()
        try:
            pilot.read()
            raise AssertionError("Deletion was not enforced")
        except ValueError:
            pass
        pilot.cleanup()
        assert set(p.name for p in pilot.root.iterdir()) == {".blocked"}
        print(
            json.dumps(
                {
                    "synthetic_only": True,
                    "live_requests": 0,
                    "clear_read": True,
                    "deletion_suppressed": True,
                    "inventoried_copies_removed": len(pilot.expected),
                }
            )
        )
    assert not root.exists()


if __name__ == "__main__":
    demo()
