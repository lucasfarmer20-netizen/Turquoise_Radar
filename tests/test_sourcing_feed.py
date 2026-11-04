"""Synthetic database evidence only; no marketplace or private application access."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import event

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
    database,
)
from radar.ebay_deletion.processing import tombstone
from radar.intake.service import promote_candidate
from radar.schemas import Shipping
from radar.sourcing_feed import build_feed

NOW = datetime(2026, 11, 3, 12, tzinfo=UTC)


@pytest.fixture
def session(tmp_path, monkeypatch):
    monkeypatch.setattr("radar.sourcing_feed.utcnow", lambda: NOW)
    engine, sessions = database(f"sqlite:///{tmp_path / 'feed.db'}")
    with sessions() as session:
        yield session
    engine.dispose()


def owner_listing(session, item, key="manual", state="NEW"):
    row = Listing(
        dedup_key=key,
        data=item.model_dump(mode="json"),
        original_data={},
        review_state=state,
        last_seen=NOW,
    )
    session.add(row)
    session.flush()
    return row


def candidate(session, item, key="candidate", listing=None, state="NEW"):
    row = DiscoveredItem(
        identity=key,
        source=item.source,
        ebay_item_id=key,
        normalized=item.model_dump(mode="json"),
        original_data={},
        listing_id=listing.id if listing else None,
        inbox_status=state,
        last_seen=NOW,
    )
    session.add(row)
    session.flush()
    return row


def discovery(session, row, query="  turqoise ring  ", mode="production", corrected=None):
    run = DiscoveryRun(mode=mode, options={})
    session.add(run)
    session.flush()
    execution = SearchExecution(
        run_id=run.id,
        source="eBay",
        mode=mode,
        group_ids=[],
        original_query=query,
        transmitted_query=query,
        parameters={},
    )
    session.add(execution)
    session.flush()
    observation = DiscoveryObservation(
        execution_id=execution.id,
        item_id=row.id,
        raw_data={"seller": {"username": "SYNTHETIC RAW ID NEVER PROJECT"}},
        corrected_query=corrected,
        correction_state="CORRECTED" if corrected else "UNKNOWN",
        observed_at=NOW,
    )
    session.add(observation)
    session.flush()
    return observation, execution


def alert(session, row, *, kind="SEARCH_MATCH", synthetic=False, occurrences=None):
    if not session.get(IntakeSource, "feed-test"):
        session.add(IntakeSource(id="feed-test", data={}))
        session.flush()
    message = AlertMessage(
        source_id="feed-test",
        digest=f"synthetic-{len(session.identity_map)}-{row.id}-{kind}",
        raw_b64="SYNTHETIC RAW EMAIL NEVER PROJECT",
        safe_text="SYNTHETIC MESSAGE NEVER PROJECT",
        subject="Synthetic message",
        message_type=kind,
        data={},
        synthetic=synthetic,
    )
    session.add(message)
    session.flush()
    observation = AlertObservation(
        message_id=message.id,
        item_id=row.id,
        query_text="  blue rock ring  ",
        attribution_verified=False,
        data={"card_occurrences": occurrences} if occurrences else {},
        created_at=NOW,
    )
    session.add(observation)
    session.flush()
    return observation, message


def test_linked_candidates_share_owner_record_and_keep_manual_listings(session, item, prefs):
    owner_item = item.model_copy(update={"item_price": Decimal("30"), "raw_title": "Owner ring"})
    owner = owner_listing(session, owner_item, state="WATCH")
    first = candidate(session, item, "one", owner, state="IRRELEVANT")
    second = candidate(session, item.model_copy(update={"source": "ShopGoodwill"}), "two", owner)
    manual = owner_listing(session, item, "same-title-independent-manual")
    discovery(session, first)
    discovery(session, second, query="blue rock ring")
    interested = Feedback(listing_id=owner.id, action="Interested", created_at=NOW)
    session.add_all(
        [
            interested,
            Feedback(
                listing_id=owner.id,
                action="Passed",
                created_at=datetime(2026, 11, 2, tzinfo=UTC),
            ),
        ]
    )
    session.commit()

    cards = {card["listing_id"]: card for card in build_feed(session, prefs)}
    assert set(cards) == {owner.id, manual.id}
    card = cards[owner.id]
    assert card["key"] == f"listing:{owner.id}"
    assert card["candidate_ids"] == [first.id, second.id]
    assert card["item"].raw_title == "Owner ring"
    assert card["a"].landed == Decimal("35.00")
    assert card["state"] == "WATCH"  # Owner state wins over stale intake rejection.
    assert card["observation_count"] == 2
    assert [e["query"] for e in card["query_evidence"]] == ["  turqoise ring  ", "blue rock ring"]
    assert card["latest_feedback"] == {
        "id": interested.id,
        "action": "Interested",
        "created_at": NOW,
    }
    assert cards[manual.id]["observation_count"] == 0
    assert cards[manual.id]["candidate_ids"] == []
    assert cards[manual.id]["latest_feedback"] is None
    assert card["last_seen"].tzinfo == UTC


def test_exact_queries_corrections_and_email_roles_remain_distinct(session, item, prefs):
    row = candidate(session, item)
    api, execution = discovery(session, row, corrected="turquoise ring")
    observation, message = alert(
        session,
        row,
        occurrences=[
            dict(
                query_text="  blue rock ring  ",
                attribution_verified=True,
                data={"message_role": "SEARCH_SECTION"},
            ),
            dict(
                query_text="  blue rock ring  ",
                attribution_verified=True,
                data={"message_role": "RECOMMENDED"},
            ),
            dict(
                query_text="sterling ring",
                attribution_verified=False,
                data={"message_role": "SEARCH_SECTION", "format_reviewed": False},
            ),
        ],
    )
    session.commit()
    card = build_feed(session, prefs)[0]
    assert card["observation_count"] == 4
    queries = card["query_evidence"]
    assert queries[0] == dict(
        kind="discovery",
        source="eBay",
        query="  turqoise ring  ",
        original_query="  turqoise ring  ",
        corrected_query="turquoise ring",
        correction_state="CORRECTED",
        attribution="SOURCE CORRECTED",
        mode="production",
        observation_id=api.id,
        execution_id=execution.id,
    )
    assert [q["attribution"] for q in queries[1:]] == [
        "SOURCE QUERY",
        "UNVERIFIED ALERT",
        "UNVERIFIED ALERT",
    ]
    assert queries[1]["query"] == "  blue rock ring  "
    assert queries[1]["observation_id"] == queries[2]["observation_id"] == observation.id
    assert queries[1]["message_id"] == message.id
    assert "NEVER PROJECT" not in repr(card)


@pytest.mark.parametrize("has_observation", [False, True])
def test_promoting_old_candidate_does_not_refresh_source_record_time(
    session, item, prefs, has_observation
):
    captured = datetime(2026, 10, 30, 12, tzinfo=UTC)
    row = candidate(session, item)
    row.first_seen = row.last_seen = captured
    expected = captured
    if has_observation:
        observation, _ = discovery(session, row)
        expected = datetime(2026, 10, 30, 13, tzinfo=UTC)
        observation.observed_at = expected
    session.commit()
    assert build_feed(session, prefs)[0]["last_seen"] == expected

    owner = promote_candidate(session, row.id)
    owner.last_seen = NOW  # The newer promotion is not a new source observation.
    manual = owner_listing(session, item, "manual-capture")
    session.commit()
    cards = build_feed(session, prefs)
    assert [card["listing_id"] for card in cards] == [manual.id, owner.id]
    assert cards[0]["last_seen"] == NOW
    assert cards[1]["last_seen"] == expected
    assert cards[1]["observation_count"] == int(has_observation)


@pytest.mark.parametrize(
    "modes, expected",
    [
        (["production"], "real"),
        (["fixture"], "fixture"),
        (["sandbox"], "sandbox"),
        (["production", "fixture"], "mixed"),
        (["fixture", "sandbox"], "mixed"),
    ],
)
def test_history_classifies_nonlive_even_if_current_item_flags_were_cleared(
    session, item, prefs, modes, expected
):
    row = candidate(session, item)
    for mode in modes:
        discovery(session, row, mode=mode)
    session.commit()
    card = build_feed(session, prefs)[0]
    assert card["dataset"] == expected
    assert all(
        evidence["attribution"] == "NON-LIVE QUERY"
        for evidence in card["query_evidence"]
        if evidence["mode"] != "production"
    )


def test_synthetic_alert_and_owner_markers_cannot_become_real(session, item, prefs):
    owner = owner_listing(session, item)
    row = candidate(session, item.model_copy(update={"synthetic": True}), listing=owner)
    alert(session, row, synthetic=True)
    discovery(session, row, mode="production")
    manual = owner_listing(
        session,
        item.model_copy(update={"synthetic": True, "discovery_mode": "sandbox"}),
        "sandbox-manual",
    )
    session.commit()
    cards = {c["listing_id"]: c for c in build_feed(session, prefs)}
    assert cards[owner.id]["dataset"] == "mixed"
    assert cards[manual.id]["dataset"] == "sandbox"


def test_tombstones_never_reconstruct_linked_original_or_history_data(session, item, prefs):
    ebay_item = item.model_copy(update={"source": "eBay"})
    removed = owner_listing(session, ebay_item, "deleted-ebay-listing-1")
    removed.data = tombstone(removed.data)
    removed.original_data = ebay_item.model_dump(mode="json")
    stale = candidate(session, ebay_item, "stale-link", removed)
    discovery(session, stale)
    standalone = candidate(session, ebay_item, "deleted-candidate")
    standalone.normalized = tombstone({})
    standalone.original_data = ebay_item.model_dump(mode="json")
    alert(session, standalone)
    inconsistent = owner_listing(session, ebay_item, "partially-redacted-link")
    linked = candidate(session, ebay_item, "redacted-link", inconsistent)
    linked.normalized = tombstone({})
    session.commit()
    assert build_feed(session, prefs) == []


@pytest.mark.parametrize(
    "end, expected",
    [
        ("2026-11-03T11:00:00Z", "ENDED"),
        ("2026-11-03T04:00:00-09:00", "UNKNOWN"),
        ("2026-11-03T11:00:00", "UNKNOWN"),
        (None, "UNKNOWN"),
    ],
)
def test_only_timezone_aware_expiry_establishes_ended(session, item, prefs, end, expected):
    data = item.model_dump(mode="json")
    data["item_end_time"] = end
    candidate(session, type(item).model_validate(data))
    session.commit()
    assert build_feed(session, prefs)[0]["availability"] == expected


def test_explicit_ended_history_does_not_claim_current_bid_or_purchase(session, item, prefs):
    row = candidate(session, item)
    alert(
        session,
        row,
        kind="AUCTION_ENDED",
        occurrences=[
            dict(
                query_text=None,
                attribution_verified=False,
                data={"message_role": "PRIMARY", "availability": "ENDED"},
            )
        ],
    )
    session.commit()
    card = build_feed(session, prefs)[0]
    assert card["availability"] == "ENDED"
    assert card["state"] == "NEW"
    assert card["latest_feedback"] is None
    assert "Ended evidence" in card["next_evidence"][-1]


def test_unknown_costs_and_alaska_gate_preserve_financial_uncertainty(session, item, prefs):
    data = item.model_dump(mode="json")
    data["cost_status"]["item_price"] = "UNKNOWN"
    data["shipping_status"] = "UNKNOWN"
    owner = owner_listing(session, type(item).model_validate(data), state="RESEARCH")
    session.commit()
    card = build_feed(session, prefs)[0]
    assert card["item"].item_price == Decimal("15")
    assert card["a"].costs["item_price"] is None
    assert card["a"].landed is None
    assert card["missing_costs"] == ["item_price"]
    assert card["state"] == "RESEARCH"
    assert any("Alaska 99669" in evidence for evidence in card["next_evidence"])
    assert owner.review_state == "RESEARCH"


def test_unavailable_shipping_and_non_usd_keep_existing_analysis_gates(session, item, prefs):
    unavailable = owner_listing(
        session,
        item.model_copy(update={"shipping_status": Shipping.CONFIRMED_UNAVAILABLE}),
        "no-ak",
    )
    foreign = owner_listing(
        session,
        item.model_copy(update={"currency": "CAD", "buying_options": ["AUCTION"]}),
        "foreign",
    )
    session.commit()
    cards = {c["listing_id"]: c for c in build_feed(session, prefs)}
    assert cards[unavailable.id]["a"].tier == "PASS"
    assert any("PASS shipping gate" in e for e in cards[unavailable.id]["next_evidence"])
    assert cards[foreign.id]["a"].landed is None
    assert cards[foreign.id]["item"].currency == "CAD"
    assert cards[foreign.id]["item"].buying_options == ["AUCTION"]


def test_manual_query_is_owner_recorded_without_invented_observation(session, item, prefs):
    owner_listing(
        session,
        item.model_copy(
            update={"raw_search_phrase": "  blue rock  ", "effective_query": "blue stone"}
        ),
    )
    session.commit()
    card = build_feed(session, prefs)[0]
    assert card["query_evidence"][0]["query"] == "  blue rock  "
    assert card["query_evidence"][0]["corrected_query"] == "blue stone"
    assert card["query_evidence"][0]["attribution"] == "OWNER-RECORDED QUERY"
    assert card["observation_count"] == 0


def test_projection_does_not_write_or_autoflush_pending_owner_work(session, item, prefs):
    row = owner_listing(session, item)
    candidate(session, item, listing=row)
    session.commit()
    row.review_state = "WATCH"
    pending = Feedback(listing_id=row.id, action="Interested")
    session.add(pending)
    statements = []

    def record(connection, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(session.bind, "before_cursor_execute", record)
    try:
        cards = build_feed(session, prefs)
    finally:
        event.remove(session.bind, "before_cursor_execute", record)
    assert cards[0]["state"] == "WATCH"
    assert cards[0]["latest_feedback"] is None
    assert set(session.new) == {pending}
    assert set(session.dirty) == {row}
    assert statements and all(
        statement.lstrip().upper().startswith("SELECT") for statement in statements
    )
