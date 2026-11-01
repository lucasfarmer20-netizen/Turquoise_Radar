"""Review regressions use temporary synthetic databases and explicit evidence provenance."""

import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from radar.db import Listing, ListingEdit, MakerCase, MakerDecision, MakerRun, MakerScenario
from radar.ebay_deletion.processing import identity_state, sanitize_database
from radar.ebay_deletion.protocol import digest
from radar.main import create_app
from radar.schemas import ListingInput

SECRET = b"SYNTHETIC-scope-review-key-0000000000000000"
ACCOUNT = "SYNTHETIC-TARGET-ACCOUNT"
OTHER = "SYNTHETIC-OTHER-ACCOUNT"
SELLER = "SYNTHETIC-TARGET-SELLER"
IDENTIFIERS = {"userId": digest(SECRET, "userId", ACCOUNT)}
ALIASES = {digest(SECRET, "username", SELLER): IDENTIFIERS["userId"]}


def listing_data(source="eBay", seller=SELLER):
    return ListingInput(
        source=source,
        seller_id=seller,
        raw_title="SYNTHETIC title from " + seller,
        raw_description="SYNTHETIC evidence from " + seller,
        item_price="23.45",
    ).model_dump(mode="json")


def dump(path):
    with sqlite3.connect(path) as db:
        return list(db.iterdump())


def test_mixed_case_only_removes_affected_copies_and_preserves_owner_work(tmp_path):
    path = tmp_path / "mixed.db"
    target, independent = listing_data(), listing_data("HiBid", "SYNTHETIC-HIBID-SELLER")
    original = {"provider": "eBay Browse", "item": {"seller": {"userId": ACCOUNT}}}
    with TestClient(create_app("sqlite:///" + str(path))) as client:
        with client.app.state.sessions.begin() as session:
            row = Listing(dedup_key="synthetic-target", data=target, original_data=original)
            session.add(row)
            session.flush()
            session.add(ListingEdit(listing_id=row.id, before_data=independent, after_data=target))
            case = MakerCase(
                listing_id=row.id,
                data={"source": "owner-research", "title": "Independent owner case"},
            )
            session.add(case)
            session.flush()
            target_run = MakerRun(case_id=case.id, data={"copied": target})
            other_run = MakerRun(
                case_id=case.id,
                data={"source": "HiBid", "note": "Independent research", "copied": independent},
            )
            session.add_all([target_run, other_run])
            session.flush()
            affected_decision = MakerDecision(
                case_id=case.id,
                run_id=target_run.id,
                action="CONFIRM",
                data={"source": "owner-research", "copied": target},
            )
            independent_decision = MakerDecision(
                case_id=case.id,
                run_id=other_run.id,
                action="CONFIRM",
                candidate_id="SYNTHETIC-independent-maker-reference",
                data={"source": "owner-research", "note": "Owner independently checked HiBid"},
            )
            target_scenario = MakerScenario(case_id=case.id, data={"listing_snapshot": target})
            other_scenario = MakerScenario(
                case_id=case.id,
                data={"source": "owner-research", "listing_snapshot": independent},
            )
            session.add_all(
                [affected_decision, independent_decision, target_scenario, other_scenario]
            )
            session.flush()
            protected = {
                "maker_cases": case.id,
                "maker_runs": other_run.id,
                "maker_scenarios": other_scenario.id,
                "maker_decisions": independent_decision.id,
            }
            affected_ids = [target_run.id, target_scenario.id, affected_decision.id]
    with sqlite3.connect(path) as db:
        unchanged = {
            table: db.execute("SELECT * FROM " + table + " WHERE id=?", (key,)).fetchone()
            for table, key in protected.items()
        }
    status, _ = sanitize_database(path, IDENTIFIERS, SECRET, aliases=ALIASES)
    assert status == "SANITIZED"
    with sqlite3.connect(path) as db:
        for table, key in protected.items():
            assert (
                db.execute("SELECT * FROM " + table + " WHERE id=?", (key,)).fetchone()
                == (unchanged[table])
            )
        before, after = db.execute("SELECT before_data,after_data FROM listing_edits").fetchone()
        assert json.loads(before) == independent
        assert json.loads(after)["source"] == "eBay"
        assert SELLER not in after
        for table, key in zip(
            ("maker_runs", "maker_scenarios", "maker_decisions"), affected_ids, strict=True
        ):
            changed = db.execute("SELECT data FROM " + table + " WHERE id=?", (key,)).fetchone()
            assert changed is not None and SELLER not in changed[0]
        assert not db.execute("PRAGMA foreign_key_check").fetchall()


@pytest.mark.parametrize("prose_key", ["note", "maker"])
def test_unseparable_dependent_prose_holds_before_any_mutation(tmp_path, prose_key):
    path = tmp_path / "ambiguous-provenance.db"
    target = listing_data()
    with TestClient(create_app("sqlite:///" + str(path))) as client:
        with client.app.state.sessions.begin() as session:
            row = Listing(dedup_key="synthetic", data=target, original_data={})
            session.add(row)
            session.flush()
            case = MakerCase(
                listing_id=row.id,
                data={"source": "owner-research", "title": "Independent owner case"},
            )
            session.add(case)
            session.flush()
            session.add(
                MakerRun(
                    case_id=case.id,
                    data={"listing_snapshot": target, prose_key: "Unseparated text " + SELLER},
                )
            )
    before = dump(path)
    assert sanitize_database(path, IDENTIFIERS, SECRET, aliases=ALIASES) == (
        "REVIEW_PROVENANCE",
        0,
    )
    assert dump(path) == before


