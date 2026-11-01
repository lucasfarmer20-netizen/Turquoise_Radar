"""Synthetic identities and mocked Notification API only. No live eBay calls."""

import asyncio
import base64
import hashlib
import json
import sqlite3
import subprocess

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from radar.db import (
    DiscoveredItem,
    DiscoveryObservation,
    Feedback,
    Listing,
    ListingEdit,
    MakerCase,
    MakerDecision,
    MakerRun,
    MakerScenario,
    database,
)
from radar.discovery.config import Connection
from radar.ebay_deletion.cli import verified_restore
from radar.ebay_deletion.processing import identity_state, sanitize_database
from radar.ebay_deletion.protocol import InvalidNotification, challenge, digest, event, verify
from radar.ebay_deletion.receiver import ApplicationToken, PublicKeys, create_receiver
from radar.ebay_deletion.store import Queue
from radar.main import create_app
from radar.schemas import ListingInput

SECRET = b"SYNTHETIC-HMAC-KEY-000000000000000000000000"
TOKEN = "SYNTHETIC-verification-token-0000000000000"
ENDPOINT = "https://notifications.example.invalid/ebay/account-deletion"
BODY = {
    "metadata": {
        "topic": "MARKETPLACE_ACCOUNT_DELETION",
        "schemaVersion": "1.0",
        "deprecated": False,
    },
    "notification": {
        "notificationId": "SYNTHETIC-NOTIFICATION",
        "eventDate": "2026-11-01T00:00:00Z",
        "publishDate": "2026-11-01T00:00:00Z",
        "publishAttemptCount": 1,
        "data": {"userId": "SYNTHETIC-ACCOUNT", "username": "SYNTHETIC-SELLER"},
    },
}


@pytest.fixture(scope="module")
def signed():
    # Independent Node crypto signing vector matching the official eBay SDK's contract.
    script = """
const c=require('node:crypto');
const {privateKey,publicKey}=c.generateKeyPairSync('ec',{namedCurve:'prime256v1'});
const body=process.argv[1];
const signer=c.createSign('sha1').update(JSON.stringify(JSON.parse(body)));
const signature=signer.sign(privateKey,'base64');
console.log(JSON.stringify({signature,key:publicKey.export({type:'spki',format:'pem'})}));
"""
    body = json.dumps(BODY).encode()
    result = json.loads(subprocess.check_output(["node", "-e", script, body.decode()], text=True))
    header = base64.b64encode(
        json.dumps({"kid": "SYNTHETIC-KEY", "signature": result["signature"]}).encode()
    ).decode()
    return (
        body,
        result["signature"],
        header,
        {"algorithm": "ECDSA", "digest": "SHA1", "key": result["key"]},
    )


def receiver(tmp_path, signed, queue=None):
    async def key(kid):
        assert kid == "SYNTHETIC-KEY"
        return signed[3]

    return create_receiver(
        endpoint=ENDPOINT,
        token=TOKEN,
        secret=SECRET,
        queue=queue or Queue(tmp_path / "queue.db"),
        keys=key,
    )


def post(client, signed, body=None, header=None):
    return client.post(
        "/ebay/account-deletion",
        content=body or signed[0],
        headers={"Content-Type": "application/json", "X-EBAY-SIGNATURE": header or signed[2]},
    )


def test_documented_challenge_and_private_boundary(tmp_path, signed):
    with TestClient(receiver(tmp_path, signed)) as c:
        response = c.get("/ebay/account-deletion?challenge_code=SYNTHETIC-code")
        assert response.json() == {
            "challengeResponse": hashlib.sha256(
                ("SYNTHETIC-code" + TOKEN + ENDPOINT).encode()
            ).hexdigest()
        }
        for query in (
            "",
            "?challenge_code=",
            "?challenge_code=a&challenge_code=b",
            "?challenge_code=%00",
        ):
            assert c.get("/ebay/account-deletion" + query).status_code == 400
        for route in ("/", "/docs", "/openapi.json", "/listings", "/discovery", "/data/queue.db"):
            assert c.get(route).status_code == 404
    assert challenge("SYNTHETIC-code", "x" * 32, ENDPOINT) != response.json()["challengeResponse"]
    assert (
        challenge("SYNTHETIC-code", TOKEN, ENDPOINT + "/") != response.json()["challengeResponse"]
    )


