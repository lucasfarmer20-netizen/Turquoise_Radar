"""Opt-in, finite unattended buyer pilot. Import and --preview never contact sources."""

import argparse
import asyncio
import fcntl
import getpass
import hashlib
import json
import os
import signal
import socket
import stat
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from sqlalchemy import select

from radar.db import (
    DiscoveredItem,
    DiscoveryObservation,
    DiscoveryRun,
    Listing,
    SearchExecution,
    VisionCase,
    VisionPhoto,
    database,
)
from radar.discovery.config import Connection, SearchOptions
from radar.discovery.ebay import OAUTH_ERROR_CODES, EbayAdapter, normalize
from radar.discovery.identity_hydration import HydratedAdapter, retained_identity
from radar.discovery.image_pilot import _digest, _store_image, validate_primary_url
from radar.discovery.worker_budget import QUERY, Budget, activate
from radar.ebay_deletion import pilot_job, retained_job
from radar.jewelry.images import MAX_FILE_BYTES, validate_original_image
from radar.schemas import utcnow

PREVIEW = {
    "query": QUERY,
    "modes": ["AUCTION/endingSoonest", "FIXED_PRICE/newlyListed"],
    "cadence": "at most one page hourly, alternating modes; skip missed hours",
    "zip": "99669",
    "maximum_listings_per_run": 3,
    "daily_marketplace_image_attempts": 32,
    "seven_day_total_attempts": 224,
    "per_run": {"oauth": 1, "search": 1, "detail": 3, "image": 3},
    "additional_retained_seller_capacity": 100,
    "deletion_check_seconds": 300,
    "automatic_bids_purchases_paid_AI": False,
}


def configured_endpoint():
    endpoint = os.environ.get("PILOT_MATCH_ENDPOINT", "")
    url = urlsplit(endpoint)
    if (
        not endpoint
        or endpoint != endpoint.strip()
        or url.scheme != "https"
        or not url.hostname
        or url.username
        or url.password
        or url.query
        or url.fragment
        or url.path != "/pilot-deletion"
    ):
        raise ValueError("Set PILOT_MATCH_ENDPOINT to the approved HTTPS pilot operation")
    return endpoint


async def acquire(connection, budget, mode, transport=None):
    options = SearchOptions(
        buying_format=mode, sort="endingSoonest" if mode == "AUCTION" else "newlyListed"
    )

    async def request(req):
        u = req.url
        if u.scheme != "https" or u.port not in (None, 443):
            raise ValueError("Unexpected network target")
        if (
            u.host == "api.ebay.com"
            and req.method == "POST"
            and u.path == "/identity/v1/oauth2/token"
        ):
            kind = "oauth"
        elif (
            u.host == "api.ebay.com"
            and req.method == "GET"
            and u.path == "/buy/browse/v1/item_summary/search"
        ):
            if dict(u.params) != {k: str(v) for k, v in options.params(QUERY, 3).items()}:
                raise ValueError("Search differs from preview")
            kind = "search"
        elif (
            u.host == "api.ebay.com"
            and req.method == "GET"
            and u.path.startswith("/buy/browse/v1/item/")
        ):
            if dict(u.params) != {"fieldgroups": "ADDITIONAL_SELLER_DETAILS"}:
                raise ValueError("Unexpected details")
            kind = "detail"
        elif req.method == "GET" and u.host == "i.ebayimg.com":
            validate_primary_url(str(u))
            if "authorization" in req.headers or "cookie" in req.headers:
                raise ValueError("Image credential isolation failed")
            kind = "image"
        else:
            raise ValueError("Unexpected request")
        budget.reserve(kind)

    async def response(resp):
        if resp.request.url.host != "api.ebay.com":
            return
        budget.received(resp.status_code)
        parts, size = [], 0
        async for part in resp.aiter_bytes():
            size += len(part)
            if size > 2 * 1024 * 1024:
                raise ValueError("API response too large")
            parts.append(part)
        resp._content = b"".join(parts)
        if resp.request.url.path.endswith("/token") and resp.status_code != 200:
            try:
                body = resp.json()
                code = body.get("error") if isinstance(body, dict) else None
            except ValueError:
                code = None
            if isinstance(code, str) and code in OAUTH_ERROR_CODES:
                budget.received(resp.status_code, "OAUTH_" + code.upper())

    rows = []
    async with httpx.AsyncClient(
        transport=transport,
        follow_redirects=False,
        timeout=15,
        event_hooks={"request": [request], "response": [response]},
    ) as client:
        adapter = EbayAdapter(connection, production_run_approved=True, client=client)

        async def detail(spec):
            token = await adapter.access_token()
            return adapter.public_data(
                await adapter._request(
                    "GET",
                    spec["path"],
                    params=spec["params"],
                    headers={
                        "Authorization": "Bearer " + token,
                        "X-EBAY-C-MARKETPLACE-ID": "EBAY_US",
                    },
                )
            )

        result = await HydratedAdapter(adapter, detail).search(
            QUERY, options, limit=3, page_limit=1, page_size=3
        )
        if result.errors or len(result.pages) != 1:
            raise ValueError("Search did not qualify")
        page = result.pages[0]
        if page.corrected_query not in (None, QUERY) or len(page.items) > 3:
            raise ValueError("Search scope changed")
        seen = set()
        for raw in page.items:
            item = normalize(raw, "production", QUERY, QUERY, page.corrected_query)
            if item.source_listing_id in seen or mode not in item.buying_options:
                raise ValueError("Unexpected result")
            seen.add(item.source_listing_id)
            retained_identity(raw)
            # Photos and full API responses stay in memory until identity maintenance qualifies.
            image = None
            url = validate_primary_url(str(item.image_urls[0])) if item.image_urls else None
            if url:
                async with httpx.AsyncClient(
                    transport=transport,
                    follow_redirects=False,
                    timeout=15,
                    event_hooks={"request": [request]},
                ) as photos:
                    async with photos.stream(
                        "GET", url, headers={"Accept-Encoding": "identity"}
                    ) as r:
                        budget.received(r.status_code)
                        if (
                            r.status_code != 200
                            or r.headers.get("Content-Encoding", "identity") != "identity"
                        ):
                            raise ValueError("Photo unavailable")
                        parts, size = [], 0
                        async for part in r.aiter_bytes():
                            size += len(part)
                            if size > MAX_FILE_BYTES:
                                raise ValueError("Photo too large")
                            parts.append(part)
                        image = validate_original_image(
                            b"".join(parts), r.headers.get("Content-Type", "")
                        )
            rows.append((raw, item, image, url))
    return rows, options


