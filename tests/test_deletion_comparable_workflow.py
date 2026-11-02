"""Actual comparable importer and use-comparables workflow, with synthetic records only."""

import json
import sqlite3
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from radar.db import ComparableSale, Listing, MakerCase, MakerRun
from radar.ebay_deletion.processing import sanitize_database
from radar.ebay_deletion.protocol import digest
from radar.identification import service as identification
from radar.main import create_app
from radar.schemas import ComparableInput, ListingInput, Preferences

SECRET = b"SYNTHETIC-comparable-review-key-0000000000"
ACCOUNT = "SYNTHETIC-COMPARABLE-ACCOUNT"
SELLER = "SYNTHETIC-COMPARABLE-SELLER"
PRIVATE_EVIDENCE = "SYNTHETIC-SELLER-PERSONAL-EVIDENCE-421"


def database_dump(path):
    with sqlite3.connect(path) as db:
        return list(db.iterdump())


def residual_copies(path):
    with sqlite3.connect(path) as db:
        return {
            table: [
                row[0]
                for row in db.execute("SELECT " + column + " FROM " + table)
                if PRIVATE_EVIDENCE in row[0]
            ]
            for table, column in (
                ("comparable_sales", "data"),
                ("listings", "data"),
                ("listing_edits", "after_data"),
                ("maker_scenarios", "data"),
            )
        }


@pytest.fixture
def purchased_comparable_workflow(tmp_path, request):
    path = tmp_path / "actual-comparable-workflow.db"
    parent_name = getattr(request, "param", "target")
    with TestClient(create_app("sqlite:///" + str(path))) as client:
        with client.app.state.sessions.begin() as session:
            target = Listing(
                dedup_key="SYNTHETIC purchased eBay item",
                data=ListingInput(
                    source="eBay",
                    raw_title="SYNTHETIC purchased ring",
                    seller_id=SELLER,
                    item_price="23.4500",
                    buyer_premium="1.2000",
                    buyer_service_fee="0.3000",
                    sales_tax="0.4000",
                    shipping_cost="5.6000",
                    handling_fee="0.7000",
                    synthetic=True,
                ).model_dump(mode="json"),
                original_data={
                    "provider": "eBay Browse",
                    "mode": "production",
                    "item": {"seller": {"userId": ACCOUNT, "username": SELLER}},
                },
            )
            second = Listing(
                dedup_key="SYNTHETIC independent HiBid item",
                data=ListingInput(
                    source="HiBid",
                    raw_title="SYNTHETIC second ring",
                    item_price="31.1200",
                    synthetic=True,
                ).model_dump(mode="json"),
                original_data={"independent": "SYNTHETIC owner data"},
            )
            unrelated = Listing(
                dedup_key="SYNTHETIC unaffected owner item",
                data=ListingInput(
                    source="owner-research",
                    raw_title="Independent owner work",
                    item_price="42.3400",
                    synthetic=True,
                ).model_dump(mode="json"),
                original_data={},
            )
            session.add_all([target, second, unrelated])
            session.flush()
            ids = {"target": target.id, "second": second.id, "unrelated": unrelated.id}
        for name in ("target", "unrelated"):
            response = client.post(
                f"/listings/{ids[name]}/feedback",
                data={"action": "Purchased", "note": "SYNTHETIC owner purchase"},
            )
            assert response.status_code == 200
        imported = ComparableInput(
            category="ring",
            materials="SYNTHETIC owner-observed metal",
            condition="SYNTHETIC used",
            sale_price="69.8700",
            selling_fees="7.6500",
            sale_date="2026-10-31",
            evidence=PRIVATE_EVIDENCE,
            notes="SYNTHETIC seller-linked resale record",
            listing_id=ids[parent_name],
            synthetic=True,
        ).model_dump(mode="json")
        assert "source" not in imported and "seller" not in imported
        response = client.post(
            "/comparables",
            data={"format": "json", "content": json.dumps([imported])},
            follow_redirects=False,
        )
        assert response.status_code == 303
        independent_input = ComparableInput(
            category="ring",
            materials="SYNTHETIC independent owner metal",
            condition="Independent",
            sale_price="80.00",
            sale_date="2026-10-31",
            evidence="Independent retained comparable",
            synthetic=True,
        ).model_dump(mode="json")
        response = client.post(
            "/comparables",
            data={"format": "json", "content": json.dumps([independent_input])},
            follow_redirects=False,
        )
        assert response.status_code == 303
        with client.app.state.sessions() as session:
            comparable = session.scalar(
                select(ComparableSale).where(ComparableSale.listing_id == ids[parent_name])
            )
            ids["comparable"] = comparable.id
        response = client.post(
            f"/listings/{ids['second']}/use-comparables",
            data={"confirm": "yes", "comparable_id": str(ids["comparable"])},
            follow_redirects=False,
        )
        assert response.status_code == 303
        # Use the actual scenario copy builder with a synthetic candidate and no external search.
        with client.app.state.sessions.begin() as session:
            case = identification.new_case(
                session, "Independent owner case", "SYNTHETIC research", listing_id=ids["second"]
            )
            candidate = {"id": "SYNTHETIC-owner-reference", "maker": "SYNTHETIC Owner Maker"}
            session.add(MakerRun(case_id=case.id, data={"candidates": [candidate], "query": {}}))
            session.flush()
            scenario = identification.scenario(
                session, case.id, candidate["id"], [ids["comparable"]], Preferences()
            )
            ids["scenario"] = scenario.id
    copies = residual_copies(path)
    assert all(copies.values()), copies
    return path, ids