def test_authenticity_durable_duplicate_and_restart(tmp_path, signed):
    path = tmp_path / "queue.db"
    with TestClient(receiver(tmp_path, signed, Queue(path))) as c:
        assert post(c, signed).status_code == 204
        assert post(c, signed).status_code == 204
        assert (
            post(
                c, signed, body=signed[0].replace(b"SYNTHETIC-ACCOUNT", b"ATTACKER-ACCOUNT")
            ).status_code
            == 412
        )
        assert post(c, signed, header="bad-base64").status_code == 412
        assert post(c, signed, body=b'{"metadata":1}').status_code == 412
        assert post(c, signed, body=b"x" * 32769).status_code == 413
    assert len(Queue(path).events()) == 1
    raw = path.read_bytes()
    for value in (b"SYNTHETIC-ACCOUNT", b"SYNTHETIC-SELLER", b"SYNTHETIC-NOTIFICATION"):
        assert value not in raw
    assert b"signature" not in raw
    verify(json.dumps(BODY, ensure_ascii=False, indent=2).encode(), signed[1], signed[3])
    with pytest.raises(InvalidNotification):
        verify(signed[0], signed[1], {**signed[3], "digest": "SHA256"})


def test_persistence_failure_is_retryable_and_no_acceptance(tmp_path, signed):
    class FailedQueue:
        def bind(self, secret):
            pass

        def accept(self, *_):
            raise sqlite3.OperationalError("SYNTHETIC-SECRET")

    with TestClient(receiver(tmp_path, signed, FailedQueue())) as c:
        r = post(c, signed)
        assert r.status_code == 503
        assert "SYNTHETIC" not in r.text


def test_failed_verifier_and_unexpected_headers_do_not_retain(tmp_path, signed):
    from radar.ebay_deletion.protocol import VerificationUnavailable

    async def unavailable(_):
        raise VerificationUnavailable("SYNTHETIC-SECRET")

    queue = Queue(tmp_path / "queue.db")
    app = create_receiver(
        endpoint=ENDPOINT, token=TOKEN, secret=SECRET, queue=queue, keys=unavailable
    )
    with TestClient(app) as c:
        assert post(c, signed).status_code == 503
        assert c.post("/ebay/account-deletion", content=b"x").status_code == 415
    assert queue.events() == []


def test_conflicting_duplicate_rejected(tmp_path):
    queue = Queue(tmp_path / "q.db")
    queue.accept("synthetic-event", {"userId": "first"})
    from radar.ebay_deletion.store import Conflict

    with pytest.raises(Conflict):
        queue.accept("synthetic-event", {"userId": "different"})
    assert queue.events() == [("synthetic-event", {"userId": "first"})]


def test_exact_identifiers_no_fuzzy_or_immutable_username_assumption():
    ids = event(json.dumps(BODY).encode(), SECRET)[1]
    assert identity_state([{"seller_id": "SYNTHETIC-SELLER"}], ids, SECRET, {}) == "AMBIGUOUS"
    assert identity_state([{"seller_id": "synthetic-seller"}], ids, SECRET, {}) == "UNMAPPED"
    assert identity_state([{"seller": {"userId": "SYNTHETIC-ACCOUNT"}}], ids, SECRET, {}) == "MATCH"
    assert (
        identity_state(
            [{"seller": {"userId": "OTHER", "username": "SYNTHETIC-SELLER"}}], ids, SECRET, {}
        )
        == "AMBIGUOUS"
    )
    aliases = {digest(SECRET, "username", "SYNTHETIC-SELLER"): ids["userId"]}
    assert identity_state([{"seller_id": "SYNTHETIC-SELLER"}], ids, SECRET, aliases) == "MATCH"