def retain(root, rows, options, client, token, budget):
    by_id = {}
    for raw, _, _, _ in rows:
        seller = retained_identity(raw)
        if by_id.setdefault(seller["userId"], seller) != seller:
            raise ValueError("Conflicting retained identity")
    sellers = list(by_id.values())
    budget.phase("DELETION_REGISTRATION")
    if sellers:
        retained_job.register(client, configured_endpoint(), token, sellers)
    budget.phase("TRACKED_RETENTION")
    engine, sessions = database("sqlite:///" + str(root / "pilot.db"))
    try:
        references = []
        with sessions.begin() as db:
            run = DiscoveryRun(
                mode="production", options={"worker": "buyer-worker-1"}, status="RUNNING"
            )
            db.add(run)
            db.flush()
            execution = SearchExecution(
                run_id=run.id,
                source="eBay",
                mode="production",
                group_ids=[],
                original_query=QUERY,
                transmitted_query=QUERY,
                parameters=options.params(QUERY, 3),
                status="RUNNING",
            )
            db.add(execution)
            db.flush()
            for raw, item, image, url in rows:
                identity = hashlib.sha256(("eBay|" + item.source_listing_id).encode()).hexdigest()
                found = db.scalar(select(DiscoveredItem).where(DiscoveredItem.identity == identity))
                if found is None:
                    found = db.scalar(
                        select(DiscoveredItem).where(
                            DiscoveredItem.source == "eBay",
                            DiscoveredItem.ebay_item_id == item.source_listing_id,
                        )
                    )
                if found is None:
                    found = DiscoveredItem(
                        identity=identity,
                        source="eBay",
                        ebay_item_id=item.source_listing_id,
                        normalized=item.model_dump(mode="json"),
                        original_data=raw,
                    )
                    db.add(found)
                    run.new_count += 1
                    execution.new_count += 1
                else:
                    # Owner feedback/financial evidence is not overwritten by a source refresh.
                    refreshed = item.model_dump(mode="json")
                    if found.listing_id:
                        owner = db.get(Listing, found.listing_id)
                        if owner:
                            data = dict(owner.data)
                            for field in (
                                "image_urls",
                                "item_end_time",
                                "buying_options",
                                "item_price",
                                "currency",
                                "shipping_cost",
                            ):
                                if data.get(field) == found.normalized.get(field):
                                    data[field] = refreshed.get(field)
                            owner.data = data
                            owner.original_data = {
                                "provider": "eBay Browse",
                                "mode": "production",
                                "item": raw,
                            }
                    found.normalized = refreshed
                    found.original_data = raw
                    found.last_seen = utcnow()
                    run.duplicate_count += 1
                    execution.duplicate_count += 1
                db.flush()
                db.add(
                    DiscoveryObservation(
                        execution_id=execution.id,
                        item_id=found.id,
                        raw_data=raw,
                        correction_state="UNKNOWN",
                    )
                )
                for photo in db.scalars(
                    select(VisionPhoto).join(VisionCase).where(VisionCase.candidate_id == found.id)
                ):
                    photo.active = False  # Still inventoried and covered by deletion.
                references.append(
                    (found.id, item.source_listing_id, found.last_seen.isoformat(), image, url)
                )
            execution.results_returned = len(rows)
            execution.unique_listings = len(rows)
            run_id, execution_id = run.id, execution.id
        for candidate, source_id, observed, image, url in references:
            if image is None:
                continue
            with sessions() as db:
                record = (
                    db.connection()
                    .exec_driver_sql("SELECT * FROM discovered_items WHERE id=?", (candidate,))
                    .mappings()
                    .one()
                )
            reference = {
                "candidate_id": candidate,
                "source_listing_id": source_id,
                "listing_observed_at": observed,
                "source_url_sha256": hashlib.sha256(url.encode()).hexdigest(),
                "record_sha256": _digest(dict(record)),
            }
            _store_image(sessions, root / "photos", root / "inventory.json", reference, image)
        budget.phase("POST_RETENTION_DELETION_CHECK")
        retained_job.cycle(root, client, configured_endpoint(), token)
        with sessions.begin() as db:
            run = db.get(DiscoveryRun, run_id)
            run.status, run.completed_at = "COMPLETED", utcnow()
            db.get(SearchExecution, execution_id).status = "COMPLETED"
    finally:
        engine.dispose()


