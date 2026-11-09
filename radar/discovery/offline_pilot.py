"""JRI-020 synthetic-only one-shot qualification; no live transport or environment keys.

This is deliberately not a live launcher. Production-shaped fixtures exercise the
real adapter and deletion gates in a new private workspace; no app is served.
"""

import argparse
import asyncio
import fcntl
import hashlib
import json
import os
from unittest.mock import patch

import httpx
from sqlalchemy import select

from radar.db import (
    DiscoveredItem,
    DiscoveryGroup,
    DiscoveryObservation,
    DiscoveryRun,
    Setting,
    database,
)
from radar.discovery.config import Budgets, Connection, GroupInput, SearchOptions
from radar.discovery.ebay import EbayAdapter
from radar.discovery.identity_hydration import FIELD, HydratedAdapter, retained_identity
from radar.discovery.image_pilot import QUERIES, _safe_path, _write_json
from radar.discovery.service import plan, run_discovery
from radar.ebay_deletion import sync
from radar.ebay_deletion.processing import replay
from radar.ebay_deletion.protocol import digest
from radar.ebay_deletion.store import Queue

# Public fixture material, never an operational credential or a replacement ledger.
SECRET = b"SYNTHETIC-JRI020-QUALIFICATION-KEY-NOT-FOR-PRODUCTION"
SCHEMA = "jri020-offline-only-1"
FILES = {
    "pilot.db",
    "receiver.db",
    "ledger.db",
    "inventory.json",
    "preview.json",
    "authorization-consumed.json",
    "receipt.json",
    ".lock",
}
LIMITS = Budgets(enabled=False, max_queries=4, max_results=12, max_pages_per_query=1, page_size=3)
OPTIONS = SearchOptions(buying_format="BOTH", sort="newlyListed", auto_correct="omit")