@pytest.fixture
def synthetic_db(tmp_path):
    path = tmp_path / "app.db"
    with TestClient(create_app("sqlite:///" + str(path))) as client:
        sessions = client.app.state.sessions
        with sessions.begin() as s:
            for source, key in (
                ("eBay", "matched"),
                ("HiBid", "unrelated"),
                ("eBay FIXTURE", "fixture"),
            ):
                data = ListingInput(
                    source=source,
                    raw_title="SYNTHETIC title",
                    seller_id="SYNTHETIC-SELLER",
                    item_price="12.00",
                ).model_dump(mode="json")
                row = Listing(
                    dedup_key=key,
                    data=data,
                    original_data={"seller": {"userId": "SYNTHETIC-ACCOUNT"}},
                )
                s.add(row)
                s.flush()
                s.add(ListingEdit(listing_id=row.id, before_data=data, after_data=data))
                s.add(Feedback(listing_id=row.id, action="Interested", note="SYNTHETIC-SELLER"))
                case = MakerCase(
                    listing_id=row.id,
                    data={"title": "SYNTHETIC-SELLER", "details": "Seller evidence"},
                )
                s.add(case)
                s.flush()
                run = MakerRun(case_id=case.id, data={"copied": data})
                s.add(run)
                s.flush()
                s.add(
                    MakerDecision(
                        case_id=case.id, run_id=run.id, action="CONFIRM", data={"copied": data}
                    )
                )
                s.add(MakerScenario(case_id=case.id, data={"listing_snapshot": data}))
        with sessions() as s:
            target = s.scalar(select(Listing).where(Listing.dedup_key == "matched"))
            target_id = target.id
    return path, target_id


def test_listing_edit_research_history_and_unrelated_preservation(synthetic_db):
    path, target = synthetic_db
    ids = event(json.dumps(BODY).encode(), SECRET)[1]
    with sqlite3.connect(path) as db:
        before = list(db.execute("SELECT * FROM listings WHERE id!=?", (target,)))
    status, count = sanitize_database(path, ids, SECRET)
    assert (status, count) == ("SANITIZED", 1)
    with sqlite3.connect(path) as db:
        assert before == list(db.execute("SELECT * FROM listings WHERE id!=?", (target,)))
        data, raw = db.execute(
            "SELECT data,original_data FROM listings WHERE id=?", (target,)
        ).fetchone()
        assert ListingInput.model_validate_json(data).item_price == 12
        assert "SYNTHETIC-SELLER" not in data + raw
        for before_data, after_data in db.execute(
            "SELECT before_data,after_data FROM listing_edits WHERE listing_id=?", (target,)
        ):
            assert "SYNTHETIC-SELLER" not in before_data + after_data
        assert db.execute(
            "SELECT action,note FROM feedback WHERE listing_id=?", (target,)
        ).fetchone() == ("Interested", "")
        case = db.execute("SELECT id FROM maker_cases WHERE listing_id=?", (target,)).fetchone()[0]
        assert (
            db.execute("SELECT count(*) FROM maker_scenarios WHERE case_id=?", (case,)).fetchone()[
                0
            ]
            == 0
        )
        assert (
            db.execute("SELECT count(*) FROM maker_runs WHERE case_id=?", (case,)).fetchone()[0]
            == 0
        )
        assert db.execute(
            "SELECT action,data FROM maker_decisions WHERE case_id=?", (case,)
        ).fetchone() == ("CONFIRM", "{}")
        assert not db.execute("pragma foreign_key_check").fetchall()
    assert sanitize_database(path, ids, SECRET) == ("NO_MATCH", 0)


