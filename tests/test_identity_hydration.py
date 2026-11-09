"""Offline detail identity provenance; synthetic values, no external traffic."""

import asyncio
import copy
import json
import sqlite3
from datetime import UTC, datetime

import httpx
import pytest

from radar.discovery import offline_pilot as pilot
from radar.discovery.config import DiscoveryError
from radar.discovery.ebay import SearchPage, SearchResult
from radar.discovery.identity_hydration import (
    FIELD,
    HydratedAdapter,
    evidence_for,
    retained_identity,
)
from radar.ebay_deletion import sync
from radar.ebay_deletion.protocol import digest
from radar.ebay_deletion.store import Queue


def summary():
    return {
        "itemId": "SYNTHETIC-0",
        "title": "SYNTHETIC ring",
        "seller": {"username": "SYNTHETIC-seller-0"},
    }


def detail():
    return {
        "itemId": "SYNTHETIC-0",
        "seller": {"username": "SYNTHETIC-seller-0", "userId": "SYNTHETIC-account-0"},
        "description": "SYNTHETIC excessive detail must not be retained",
    }


@pytest.mark.parametrize(
    "change", ["missing_id", "wrong_item", "wrong_seller", "blank", "wrong_type"]
)
def test_missing_or_mismatched_detail_rejected(change):
    raw = detail()
    if change == "missing_id":
        del raw["seller"]["userId"]
    elif change == "wrong_item":
        raw["itemId"] = "SYNTHETIC-other-item"
    elif change == "wrong_seller":
        raw["seller"]["username"] = "SYNTHETIC-other-seller"
    elif change == "blank":
        raw["seller"]["userId"] = " "
    else:
        raw["seller"]["userId"] = 123
    with pytest.raises(ValueError):
        evidence_for(summary(), raw, datetime.now(UTC))


def test_typed_evidence_preserves_summary_and_rejects_rebinding():
    original = summary()
    before = copy.deepcopy(original)
    evidence = evidence_for(original, detail(), datetime.now(UTC)).model_dump(mode="json")
    hydrated = original | {FIELD: evidence}
    assert original == before and "userId" not in hydrated["seller"]
    assert retained_identity(hydrated)["userId"] == "SYNTHETIC-account-0"
    assert evidence["production_validation"] == "UNVERIFIED"
    assert "description" not in json.dumps(hydrated)
    hydrated["title"] = "SYNTHETIC changed evidence"
    with pytest.raises(ValueError):
        retained_identity(hydrated)
    with pytest.raises(ValueError):
        evidence_for(original | {FIELD: evidence}, detail(), datetime.now(UTC))


def test_detail_bound_and_cache_do_not_drop_query_observations():
    class Source:
        async def search(self, *args):
            return SearchResult(pages=[SearchPage(items=[summary(), summary()], offset=0)])

        async def close(self):
            pass

    calls = []

    async def fetch(proposal):
        calls.append(proposal)
        return detail()

    adapter = HydratedAdapter(Source(), fetch)
    first = asyncio.run(adapter.search())
    second = asyncio.run(adapter.search())
    assert len(calls) == adapter.detail_attempts == 1
    assert calls[0] == {
        "method": "GET",
        "path": "/buy/browse/v1/item/SYNTHETIC-0",
        "params": {"fieldgroups": "ADDITIONAL_SELLER_DETAILS"},
    }
    assert len(first.pages[0].items) == len(second.pages[0].items) == 2


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    async def deny(*args, **kwargs):
        raise AssertionError("Real HTTP forbidden")

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", deny)
    monkeypatch.setattr(sync, "_pull_receiver", lambda _: pytest.fail("Real SSH forbidden"))