@pytest.mark.parametrize("original_present", [True, False])
def test_real_comparable_workflow_holds_before_mutation(
    purchased_comparable_workflow, original_present
):
    path, ids = purchased_comparable_workflow
    if not original_present:
        with sqlite3.connect(path) as db:
            db.execute("DELETE FROM comparable_sales WHERE id=?", (ids["comparable"],))
    before = database_dump(path)
    identifiers = {"userId": digest(SECRET, "userId", ACCOUNT)}
    aliases = {digest(SECRET, "username", SELLER): identifiers["userId"]}
    assert sanitize_database(path, identifiers, SECRET, aliases=aliases) == (
        "REVIEW_COMPARABLE_PROVENANCE",
        0,
    )
    assert database_dump(path) == before


def test_dangling_use_comparables_references_hold_without_original_or_scenario(
    purchased_comparable_workflow,
):
    path, ids = purchased_comparable_workflow
    with sqlite3.connect(path) as db:
        db.execute("DELETE FROM comparable_sales WHERE id=?", (ids["comparable"],))
        db.execute("DELETE FROM maker_scenarios")
    copies = residual_copies(path)
    assert not copies["comparable_sales"] and not copies["maker_scenarios"]
    assert copies["listings"] and copies["listing_edits"]
    before = database_dump(path)
    identifiers = {"userId": digest(SECRET, "userId", ACCOUNT)}
    aliases = {digest(SECRET, "username", SELLER): identifiers["userId"]}
    assert sanitize_database(path, identifiers, SECRET, aliases=aliases) == (
        "REVIEW_COMPARABLE_PROVENANCE",
        0,
    )
    assert database_dump(path) == before


def test_legacy_explicit_source_comparable_holds_for_existing_dependent_copies(
    purchased_comparable_workflow,
):
    path, ids = purchased_comparable_workflow
    # This simulates an older stored record; the actual importer rejects these fields.
    # Remove scenarios so their source-less snapshots cannot mask the ID-copy branch.
    with sqlite3.connect(path) as db:
        value = json.loads(
            db.execute(
                "SELECT data FROM comparable_sales WHERE id=?", (ids["comparable"],)
            ).fetchone()[0]
        )
        value.update(source="eBay", seller={"userId": ACCOUNT, "username": SELLER})
        db.execute(
            "UPDATE comparable_sales SET data=? WHERE id=?", (json.dumps(value), ids["comparable"])
        )
        db.execute("DELETE FROM maker_scenarios")
    before = database_dump(path)
    identifiers = {"userId": digest(SECRET, "userId", ACCOUNT)}
    aliases = {digest(SECRET, "username", SELLER): identifiers["userId"]}
    assert sanitize_database(path, identifiers, SECRET, aliases=aliases) == (
        "REVIEW_COMPARABLE_PROVENANCE",
        0,
    )
    assert database_dump(path) == before


@pytest.mark.parametrize("purchased_comparable_workflow", ["unrelated"], indirect=True)
def test_independent_comparable_workflow_is_preserved_without_hold(purchased_comparable_workflow):
    path, ids = purchased_comparable_workflow
    tables = ("comparable_sales", "listing_edits", "maker_cases", "maker_runs", "maker_scenarios")
    with sqlite3.connect(path) as db:
        before = {table: db.execute("SELECT * FROM " + table).fetchall() for table in tables}
        unrelated_listings = db.execute(
            "SELECT * FROM listings WHERE id!=?", (ids["target"],)
        ).fetchall()
    identifiers = {"userId": digest(SECRET, "userId", ACCOUNT)}
    aliases = {digest(SECRET, "username", SELLER): identifiers["userId"]}
    assert sanitize_database(path, identifiers, SECRET, aliases=aliases)[0] == "SANITIZED"
    with sqlite3.connect(path) as db:
        for table in tables:
            assert db.execute("SELECT * FROM " + table).fetchall() == before[table]
        assert (
            db.execute("SELECT * FROM listings WHERE id!=?", (ids["target"],)).fetchall()
            == unrelated_listings
        )
        current = json.loads(
            db.execute("SELECT data FROM listings WHERE id=?", (ids["target"],)).fetchone()[0]
        )
        assert Decimal(current["item_price"]) == Decimal("23.4500")
        assert (
            db.execute(
                "SELECT action FROM feedback WHERE listing_id=?", (ids["target"],)
            ).fetchone()[0]
            == "Purchased"
        )