def test_direct_stable_match_conflicts_with_reviewed_alias(tmp_path):
    path = tmp_path / "alias-conflict.db"
    with TestClient(create_app("sqlite:///" + str(path))) as client:
        with client.app.state.sessions.begin() as session:
            session.add(
                Listing(
                    dedup_key="synthetic",
                    data=listing_data(),
                    original_data={"seller": {"userId": ACCOUNT}},
                )
            )
    before = dump(path)
    aliases = {digest(SECRET, "username", SELLER): digest(SECRET, "userId", OTHER)}
    assert sanitize_database(path, IDENTIFIERS, SECRET, aliases=aliases) == (
        "REVIEW_IDENTITY",
        0,
    )
    assert dump(path) == before


def test_conflicting_aliases_across_historical_snapshots_hold(tmp_path):
    path = tmp_path / "history-conflict.db"
    target, other = listing_data(), listing_data(seller="SYNTHETIC-OTHER-SELLER")
    with TestClient(create_app("sqlite:///" + str(path))) as client:
        with client.app.state.sessions.begin() as session:
            row = Listing(dedup_key="synthetic", data=target, original_data={})
            session.add(row)
            session.flush()
            session.add(ListingEdit(listing_id=row.id, before_data=other, after_data=target))
    aliases = {
        **ALIASES,
        digest(SECRET, "username", "SYNTHETIC-OTHER-SELLER"): digest(SECRET, "userId", OTHER),
    }
    before = dump(path)
    assert sanitize_database(path, IDENTIFIERS, SECRET, aliases=aliases) == (
        "REVIEW_IDENTITY",
        0,
    )
    assert dump(path) == before


def test_disjoint_immutable_namespaces_are_unmapped_not_nonmatch(tmp_path):
    raw = {"seller": {"eiasToken": "SYNTHETIC-EIAS"}}
    assert identity_state([raw], IDENTIFIERS, SECRET, {}) == "UNMAPPED"
    path = tmp_path / "namespace.db"
    with TestClient(create_app("sqlite:///" + str(path))) as client:
        with client.app.state.sessions.begin() as session:
            session.add(
                Listing(
                    dedup_key="synthetic",
                    data=ListingInput(source="eBay", raw_title="SYNTHETIC").model_dump(mode="json"),
                    original_data=raw,
                )
            )
    before = dump(path)
    assert sanitize_database(path, IDENTIFIERS, SECRET) == ("REVIEW_IDENTITY", 0)
    assert dump(path) == before


@pytest.mark.parametrize("provenance", ["explicit", "legacy"])
def test_original_payload_survives_source_relabel_and_cannot_be_overlooked(tmp_path, provenance):
    path = tmp_path / "source-relabel.db"
    target = listing_data()
    owner = ListingInput(
        source="owner-research", raw_title="Independent current record"
    ).model_dump(mode="json")
    raw = {"seller": {"userId": ACCOUNT, "username": SELLER}, "title": "Original " + SELLER}
    original = {"provider": "eBay Browse", "item": raw} if provenance == "explicit" else raw
    with TestClient(create_app("sqlite:///" + str(path))) as client:
        with client.app.state.sessions.begin() as session:
            row = Listing(dedup_key="synthetic", data=target, original_data=original)
            session.add(row)
            session.flush()
            session.add(ListingEdit(listing_id=row.id, before_data=target, after_data=owner))
            row.data = owner  # Keep the original eBay payload intact throughout the regression.
    before = dump(path)
    status, _ = sanitize_database(path, IDENTIFIERS, SECRET, aliases=ALIASES)
    if provenance == "legacy":
        assert status == "REVIEW_PROVENANCE"
        assert dump(path) == before
    else:
        assert status == "SANITIZED"
        with sqlite3.connect(path) as db:
            current, retained_original = db.execute(
                "SELECT data,original_data FROM listings"
            ).fetchone()
            assert json.loads(current) == owner
            assert json.loads(retained_original) == {}
            historical, independent = db.execute(
                "SELECT before_data,after_data FROM listing_edits"
            ).fetchone()
            assert SELLER not in historical
            assert json.loads(independent) == owner


@pytest.mark.parametrize("independent_source", ["HiBid", "owner-research"])
def test_matching_ebay_root_cannot_discard_independent_descendant(tmp_path, independent_source):
    path = tmp_path / "mixed-provenance-root.db"
    with TestClient(create_app("sqlite:///" + str(path))) as client:
        with client.app.state.sessions.begin() as session:
            row = Listing(dedup_key="synthetic", data=listing_data(), original_data={})
            session.add(row)
            session.flush()
            case = MakerCase(
                listing_id=row.id,
                data={"source": "owner-research", "title": "Independent case"},
            )
            session.add(case)
            session.flush()
            session.add(
                MakerRun(
                    case_id=case.id,
                    data={
                        "source": "eBay",
                        "seller_id": SELLER,
                        "raw_title": "Affected seller evidence",
                        "independent": {
                            "source": independent_source,
                            "note": "Independent owner research must survive",
                        },
                    },
                )
            )
    before = dump(path)
    assert sanitize_database(path, IDENTIFIERS, SECRET, aliases=ALIASES) == (
        "REVIEW_PROVENANCE",
        0,
    )
    assert dump(path) == before