def test_integrated_hydration_and_unchanged_deletion_rules(tmp_path):
    root = tmp_path / "SYNTHETIC-hydrated"
    p = pilot.prepare(root, "hydrated-fixture")
    original_limits = pilot.LIMITS.model_dump()
    assert p["limits"] == original_limits and p["detail_proposal"]["maximum"] == 12
    calls = []

    def pull(nonce):
        calls.append(nonce)
        if len(calls) == 2:
            Queue.existing(root / "receiver.db").accept(
                "b" * 64, {"userId": digest(pilot.SECRET, "userId", "SYNTHETIC-account-0")}
            )
        return sync.receiver_bundle(root / "receiver.db", pilot.SECRET, nonce)

    result = asyncio.run(pilot.execute(root, p["sha256"], receiver_pull=pull))
    assert result["state"] == "QUALIFIED_SYNTHETIC_ONLY"
    assert result["mock_detail_attempts"] == 8
    assert result["mock_requests"] == 13
    assert result["live_requests"] == 0 and result["display_authorized"] is False
    with sqlite3.connect(root / "pilot.db") as db:
        rows = db.execute("SELECT original_data,normalized FROM discovered_items").fetchall()
        assert len(rows) == 8
        assert sum(json.loads(n)["raw_title"] == "eBay account data removed" for _, n in rows) == 1
        for raw, normalized in rows:
            data = json.loads(raw)
            if data:
                assert FIELD in data and "userId" not in data["seller"]
                assert retained_identity(data)["userId"].startswith("SYNTHETIC-")
                assert json.loads(normalized)["seller_id"].startswith("SYNTHETIC-seller-")
        assert db.execute("SELECT count(*) FROM discovery_observations").fetchone()[0] == 12
        erased = [
            json.loads(r[0])
            for r in db.execute(
                "SELECT raw_data FROM discovery_observations WHERE item_id="
                "(SELECT id FROM discovered_items WHERE ebay_item_id LIKE 'removed-%')"
            )
        ]
        assert erased and all(raw == {} for raw in erased)


@pytest.mark.parametrize("failure", ["missing", "mismatch", "http403"])
def test_hydration_failure_never_persists_username_only_page(tmp_path, failure):
    root = tmp_path / failure
    p = pilot.prepare(root, "hydrated-fixture")
    handler = pilot.mock_handler("hydrated-fixture")
    calls = []

    def respond(request):
        calls.append(request.url.path)
        if request.url.path.startswith("/buy/browse/v1/item/"):
            with sqlite3.connect(root / "pilot.db") as db:
                assert db.execute("SELECT count(*) FROM discovered_items").fetchone()[0] == 0
            if failure == "http403":
                return httpx.Response(403)
            raw = detail()
            if failure == "missing":
                del raw["seller"]["userId"]
            else:
                raw["itemId"] = "SYNTHETIC-wrong-item"
            return httpx.Response(200, json=raw)
        return handler(request)

    with pytest.raises(ValueError, match="Incomplete discovery"):
        asyncio.run(pilot.execute(root, p["sha256"], transport=httpx.MockTransport(respond)))
    assert len(calls) == 3  # OAuth, first search, one detail; no retries or later search.
    with sqlite3.connect(root / "pilot.db") as db:
        assert db.execute("SELECT count(*) FROM discovered_items").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM discovery_observations").fetchone()[0] == 0
    assert json.loads((root / "receipt.json").read_text())["state"] == "QUARANTINED"


def test_detail_request_limit_stops_at_twelve():
    class Source:
        async def search(self, *args):
            return SearchResult(
                pages=[
                    SearchPage(
                        items=[summary() | {"itemId": f"SYNTHETIC-{n}"} for n in range(13)],
                        offset=0,
                    )
                ]
            )

        async def close(self):
            pass

    calls = []

    async def fetch(proposal):
        calls.append(proposal)
        return detail() | {"itemId": proposal["path"].rsplit("/", 1)[-1]}

    adapter = HydratedAdapter(Source(), fetch)
    with pytest.raises(DiscoveryError):
        asyncio.run(adapter.search())
    assert len(calls) == adapter.detail_attempts == 12 and adapter.blocked
    with pytest.raises(DiscoveryError):
        asyncio.run(adapter.search())
    assert len(calls) == 12