def test_unmatched_ambiguous_and_artifact_hold(synthetic_db):
    path, target = synthetic_db
    ids = event(json.dumps(BODY).encode(), SECRET)[1]
    assert sanitize_database(
        path,
        {"userId": "not-a-match"},
        SECRET,
        aliases={digest(SECRET, "username", "SYNTHETIC-SELLER"): ids["userId"]},
    ) == ("NO_MATCH", 0)
    with sqlite3.connect(path) as db:
        db.execute("UPDATE listings SET original_data='{}' WHERE id=?", (target,))
    assert sanitize_database(path, ids, SECRET) == ("REVIEW_IDENTITY", 0)
    with sqlite3.connect(path) as db:
        db.execute(
            "UPDATE listings SET original_data=? WHERE id=?",
            (json.dumps({"seller": {"userId": "SYNTHETIC-ACCOUNT"}}), target),
        )
        db.execute(
            "INSERT INTO vision_cases(owner_id,listing_id,created_at) "
            "VALUES ('local-owner',?,'2026-11-01')",
            (target,),
        )
    with sqlite3.connect(path) as db:
        case = db.execute("SELECT id FROM maker_cases WHERE listing_id=?", (target,)).fetchone()[0]
        db.execute(
            "INSERT INTO maker_photos(id,case_id,role,content_hash,data,created_at) "
            "VALUES ('00000000000000000000000000000000',?,'HALLMARK','hash','{}','2026-11-01')",
            (case,),
        )
    assert sanitize_database(path, ids, SECRET) == ("REVIEW_ARTIFACTS", 0)
    with sqlite3.connect(path) as db:
        assert (
            "SYNTHETIC-SELLER"
            in db.execute("SELECT data FROM listings WHERE id=?", (target,)).fetchone()[0]
        )


def test_restore_replays_ledger_even_after_processing_interruption(tmp_path, synthetic_db):
    path, _ = synthetic_db
    queue = Queue(tmp_path / "q.db")
    key, ids = event(json.dumps(BODY).encode(), SECRET)
    queue.accept(key, ids)
    # The original remains an old backup. Restored copies cannot skip the durable ledger.
    destination = tmp_path / "quarantined.db"
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "version": 1,
                "inventory_complete": True,
                "assets": [{"kind": "sqlite", "path": str(destination)}],
            }
        )
    )
    statuses = verified_restore(path, destination, manifest, queue.path, SECRET)
    assert statuses == ["SANITIZED"]
    # Simulate loss of receipt after data commit: replay remains harmless.
    with queue.connect() as db:
        db.execute("DELETE FROM receipts")
    from radar.ebay_deletion.processing import replay

    assert replay(manifest, queue.path, SECRET) == ["NO_MATCH"]
    with pytest.raises(ValueError):
        verified_restore(path, destination, manifest, queue.path, SECRET)
    manifest.write_text(
        json.dumps(
            {
                "version": 1,
                "inventory_complete": True,
                "assets": [{"kind": "archive", "path": str(tmp_path / "old-mixed-backup.tar")}],
            }
        )
    )
    assert replay(manifest, queue.path, SECRET) == ["REVIEW_EXPORT_OR_ARCHIVE"]


def test_notification_oauth_publickey_cache_is_mocked(monkeypatch):
    requests = []
    real_client = httpx.AsyncClient

    def response(request):
        requests.append(request)
        if request.url.path.endswith("/token"):
            assert b"grant_type=client_credentials" in request.content
            return httpx.Response(
                200,
                json={
                    "access_token": "SYNTHETIC-TOKEN",
                    "expires_in": 7200,
                    "token_type": "Application Access Token",
                },
            )
        assert request.url.path == "/commerce/notification/v1/public_key/SYNTHETIC-KEY"
        assert request.headers["Authorization"] == "Bearer SYNTHETIC-TOKEN"
        return httpx.Response(
            200, json={"algorithm": "ECDSA", "digest": "SHA1", "key": "SYNTHETIC"}
        )

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kw: real_client(transport=httpx.MockTransport(response), **kw),
    )

    async def check():
        keys = PublicKeys(ApplicationToken("SYNTHETIC-ID", "SYNTHETIC-SECRET"))
        assert await keys("SYNTHETIC-KEY") == await keys("SYNTHETIC-KEY")
        assert len(requests) == 2

    asyncio.run(check())
    assert not Connection().production_enabled


