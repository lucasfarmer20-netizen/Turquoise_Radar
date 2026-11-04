"""Synthetic local records exercise evaluation; no live sourcing yield is claimed."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import event

from radar.anomalies import PatternInput, add_pattern
from radar.db import (
    AlertMessage,
    AlertObservation,
    DiscoveredItem,
    DiscoveryObservation,
    DiscoveryRun,
    Feedback,
    IntakeSource,
    Listing,
    SearchExecution,
    Setting,
    database,
)
from radar.ebay_deletion.processing import tombstone
from radar.schemas import Shipping
from radar.search_evaluation import evaluate_searches
from radar.sourcing_catalog import catalog_entries

NOW = datetime(2026, 11, 3, 12, tzinfo=UTC)


@pytest.fixture
def session(tmp_path):
    engine, sessions = database(f"sqlite:///{tmp_path / 'evaluation.db'}")
    with sessions() as session:
        yield session
    engine.dispose()


def record(session, item, key, *, query=None, mode="production", linked=True, source="eBay"):
    data = item.model_dump(mode="json") | dict(source=source, synthetic=mode != "production")
    if query is not None:
        data["discovery_mode"] = mode
    owner = None
    if linked:
        owner = Listing(dedup_key=key, data=data, original_data={}, last_seen=NOW)
        session.add(owner)
        session.flush()
    candidate = DiscoveredItem(
        identity=key,
        source=source,
        ebay_item_id=key,
        normalized=data,
        original_data={},
        listing_id=owner.id if owner else None,
        last_seen=NOW,
    )
    session.add(candidate)
    session.flush()
    execution = None
    if query is not None:
        run = DiscoveryRun(mode=mode, options={})
        session.add(run)
        session.flush()
        execution = SearchExecution(
            run_id=run.id,
            source=source,
            mode=mode,
            group_ids=[],
            original_query=query,
            transmitted_query=query,
            parameters={"pages": [{"q": query}]},
            status="COMPLETED",
        )
        session.add(execution)
        session.flush()
        observe(session, candidate, execution)
    return owner, candidate, execution


def observe(session, candidate, execution, corrected=None):
    session.add(
        DiscoveryObservation(
            item_id=candidate.id,
            execution_id=execution.id,
            raw_data={"SYNTHETIC PRIVATE PAYLOAD": "not projected"},
            corrected_query=corrected,
            correction_state="CORRECTED" if corrected else "UNKNOWN",
        )
    )


def pattern(session, phrase, active=False):
    return add_pattern(
        session,
        PatternInput(
            phrase=phrase,
            original_phrase=phrase,
            canonical_concept="SYNTHETIC owner search",
            anomaly_category="manual",
            active=active,
        ),
    )


def test_observations_and_unique_items_do_not_multiply_feedback(session, item, prefs):
    owner, candidate, execution = record(session, item, "a", query="blue stone ring")
    observe(session, candidate, execution)
    observe(session, candidate, execution)
    _, unlinked, _ = record(session, item, "b", query="blue stone ring", linked=False)
    record(session, item, "c", query="blue stone ring", source="Other source")
    session.add_all(
        [Feedback(listing_id=owner.id, action="Interested", created_at=NOW) for _ in range(3)]
    )
    session.commit()
    result = evaluate_searches(session, prefs)
    rows = {row["source"]: row for row in result["phrases"]}
    assert rows["eBay"]["observations"] == 4
    assert rows["eBay"]["unique_items"] == 2
    assert rows["eBay"]["interested"] == rows["eBay"]["reviewed_items"] == 1
    assert rows["Other source"]["observations"] == 1
    suggestion = next(row for row in result["suggestions"] if row["phrase"] == "blue stone ring")
    assert suggestion["observations"] == 5 and suggestion["unique_items"] == 3
    assert suggestion["interested"] == 1
    assert suggestion["tested"] and suggestion["evidence_label"] == "OBSERVED SOURCE MATCH"
    assert {link["url"] for link in rows["eBay"]["evidence"]} == {
        f"/listings/{owner.id}",
        f"/discovery/items/{unlinked.id}",
    }
    assert all(link["title"] == item.raw_title for link in rows["eBay"]["evidence"])
    assert "PRIVATE PAYLOAD" not in repr(result)


@pytest.mark.parametrize(
    "action, field",
    [
        ("Interested", "interested"),
        ("Passed", "passed"),
        ("False positive", "false_positive"),
        ("Purchased", "purchased"),
        ("Sold", "sold"),
    ],
)
def test_latest_owner_action_is_distinct_from_history_and_screening(
    session, item, prefs, action, field
):
    owner, _, _ = record(session, item, "a", query="blue stone ring")
    owner.review_state = "PASSED"
    session.add(Feedback(listing_id=owner.id, action="Interested", created_at=datetime(2026, 1, 1)))
    session.add_all(
        [Feedback(listing_id=owner.id, action=action, created_at=NOW) for _ in range(2)]
    )
    session.commit()
    row = evaluate_searches(session, prefs)["phrases"][0]
    assert row["reviewed_items"] == 1 and row[field] == 1
    assert sum(row[f] for f in ("interested", "passed", "false_positive", "purchased", "sold")) == 1


def test_corrections_keep_exact_query_and_separate_attribution(session, item, prefs):
    exact = "  turqoise RING  "
    pattern(session, exact)
    _, candidate, execution = record(session, item, "a", query=exact)
    observe(session, candidate, execution, corrected="turquoise ring")
    session.commit()
    result = evaluate_searches(session, prefs)
    assert {row["phrase"] for row in result["phrases"]} == {exact}
    rows = {row["attribution"]: row for row in result["phrases"]}
    assert rows["SOURCE QUERY"]["observations"] == 1
    assert rows["SOURCE CORRECTED"]["corrected_queries"] == ["turquoise ring"]
    suggested = next(row for row in result["suggestions"] if row["phrase"] == exact)
    assert suggested["observations"] == 2 and suggested["corrected_observations"] == 1
    assert suggested["unique_items"] == 1


def test_title_only_vocabulary_is_untested_and_not_owner_query_success(session, item, prefs):
    for phrase in ("ring", "earring", "sterling"):
        pattern(session, phrase)
    owner, _, _ = record(
        session,
        item.model_copy(update={"raw_title": "SYNTHETIC PRIVATE TITLE sterling earrings"}),
        "a",
    )
    session.add(Feedback(listing_id=owner.id, action="Interested", created_at=NOW))
    session.commit()
    result = evaluate_searches(session, prefs)
    suggestions = {row["phrase"]: row for row in result["suggestions"]}
    assert suggestions["sterling"]["title_matches"] == 1
    assert suggestions["ring"]["title_matches"] == suggestions["earring"]["title_matches"] == 0
    assert not suggestions["sterling"]["tested"]
    assert suggestions["sterling"]["evidence_label"] == "UNTESTED SEARCH"
    assert suggestions["sterling"]["interested"] == 0
    assert suggestions["sterling"]["evidence"] == [
        dict(
            key=f"listing:{owner.id}",
            url=f"/listings/{owner.id}",
            title="SYNTHETIC PRIVATE TITLE sterling earrings",
        )
    ]
    assert result["phrases"] == []


def test_provisional_and_recommended_alerts_do_not_become_source_yield(session, item, prefs):
    _, candidate, _ = record(session, item, "a")
    session.add(IntakeSource(id="test", data={}))
    session.flush()
    message = AlertMessage(
        source_id="test",
        digest="synthetic",
        raw_b64="",
        safe_text="",
        subject="Synthetic",
        message_type="SEARCH_MATCH",
        data={},
        synthetic=False,
    )
    session.add(message)
    session.flush()
    session.add(
        AlertObservation(
            message_id=message.id,
            item_id=candidate.id,
            query_text="blue stone ring",
            attribution_verified=False,
            data={
                "card_occurrences": [
                    dict(query_text="blue stone ring", attribution_verified=False, data={}),
                    dict(
                        query_text="blue stone ring",
                        attribution_verified=True,
                        data={"message_role": "RECOMMENDED"},
                    ),
                    dict(
                        query_text="blue stone ring",
                        attribution_verified=True,
                        data={"message_role": "SEARCH_SECTION"},
                    ),
                ]
            },
        )
    )
    session.commit()
    result = evaluate_searches(session, prefs)
    assert result["sources"][0]["observations"] == 3
    assert result["sources"][0]["attributed_observations"] == 1
    assert result["phrases"][0]["observations"] == 1


def test_nonlive_mixed_deleted_records_are_excluded_but_closed_real_records_remain(
    session, item, prefs
):
    for key, mode in (("fixture", "fixture"), ("sandbox", "sandbox")):
        record(session, item, key, query="blue stone ring", mode=mode)
    owner, mixed, execution = record(session, item, "mixed", query="blue stone ring")
    execution.mode = "fixture"
    deleted, _, _ = record(session, item, "deleted", query="blue stone ring")
    deleted.data = tombstone(deleted.data)
    deleted.original_data = item.model_dump(mode="json")
    closed_item = item.model_copy(
        update={"shipping_status": Shipping.UNKNOWN, "shipping_cost": None}
    )
    closed, _, _ = record(session, closed_item, "closed", query="blue stone ring")
    closed.review_state = "PASSED"
    session.commit()
    result = evaluate_searches(session, prefs)
    assert result["sources"] == [
        dict(
            source="eBay",
            items=1,
            observations=1,
            attributed_observations=1,
            missing_cost_items=1,
            unknown_shipping_items=1,
            unavailable_shipping_items=0,
        )
    ]
    assert result["phrases"][0]["unique_items"] == 1
    assert result["phrases"][0]["passed"] == 0  # Screening state is not owner feedback.


def test_failed_bookkeeping_and_zero_results_cannot_establish_observed_query(session, prefs):
    run = DiscoveryRun(mode="production", options={})
    session.add(run)
    session.flush()
    for status, pages in (("FAILED", []), ("COMPLETED", [{"q": "blue stone ring"}])):
        session.add(
            SearchExecution(
                run_id=run.id,
                source="eBay",
                mode="production",
                group_ids=[],
                original_query="blue stone ring",
                transmitted_query="blue stone ring",
                parameters={"pages": pages},
                status=status,
            )
        )
    session.commit()
    result = evaluate_searches(session, prefs)
    assert result["sources"] == result["phrases"] == []
    assert not any(row["tested"] for row in result["suggestions"])
    assert any("Failed bookkeeping" in text for text in result["limitations"])


def test_catalog_and_35_existing_patterns_are_bounded_without_rewriting(session, prefs):
    catalog = {entry["phrase"] for entry in catalog_entries()}
    for i in range(40):
        pattern(session, f"  owner exact {i:02d}  ")
    session.commit()
    result = evaluate_searches(session, prefs)
    assert len(result["suggestions"]) == 100
    assert result["omitted_suggestions"] == 5
    assert {row["phrase"] for row in result["suggestions"]} == catalog | {
        f"  owner exact {i:02d}  " for i in range(35)
    }


def test_more_unique_items_rank_ahead_of_repeated_observations(session, item, prefs):
    record(session, item, "a", query="blue stone ring")
    record(session, item, "b", query="blue stone ring")
    _, candidate, execution = record(session, item, "c", query="sterling jewelry lot")
    for _ in range(9):
        observe(session, candidate, execution)
    session.commit()
    suggestions = evaluate_searches(session, prefs)["suggestions"]
    phrases = [row["phrase"] for row in suggestions]
    assert phrases.index("blue stone ring") < phrases.index("sterling jewelry lot")
    repeated = next(row for row in suggestions if row["phrase"] == "sterling jewelry lot")
    assert repeated["observations"] == 10 and repeated["unique_items"] == 1


def test_projection_does_not_change_patterns_settings_or_flush_pending_work(session, item, prefs):
    saved = pattern(session, "blue stone ring")
    record(session, item, "a", query="blue stone ring")
    session.commit()
    saved.priority_weight = 17
    pending = Setting(id=3, data={"enabled": False})
    session.add(pending)
    statements = []

    def capture(connection, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(session.bind, "before_cursor_execute", capture)
    try:
        evaluate_searches(session, prefs)
    finally:
        event.remove(session.bind, "before_cursor_execute", capture)
    assert statements and all(
        statement.lstrip().upper().startswith("SELECT") for statement in statements
    )
    assert saved.priority_weight == 17 and not saved.active
    assert set(session.dirty) == {saved} and set(session.new) == {pending}
