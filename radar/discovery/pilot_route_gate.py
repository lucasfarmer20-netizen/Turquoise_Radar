"""Opt-in isolated pilot route gate; transport is injected, never configured from secrets.

This qualification adapter accepts synthetic actual-schema cohorts only. No live
launcher/SSH adapter is supplied. Default application instances are unchanged.
"""

import asyncio
import hashlib
import os
import secrets
from pathlib import Path

from sqlalchemy import select
from starlette.responses import Response

from radar.db import DiscoveredItem, DiscoveryObservation, Listing
from radar.discovery.local_deletion_slice import Identity, Query, seal, sha, unseal


class PilotRouteGate:
    def __init__(self, root, channel_key, exchange, *, timeout=5):
        self.root = Path(root).absolute()
        if "new-only-research" in self.root.parts or any(
            p.is_symlink() for p in (self.root, *self.root.parents)
        ):
            raise ValueError("Separate new pilot root required")
        self.root.mkdir(mode=0o700, exist_ok=False)
        (self.root / "photos").mkdir(mode=0o700)
        self.channel_key, self.exchange, self.timeout = channel_key, exchange, timeout
        self.session = secrets.token_hex(32)
        self.expected = None
        self.identities = None
        self.blocked = False
        self.lock = asyncio.Lock()

    def check_config(self, database_url):
        from sqlalchemy.engine import make_url

        url = make_url(database_url or "sqlite:///data/radar.db")
        photos = Path(os.environ.get("VISION_IMAGE_DIR", "")).absolute()
        if (
            url.drivername != "sqlite"
            or Path(url.database or "").absolute() != self.root / "pilot.db"
            or photos != self.root / "photos"
        ):
            raise ValueError("Pilot requires its separate database and photo directory")

    def snapshot(self):
        result = {}
        for path in self.root.rglob("*"):
            if path.is_symlink():
                raise ValueError("Symlink outside closed inventory")
            if path.is_dir():
                continue
            if not path.is_file() or path.stat().st_nlink != 1:
                raise ValueError("Unsafe inventory entry")
            result[str(path.relative_to(self.root))] = hashlib.sha256(path.read_bytes()).hexdigest()
        return result

    def freeze(self, sessions):
        """Explicit offline qualification after fixture preparation; never auto-adopt changes."""
        if self.expected is not None or self.blocked:
            raise ValueError("One-shot pilot already frozen or suppressed")
        values = []
        with sessions() as db:
            if Path(db.get_bind().url.database).absolute() != self.root / "pilot.db":
                raise ValueError("Wrong database")
            # Actual app schema; only clearly synthetic listings are accepted here.
            if db.scalar(select(DiscoveredItem)) or db.scalar(select(DiscoveryObservation)):
                raise ValueError("Hydrated production cohort not qualified by this adapter")
            for row in db.scalars(select(Listing)):
                if row.data.get("synthetic") is not True:
                    raise ValueError("Synthetic route qualification only")
                values.append(Identity.model_validate(row.original_data.get("seller")))
        if not 1 <= len(values) <= 12:
            raise ValueError("Bounded typed cohort required")
        self.identities = [v.model_dump() for v in values]
        self.expected = self.snapshot()
        if "pilot.db" not in self.expected or ".blocked" in self.expected:
            self.expected = None
            raise ValueError("Invalid pilot inventory")

    def suppress(self):
        self.blocked = True
        path = self.root / ".blocked"
        with path.open("xb") as out:
            out.write(b"Pilot suppressed; do not reopen or resume.\n")
            out.flush()
            os.fsync(out.fileno())

    def unchanged(self):
        if self.blocked or self.expected is None or self.snapshot() != self.expected:
            raise ValueError("Pilot inventory unavailable or changed")

    async def authorize(self, request_binding):
        self.check_config("sqlite:///" + str(self.root / "pilot.db"))
        self.unchanged()
        query = Query(
            session=self.session,
            nonce=secrets.token_hex(32),
            inventory=sha(self.expected),
            plan=sha(request_binding),
            identities=self.identities,
        ).model_dump()
        # Timeout denies release even if a blocked worker cannot be cancelled. No retry.
        frame = await asyncio.wait_for(
            asyncio.to_thread(self.exchange, seal(self.channel_key, b"request", query)),
            timeout=self.timeout,
        )
        answer = unseal(self.channel_key, b"response", frame)
        if answer != {"request": sha(query), "result": "CLEAR"}:
            raise ValueError("Deletion decision denied")
        self.unchanged()


class PilotRouteMiddleware:
    """Buffers actual route output until both matching and post-render inventory checks pass."""

    def __init__(self, app, gate):
        self.app, self.gate = app, gate

    async def __call__(self, scope, receive, send):
        if scope["type"] == "lifespan":
            return await self.app(scope, receive, send)
        if scope["type"] != "http":
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
            return
        path, method = scope["path"], scope["method"]
        if path.startswith("/static/") and method in ("GET", "HEAD"):
            return await self.app(scope, receive, send)
        allowed = path == "/feed" or path.startswith(("/feed/photos/", "/vision/photos/"))
        if method != "GET" or not allowed:
            return await Response(
                "Pilot route unavailable", 403, headers={"Cache-Control": "private, no-store"}
            )(scope, receive, send)
        gate = self.gate
        async with gate.lock:
            messages, total = [], 0

            async def capture(message):
                nonlocal total
                if message["type"] == "http.response.body":
                    total += len(message.get("body", b""))
                    if total > 16 * 1024 * 1024:
                        raise ValueError("Pilot response exceeds bound")
                messages.append(message)

            try:
                await gate.authorize(
                    {
                        "method": method,
                        "path": path,
                        "query": scope.get("query_string", b"").hex(),
                    }
                )
                await asyncio.wait_for(self.app(scope, receive, capture), timeout=gate.timeout)
                await gate.authorize(
                    {
                        "method": method,
                        "path": path,
                        "query": scope.get("query_string", b"").hex(),
                        "phase": "release",
                    }
                )
            except Exception:
                if not gate.blocked:
                    try:
                        gate.suppress()
                    except OSError:
                        pass  # In-memory latch still prevents any further release.
                return await Response(
                    "Pilot unavailable; deletion review required",
                    503,
                    headers={"Cache-Control": "private, no-store"},
                )(scope, receive, send)
            for message in messages:
                if message["type"] == "http.response.start":
                    headers = [
                        (k, v) for k, v in message["headers"] if k.lower() != b"cache-control"
                    ]
                    message = {
                        **message,
                        "headers": headers + [(b"cache-control", b"private, no-store")],
                    }
                await send(message)