def test_discovery_observations_linked_to_promoted_listing_are_cleared(synthetic_db):
    from radar.db import DiscoveryRun, SearchExecution

    path, listing_id = synthetic_db
    engine, sessions = database("sqlite:///" + str(path))
    with sessions.begin() as s:
        run = DiscoveryRun(mode="production", options={})
        s.add(run)
        s.flush()
        execution = SearchExecution(
            run_id=run.id,
            source="eBay",
            mode="production",
            group_ids=[],
            original_query="SYNTHETIC query",
            transmitted_query="SYNTHETIC query",
            parameters={},
        )
        s.add(execution)
        s.flush()
        found = DiscoveredItem(
            identity="SYNTHETIC",
            source="eBay",
            ebay_item_id="SYNTHETIC-ITEM",
            listing_id=listing_id,
            normalized={"seller_id": "SYNTHETIC-SELLER"},
            original_data={"seller": {"userId": "SYNTHETIC-ACCOUNT"}},
        )
        s.add(found)
        s.flush()
        s.add(
            DiscoveryObservation(
                execution_id=execution.id,
                item_id=found.id,
                raw_data={"seller": {"userId": "SYNTHETIC-ACCOUNT"}},
                correction_state="UNKNOWN",
            )
        )
    engine.dispose()
    ids = event(json.dumps(BODY).encode(), SECRET)[1]
    assert sanitize_database(path, ids, SECRET) == ("SANITIZED", 2)
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT raw_data FROM discovery_observations").fetchone()[0] == "{}"
        raw = db.execute("SELECT original_data FROM discovered_items").fetchone()[0]
        assert raw == "{}"


def test_database_failure_rolls_back_and_recovers(synthetic_db):
    path, target = synthetic_db
    ids = event(json.dumps(BODY).encode(), SECRET)[1]
    with sqlite3.connect(path) as db:
        db.execute(
            "CREATE TRIGGER fail_update BEFORE UPDATE ON listing_edits "
            "BEGIN SELECT RAISE(ABORT, 'synthetic interruption'); END"
        )
    with pytest.raises(sqlite3.DatabaseError):
        sanitize_database(path, ids, SECRET)
    with sqlite3.connect(path) as db:
        assert (
            "SYNTHETIC-SELLER"
            in db.execute("SELECT data FROM listings WHERE id=?", (target,)).fetchone()[0]
        )
        db.execute("DROP TRIGGER fail_update")
    assert sanitize_database(path, ids, SECRET)[0] == "SANITIZED"


@pytest.mark.parametrize("photo_kind", ["seller_copy", "independent"])
def test_reviewed_photos_and_vision_keep_cost_accounting(synthetic_db, tmp_path, photo_kind):
    from radar.db import MakerPhoto, VisionCase, VisionRun

    path, listing_id = synthetic_db
    photo_id = "a" * 32
    file = tmp_path / (photo_id + ".png")
    file.write_bytes(b"SYNTHETIC-SELLER-PHOTO")
    content_hash = hashlib.sha256(file.read_bytes()).hexdigest()
    engine, sessions = database("sqlite:///" + str(path))
    with sessions.begin() as s:
        case = s.scalar(select(MakerCase).where(MakerCase.listing_id == listing_id))
        s.add(
            MakerPhoto(
                id=photo_id, case_id=case.id, role="HALLMARK", content_hash=content_hash, data={}
            )
        )
        vision = VisionCase(listing_id=listing_id)
        s.add(vision)
        s.flush()
        s.add(
            VisionRun(
                case_id=vision.id,
                provider="anthropic",
                model="SYNTHETIC",
                stage="quick",
                status="FAILED",
                cache_key="SYNTHETIC",
                reserved_usd="0.05",
                accounted_usd="0.05",
                cost_status="HELD_UNKNOWN",
                data={"photo_ids": [], "context": {"seller": "SYNTHETIC-SELLER"}},
            )
        )
    engine.dispose()
    ids = event(json.dumps(BODY).encode(), SECRET)[1]
    assert sanitize_database(path, ids, SECRET) == ("REVIEW_ARTIFACTS", 0)
    reviews = {
        "maker_photos:" + photo_id: {
            "kind": photo_kind,
            "path": str(file),
            "evidence": "Synthetic source provenance",
        }
    }
    assert sanitize_database(path, ids, SECRET, photo_reviews=reviews)[0] == "SANITIZED"
    assert file.exists() == (photo_kind == "independent")
    with sqlite3.connect(path) as db:
        amount, status, data = db.execute(
            "SELECT accounted_usd,cost_status,data FROM vision_runs"
        ).fetchone()
        assert amount == "0.05" and status == "HELD_UNKNOWN"
        assert json.loads(data)["error_code"] == "SOURCE_REMOVED"
        assert "SYNTHETIC-SELLER" not in data