@pytest.mark.parametrize("purchased_comparable_workflow", ["unrelated"], indirect=True)
def test_nested_ebay_reference_does_not_taint_independent_purchase_comparables(
    purchased_comparable_workflow,
):
    path, ids = purchased_comparable_workflow
    with TestClient(create_app("sqlite:///" + str(path))) as client:
        with client.app.state.sessions.begin() as session:
            independent = session.get(Listing, ids["unrelated"])
            independent.data = {**independent.data, "source": "HiBid"}
            case = MakerCase(
                listing_id=independent.id,
                data={"source": "owner-research", "title": "Independent owner comparison"},
            )
            session.add(case)
            session.flush()
            run = MakerRun(
                case_id=case.id,
                data={
                    "source": "owner-research",
                    "note": "Independent purchase study with separate eBay reference",
                    "reference_snapshot": session.get(Listing, ids["target"]).data,
                },
            )
            session.add(run)
            session.flush()
            run_id = run.id
    tables = ("comparable_sales", "listing_edits", "maker_cases", "maker_scenarios")
    with sqlite3.connect(path) as db:
        before = {table: db.execute("SELECT * FROM " + table).fetchall() for table in tables}
        independent_listings = db.execute(
            "SELECT * FROM listings WHERE id!=?", (ids["target"],)
        ).fetchall()
        independent_runs = db.execute("SELECT * FROM maker_runs WHERE id!=?", (run_id,)).fetchall()
        independent_feedback = db.execute(
            "SELECT * FROM feedback WHERE listing_id!=?", (ids["target"],)
        ).fetchall()
    identifiers = {"userId": digest(SECRET, "userId", ACCOUNT)}
    aliases = {digest(SECRET, "username", SELLER): identifiers["userId"]}
    assert sanitize_database(path, identifiers, SECRET, aliases=aliases)[0] == "SANITIZED"
    with sqlite3.connect(path) as db:
        for table in tables:
            assert db.execute("SELECT * FROM " + table).fetchall() == before[table]
        assert (
            db.execute("SELECT * FROM listings WHERE id!=?", (ids["target"],)).fetchall()
            == independent_listings
        )
        assert db.execute("SELECT * FROM maker_runs WHERE id!=?", (run_id,)).fetchall() == (
            independent_runs
        )
        assert (
            db.execute("SELECT * FROM feedback WHERE listing_id!=?", (ids["target"],)).fetchall()
            == independent_feedback
        )
        changed = json.loads(
            db.execute("SELECT data FROM maker_runs WHERE id=?", (run_id,)).fetchone()[0]
        )
    assert changed["source"] == "owner-research"
    assert changed["note"] == "Independent purchase study with separate eBay reference"
    assert SELLER not in json.dumps(changed["reference_snapshot"])
    assert changed["reference_snapshot"]["item_price"] == "23.4500"


def current_comparable_ledger(tmp_path, monkeypatch):
    from radar.ebay_deletion import sync
    from radar.ebay_deletion.store import Queue

    receiver = Queue(tmp_path / "synthetic-receiver.db")
    receiver.bind(SECRET)
    receiver.accept(
        digest(SECRET, "notification", "SYNTHETIC-COMPARABLE-EVENT"),
        {"userId": digest(SECRET, "userId", ACCOUNT)},
    )
    local = tmp_path / "synthetic-local-ledger.db"
    sync.sync_ledger(receiver.path, local, SECRET)
    monkeypatch.setattr(
        sync, "_pull_receiver", lambda nonce: sync.receiver_bundle(receiver.path, SECRET, nonce)
    )
    return local


def write_manifest(path, destination):
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "inventory_complete": True,
                "assets": [{"kind": "sqlite", "path": str(destination)}],
                "aliases": {digest(SECRET, "username", SELLER): digest(SECRET, "userId", ACCOUNT)},
            }
        )
    )
    return path