async def tick(root, connection, token, client, *, clock=time.time, transport=None):
    # A live capability/deletion check always precedes any marketplace reservation.
    retained_job.cycle(root, client, configured_endpoint(), token)
    budget = Budget(root / "pilot.db", clock)
    mode = budget.begin()
    if mode is None:
        return "MAINTENANCE_ONLY"
    try:
        rows, options = await acquire(connection, budget, mode, transport)
        retain(root, rows, options, client, token, budget)
        budget.finish(True)
        return "COMPLETED"
    except Exception:
        budget.finish(False)
        raise ValueError("Worker paused; no automatic acquisition retry") from None


def private_token(path):
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError("Unsafe token path")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ValueError("Private token required")
        value = stream.read(257).decode().strip()
    if not 32 <= len(value) <= 256 or any(c.isspace() for c in value):
        raise ValueError("Invalid token")
    return value


def run(root, connection, token):
    configured_endpoint()
    pilot_job.inventory(root)
    pilot_job.enrolled_state(root)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 8001))
    fd = os.open(root / ".job-lock", os.O_CREAT | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        process = None
        # Activation preserves an existing policy even after restart; never resets attempts.
        activate(root / "pilot.db", time.time())
        child_env = dict(
            os.environ,
            RADAR_DATABASE_URL="sqlite:///" + str(root / "pilot.db"),
            VISION_IMAGE_DIR=str(root / "photos"),
            RADAR_PILOT_VIEW_ONLY="true",
            EBAY_SOURCE_ENABLED="false",
            EBAY_PRODUCTION_ENABLED="false",
            EBAY_PRODUCTION_APPROVED="false",
            VISION_LIVE_ENABLED="false",
        )
        for key in (
            "EBAY_CLIENT_ID",
            "EBAY_CLIENT_SECRET",
            "PILOT_MATCH_TOKEN",
            "ANTHROPIC_API_KEY",
        ):
            child_env.pop(key, None)
        try:
            with httpx.Client() as client:
                while True:
                    pilot_job.stop(process)
                    process = None
                    try:
                        asyncio.run(tick(root, connection, token, client))
                    except Exception:
                        print("WORKER_PAUSED_OR_MAINTENANCE_UNAVAILABLE", flush=True)
                    try:
                        retained_job.cycle(root, client, configured_endpoint(), token)
                        process = subprocess.Popen(
                            [
                                sys.executable,
                                "-m",
                                "radar.ebay_deletion.pilot_server",
                                str(os.getpid()),
                            ],
                            env=child_env,
                        )
                    except Exception:
                        print("FEED_WITHHELD_DELETION_CHECK_UNAVAILABLE", flush=True)
                    time.sleep(300)
        finally:
            pilot_job.stop(process)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preview", action="store_true")
    parser.add_argument("--enable", action="store_true")
    parser.add_argument("--root", type=Path)
    args = parser.parse_args()
    if args.preview or not args.enable:
        print(json.dumps(PREVIEW, indent=2))
        return
    try:
        configured_endpoint()
    except ValueError as error:
        parser.error(str(error))
    if sys.platform != "linux" or os.getuid() == 0 or args.root is None:
        parser.error("An existing private Linux pilot root is required")
    root = args.root.absolute()
    pilot_job.inventory(root)
    pilot_job.enrolled_state(root)
    client_id = getpass.getpass("Production App ID (Client ID), private: ")
    secret = getpass.getpass("Production Cert ID (Client Secret), same keyset, private: ")
    if not client_id or not secret or client_id != client_id.strip() or secret != secret.strip():
        raise ValueError("Empty or whitespace credential refused locally")
    if "sandbox" in client_id.lower() or "-SBX-" in client_id.upper():
        raise ValueError("Sandbox-labeled credential refused locally")
    connection = Connection(
        mode="production",
        enabled=True,
        production_enabled=True,
        production_approved=True,
        client_id=client_id,
        client_secret=secret,
        retry_limit=0,
        request_interval=1,
    )
    token = private_token(Path.home() / ".config/turquoise-radar/jri020/pilot-token")
    for sig in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, pilot_job.shutdown_signal)
    run(root, connection, token)


if __name__ == "__main__":
    try:
        main()
    except (Exception, KeyboardInterrupt):
        print("WORKER_STOPPED_NO_SECRET_DETAILS; inspect local status, do not reset counters")
        raise SystemExit(1) from None