def test_wrong_worker_hmac_key_cannot_silently_replay(tmp_path):
    queue = Queue(tmp_path / "queue.db")
    queue.bind(SECRET)
    from radar.ebay_deletion.store import Conflict

    with pytest.raises(Conflict):
        queue.bind(b"wrong-worker-key" * 4)


def test_ssh_ledger_sync_rejects_rollback_and_requires_freshness(tmp_path):
    from radar.ebay_deletion.store import Conflict
    from radar.ebay_deletion.sync import require_fresh, sync_ledger

    remote, local = tmp_path / "remote.db", tmp_path / "local.db"
    queue = Queue(remote)
    queue.bind(SECRET)
    key, ids = event(json.dumps(BODY).encode(), SECRET)
    queue.accept(key, ids)
    with pytest.raises(ValueError):
        require_fresh(local)
    sync_ledger(remote, local, SECRET)
    require_fresh(local)
    with queue.connect() as db:
        db.execute("DELETE FROM events")
    with pytest.raises(Conflict):
        sync_ledger(remote, local, SECRET)
    assert len(Queue(local).events()) == 1
    with Queue(local).connect() as db:
        db.execute("UPDATE metadata SET value='0' WHERE key='last_sync'")
    with pytest.raises(ValueError):
        require_fresh(local)


def test_missing_userid_never_treats_unmapped_username_as_match():
    assert (
        identity_state(
            [{"seller_id": "SYNTHETIC-SOMEONE-ELSE"}],
            {"eiasToken": digest(SECRET, "eiasToken", "SYNTHETIC-EIAS")},
            SECRET,
            {},
        )
        == "UNMAPPED"
    )


def test_comparable_removal_preserves_other_source_and_owner_link(synthetic_db):
    path, listing = synthetic_db
    with sqlite3.connect(path) as db:
        db.execute(
            "INSERT INTO comparable_sales(id,data,listing_id,created_at) VALUES (1,?,?,?)",
            (
                json.dumps({"source": "eBay", "seller": {"userId": "SYNTHETIC-ACCOUNT"}}),
                listing,
                "2026-11-01",
            ),
        )
        unrelated = json.dumps({"source": "HiBid", "seller_id": "SYNTHETIC-SELLER"})
        db.execute(
            "INSERT INTO comparable_sales(id,data,listing_id,created_at) VALUES (2,?,?,?)",
            (unrelated, listing, "2026-11-01"),
        )
    ids = event(json.dumps(BODY).encode(), SECRET)[1]
    assert sanitize_database(path, ids, SECRET)[0] == "SANITIZED"
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT id,data,listing_id FROM comparable_sales").fetchall() == [
            (2, unrelated, listing)
        ]


def test_historical_source_change_does_not_hide_ebay_snapshot(synthetic_db):
    path, listing = synthetic_db
    snapshot = {
        "source": "eBay",
        "raw_title": "SYNTHETIC-SELLER",
        "seller": {"userId": "SYNTHETIC-ACCOUNT"},
    }
    with sqlite3.connect(path) as db:
        current = json.dumps({"source": "owner-research", "raw_title": "Independent owner work"})
        db.execute("UPDATE listings SET data=?,original_data='{}' WHERE id=?", (current, listing))
        db.execute(
            "UPDATE listing_edits SET before_data=?,after_data=? WHERE listing_id=?",
            (json.dumps(snapshot), current, listing),
        )
    ids = event(json.dumps(BODY).encode(), SECRET)[1]
    assert (
        sanitize_database(
            path,
            ids,
            SECRET,
            aliases={digest(SECRET, "username", "SYNTHETIC-SELLER"): ids["userId"]},
        )[0]
        == "SANITIZED"
    )
    with sqlite3.connect(path) as db:
        assert (
            db.execute("SELECT data FROM listings WHERE id=?", (listing,)).fetchone()[0] == current
        )
        before, after = db.execute(
            "SELECT before_data,after_data FROM listing_edits WHERE listing_id=?", (listing,)
        ).fetchone()
        assert "SYNTHETIC-SELLER" not in before
        assert after == current