def test_replay_reports_unresolved_comparable_copy_without_completion(
    purchased_comparable_workflow, tmp_path, monkeypatch
):
    from radar.ebay_deletion.processing import replay
    from radar.ebay_deletion.store import Queue

    database, _ = purchased_comparable_workflow
    before = database_dump(database)
    queue = current_comparable_ledger(tmp_path, monkeypatch)
    manifest = write_manifest(tmp_path / "replay-manifest.json", database)
    assert replay(manifest, queue, SECRET) == ["REVIEW_COMPARABLE_PROVENANCE"]
    assert database_dump(database) == before
    with Queue.existing(queue).read() as db:
        assert db.execute("SELECT status FROM receipts").fetchall() == [
            ("REVIEW_COMPARABLE_PROVENANCE",)
        ]


def test_restore_keeps_unresolved_comparable_copies_quarantined(
    purchased_comparable_workflow, tmp_path, monkeypatch
):
    from radar.ebay_deletion.cli import verified_restore

    source, _ = purchased_comparable_workflow
    before = database_dump(source)
    queue = current_comparable_ledger(tmp_path, monkeypatch)
    destination = tmp_path / "quarantined-restore.db"
    manifest = write_manifest(tmp_path / "restore-manifest.json", destination)
    with pytest.raises(ValueError, match="remains quarantined"):
        verified_restore(source, destination, manifest, queue, SECRET)
    assert database_dump(source) == before
    assert database_dump(destination) == before


@pytest.mark.parametrize("ancestry", ["original", "historical_edit"])
def test_original_account_provenance_keeps_comparable_dependencies_in_scope(
    purchased_comparable_workflow,
    ancestry,
):
    path, ids = purchased_comparable_workflow
    with sqlite3.connect(path) as db:
        current = json.loads(
            db.execute("SELECT data FROM listings WHERE id=?", (ids["target"],)).fetchone()[0]
        )
        historical = dict(current)
        current["source"] = "owner-research"
        db.execute("UPDATE listings SET data=? WHERE id=?", (json.dumps(current), ids["target"]))
        if ancestry == "historical_edit":
            # Isolate ancestry established only by a retained historical eBay edit.
            db.execute("UPDATE listings SET original_data='{}' WHERE id=?", (ids["target"],))
            db.execute(
                "INSERT INTO listing_edits(listing_id,before_data,after_data,created_at) "
                "VALUES (?,?,?,'2026-11-01')",
                (ids["target"], json.dumps(historical), json.dumps(current)),
            )
    # Purchased decision and imported comparable remain; ancestry is explicit in one store.
    before = database_dump(path)
    identifiers = {"userId": digest(SECRET, "userId", ACCOUNT)}
    aliases = {digest(SECRET, "username", SELLER): identifiers["userId"]}
    assert sanitize_database(path, identifiers, SECRET, aliases=aliases) == (
        "REVIEW_COMPARABLE_PROVENANCE",
        0,
    )
    assert database_dump(path) == before


@pytest.mark.parametrize("relation", ["column_only", "json_only", "conflicting"])
def test_each_stored_comparable_relation_requires_review(purchased_comparable_workflow, relation):
    """Synthetic malformed/legacy copies isolate each relation after a valid import."""
    path, ids = purchased_comparable_workflow
    with sqlite3.connect(path) as db:
        # Remove derived copies from this isolated relation test; they are covered above.
        prior = db.execute(
            "SELECT before_data FROM listing_edits WHERE listing_id=?", (ids["second"],)
        ).fetchone()[0]
        db.execute("UPDATE listings SET data=? WHERE id=?", (prior, ids["second"]))
        db.execute("DELETE FROM listing_edits WHERE listing_id=?", (ids["second"],))
        db.execute("DELETE FROM maker_scenarios")
        payload = json.loads(
            db.execute(
                "SELECT data FROM comparable_sales WHERE id=?", (ids["comparable"],)
            ).fetchone()[0]
        )
        if relation == "column_only":
            payload["listing_id"] = None
        elif relation == "conflicting":
            payload["listing_id"] = ids["unrelated"]
        else:
            db.execute(
                "UPDATE comparable_sales SET listing_id=NULL WHERE id=?", (ids["comparable"],)
            )
        db.execute(
            "UPDATE comparable_sales SET data=? WHERE id=?",
            (json.dumps(payload), ids["comparable"]),
        )
    before = database_dump(path)
    identifiers = {"userId": digest(SECRET, "userId", ACCOUNT)}
    aliases = {digest(SECRET, "username", SELLER): identifiers["userId"]}
    assert sanitize_database(path, identifiers, SECRET, aliases=aliases) == (
        "REVIEW_COMPARABLE_PROVENANCE",
        0,
    )
    assert database_dump(path) == before