def checksum(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def inventory(root):
    return {
        "version": 1,
        "inventory_complete": True,
        "assets": [{"kind": "sqlite", "path": str(root / "pilot.db")}],
        "aliases": {},
        "photo_reviews": {},
    }


def check_files(root):
    root = _safe_path(root)
    if root.stat().st_mode & 0o077:
        raise ValueError("Private synthetic workspace required")
    for path in root.iterdir():
        if path.name not in FILES or path.is_symlink() or not path.is_file():
            raise ValueError("Uninventoried copy or unsafe workspace entry")
        if path.stat().st_mode & 0o077:
            raise ValueError("Private synthetic files required")
    if json.loads((root / "inventory.json").read_text()) != inventory(root):
        raise ValueError("Inventory changed")
    return root


def preview_for(root, scenario):
    if scenario not in ("username", "stable-fixture", "hydrated-fixture"):
        raise ValueError("Unknown synthetic identity scenario")
    return {
        "schema": SCHEMA,
        "synthetic_only": True,
        "workspace": str(root),
        "scenario": scenario,
        "database_sha256": hashlib.sha256((root / "pilot.db").read_bytes()).hexdigest(),
        "inventory_sha256": checksum(inventory(root)),
        "queries": list(QUERIES),
        "limits": LIMITS.model_dump(),
        "options": OPTIONS.model_dump(),
        "marketplace": "EBAY_US",
        "destination": "US 99669; eligibility unverified",
        "timeout_seconds": 15,
        "retry_limit": 2,
        "request_interval_seconds": 1,
        "image_requests": 0,
        "detail_proposal": {
            "enabled_synthetically": scenario == "hydrated-fixture",
            "maximum": 12 if scenario == "hydrated-fixture" else 0,
            "retries": 0,
            "production_validation": "UNVERIFIED",
        },
        "live_execution_supported": False,
    }


def prepare(workspace, scenario="username"):
    root = _safe_path(workspace)
    # Exclusive creation prevents reuse of any prior application or pilot.
    root.mkdir(mode=0o700, parents=False, exist_ok=False)
    engine, sessions = database("sqlite:///" + str(root / "pilot.db"))
    try:
        with sessions.begin() as session:
            session.add(Setting(id=3, data=LIMITS.model_dump()))
            for index, query in enumerate(QUERIES):
                session.add(
                    DiscoveryGroup(
                        id=f"jri020-{index}",
                        data=GroupInput(
                            name="SYNTHETIC JRI020", queries=[query], options=OPTIONS
                        ).model_dump(mode="json"),
                    )
                )
    finally:
        engine.dispose()
    (root / "pilot.db").chmod(0o600)
    for name in ("receiver.db", "ledger.db"):
        Queue(root / name).bind(SECRET)
    # One unrelated stable-ID event ensures username-only fixtures cannot obtain
    # a vacuous pass from an empty synthetic ledger.
    Queue.existing(root / "receiver.db").accept(
        "a" * 64, {"userId": digest(SECRET, "userId", "SYNTHETIC-unrelated-account")}
    )
    _write_json(root / "inventory.json", inventory(root), exclusive=True)
    value = preview_for(root, scenario)
    preview = value | {"sha256": checksum(value)}
    _write_json(root / "preview.json", preview, exclusive=True)
    return preview


def mock_handler(scenario):
    def respond(request):
        if request.url.host != "api.ebay.com":
            raise ValueError("Unexpected mock destination")
        if request.method == "POST" and request.url.path == "/identity/v1/oauth2/token":
            return httpx.Response(
                200,
                json={
                    "access_token": "SYNTHETIC-OFFLINE-TOKEN",
                    "expires_in": 7200,
                    "token_type": "Application Access Token",
                },
            )
        if (
            scenario == "hydrated-fixture"
            and request.method == "GET"
            and request.url.path.startswith("/buy/browse/v1/item/")
        ):
            if dict(request.url.params) != {"fieldgroups": "ADDITIONAL_SELLER_DETAILS"}:
                raise ValueError("Unexpected detail request")
            item_id = request.url.path.rsplit("/", 1)[-1]
            number = item_id.removeprefix("SYNTHETIC-")
            return httpx.Response(
                200,
                json={
                    "itemId": item_id,
                    "seller": {
                        "username": f"SYNTHETIC-seller-{number}",
                        "userId": f"SYNTHETIC-account-{number}",
                    },
                },
            )
        query = request.url.params.get("q")
        if (
            request.method != "GET"
            or request.url.path != "/buy/browse/v1/item_summary/search"
            or query not in QUERIES
            or dict(request.url.params) != {k: str(v) for k, v in OPTIONS.params(query, 3).items()}
        ):
            raise ValueError("Unexpected mock request plan")
        index = QUERIES.index(query)
        # Same bounded 12-observation / 8-unique shape; all values are invented.
        groups = ((0, 1, 2), (0, 1, 2), (3, 4, 5), (5, 6, 7))
        items = []
        for number in groups[index]:
            seller = {"username": f"SYNTHETIC-seller-{number}"}
            if scenario == "stable-fixture":
                # Hypothetical supported identity evidence, NOT a claim that
                # actual Browse summaries supply this field.
                seller["userId"] = f"SYNTHETIC-account-{number}"
            items.append(
                {
                    "itemId": f"SYNTHETIC-{number}",
                    "title": f"SYNTHETIC offline ring {number}",
                    "seller": seller,
                    "buyingOptions": ["FIXED_PRICE"],
                    "price": {"value": "15.00", "currency": "USD"},
                }
            )
        return httpx.Response(200, json={"itemSummaries": items, "total": len(items)})

    return respond


async def execute(workspace, approved_preview, *, transport=None, receiver_pull=None):
    """Only MockTransport is accepted; every pull still passes real nonce/HMAC checks.

    Injection points are for offline negative tests, never live integration hooks.
    Fixed synthetic keys and fixtures cannot confer real deletion coverage.
    """
    if transport is not None and type(transport) is not httpx.MockTransport:
        raise ValueError("Only offline MockTransport supported")
    root = check_files(workspace)
    fd = os.open(root / ".lock", os.O_CREAT | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    engine = None
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if (root / "authorization-consumed.json").exists():
            raise ValueError("Authorization already consumed; no resume or retry")
        preview = json.loads((root / "preview.json").read_text())
        expected = preview_for(root, preview["scenario"])
        if preview != expected | {"sha256": checksum(expected)} or approved_preview != checksum(
            expected
        ):
            raise ValueError("Exact unchanged preview approval required")
        engine, sessions = database("sqlite:///" + str(root / "pilot.db"))
        with sessions() as session:
            if session.scalar(select(DiscoveredItem)) or session.scalar(select(DiscoveryRun)):
                raise ValueError("Empty fresh pilot required")
            entries = plan(session, LIMITS.model_copy(update={"enabled": True}))
            if [entry.query for entry in entries] != list(QUERIES):
                raise ValueError("Exact original plan required")
        receipt = {
            "schema": SCHEMA,
            "synthetic_only": True,
            "state": "QUARANTINED",
            "phase": "pre_collection",
            "gates": [],
            "mock_requests": 0,
            "mock_oauth_attempts": 0,
            "mock_browse_attempts": 0,
            "mock_detail_attempts": 0,
            "live_requests": 0,
            "images": 0,
            "display_authorized": False,
        }
        engine.dispose()

        def gate(phase):
            check_files(root)
            receipt["phase"] = phase
            # Narrow mock boundary: real maintenance_ledger verification and replay,
            # including per-operation nonce, key binding and rollback detection.
            pull = receiver_pull or (
                lambda nonce: sync.receiver_bundle(root / "receiver.db", SECRET, nonce)
            )
            with patch.object(sync, "_pull_receiver", pull):
                statuses = replay(root / "inventory.json", root / "ledger.db", SECRET)
            receipt["gates"].append(
                {
                    "phase": phase,
                    "review_required": sum(status.startswith("REVIEW_") for status in statuses),
                }
            )
            if any(status.startswith("REVIEW_") for status in statuses):
                raise ValueError("Deletion review required; cohort stays quarantined")

        try:
            gate("pre_collection")
            # Durable exclusive marker BEFORE any mock OAuth/Browse dispatch.
            _write_json(
                root / "authorization-consumed.json",
                {"schema": SCHEMA, "preview_sha256": approved_preview},
                exclusive=True,
            )
            _write_json(root / "receipt.json", receipt)
            with sessions.begin() as session:
                session.get(Setting, 3).data = LIMITS.model_copy(
                    update={"enabled": True}
                ).model_dump()
            connection = Connection(
                mode="production",
                production_enabled=True,
                production_approved=True,
                client_id="SYNTHETIC-CLIENT",
                client_secret="SYNTHETIC-SECRET",
                timeout=15,
                retry_limit=2,
                request_interval=1,
            )
            underlying = transport or httpx.MockTransport(mock_handler(preview["scenario"]))

            async def dispatch(request):
                receipt["phase"] = "collection"
                if receipt["mock_requests"] >= (
                    27 if preview["scenario"] == "hydrated-fixture" else 15
                ):
                    raise ValueError("Original retry-inclusive request bound exhausted")
                kind = (
                    "mock_oauth_attempts"
                    if request.method == "POST"
                    else "mock_detail_attempts"
                    if request.url.path.startswith("/buy/browse/v1/item/")
                    else "mock_browse_attempts"
                )
                if kind == "mock_detail_attempts" and preview["scenario"] != "hydrated-fixture":
                    raise ValueError("Detail requests not in original proposal")
                bound = 3 if kind == "mock_oauth_attempts" else 12
                if receipt[kind] >= bound:
                    raise ValueError("Original per-operation request bound exhausted")
                receipt[kind] += 1
                receipt["mock_requests"] += 1
                _write_json(root / "receipt.json", receipt)
                return await underlying.handle_async_request(request)

            async with httpx.AsyncClient(transport=httpx.MockTransport(dispatch)) as client:
                adapter = EbayAdapter(connection, production_run_approved=True, client=client)
                if preview["scenario"] == "hydrated-fixture":
                    source = adapter

                    async def fetch_detail(proposal):
                        await asyncio.sleep(1)
                        response = await client.get(
                            "https://api.ebay.com" + proposal["path"],
                            params=proposal["params"],
                            headers={
                                "Authorization": "Bearer " + source.token.get_secret_value(),
                                "X-EBAY-C-MARKETPLACE-ID": "EBAY_US",
                            },
                            timeout=15,
                            follow_redirects=False,
                        )
                        if response.status_code != 200:
                            raise ValueError("Mock detail unavailable")
                        return response.json()

                    adapter = HydratedAdapter(source, fetch_detail)
                await run_discovery(
                    sessions, connection, "production", approved_run=True, adapter=adapter
                )
            with sessions.begin() as session:
                session.get(Setting, 3).data = LIMITS.model_dump()
                run = session.scalar(select(DiscoveryRun))
                if run.status != "COMPLETED":
                    raise ValueError("Incomplete discovery; quarantine retained")
            engine.dispose()
            gate("post_collection")
            # Even an empty ledger cannot qualify usernames without stable evidence.
            with sessions() as session:
                for row in session.scalars(select(DiscoveredItem)):
                    if row.normalized.get("raw_title") == "eBay account data removed":
                        continue
                    originals = [
                        row.original_data,
                        *session.scalars(
                            select(DiscoveryObservation.raw_data).where(
                                DiscoveryObservation.item_id == row.id
                            )
                        ),
                    ]
                    identities = {"userId": set(), "eiasToken": set()}
                    for original in originals:
                        seller = (
                            retained_identity(original)
                            if FIELD in original
                            else original.get("seller", {})
                        )
                        stable = {
                            k: seller[k]
                            for k in identities
                            if isinstance(seller.get(k), str) and seller[k]
                        }
                        if not stable:
                            raise ValueError("Stable identity evidence unavailable")
                        for key, value in stable.items():
                            identities[key].add(value)
                    if any(len(values) > 1 for values in identities.values()):
                        raise ValueError("Conflicting stable identity evidence")
            engine.dispose()
            gate("pre_display")
            receipt["state"] = "QUALIFIED_SYNTHETIC_ONLY"
            # No app launch, image continuation, durable clearance or live permission.
            return receipt
        except BaseException:
            receipt["state"] = "QUARANTINED"
            raise
        finally:
            with sessions.begin() as session:
                session.get(Setting, 3).data = LIMITS.model_dump()
            engine.dispose()
            _write_json(root / "receipt.json", receipt)
    finally:
        if engine is not None:
            engine.dispose()
        os.close(fd)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["prepare", "execute"])
    parser.add_argument("--workspace", required=True)
    parser.add_argument(
        "--scenario", choices=["username", "stable-fixture", "hydrated-fixture"], default="username"
    )
    parser.add_argument("--approved-preview")
    args = parser.parse_args()
    try:
        result = (
            prepare(args.workspace, args.scenario)
            if args.command == "prepare"
            else asyncio.run(execute(args.workspace, args.approved_preview))
        )
        print(json.dumps(result, indent=2))
    except (ValueError, OSError, KeyError):
        print("Offline qualification incomplete; retain quarantine. No live execution supported.")
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()
