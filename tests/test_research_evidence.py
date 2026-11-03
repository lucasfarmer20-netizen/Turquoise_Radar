"""Synthetic owner-research evidence; no real listings, private databases, or requests."""

import json
from copy import deepcopy
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from radar.analysis import analyze
from radar.db import (
    AlertMessage,
    AlertObservation,
    ComparableSale,
    DiscoveredItem,
    Feedback,
    IntakeSource,
    Listing,
    ListingEdit,
    SearchPattern,
    database,
)
from radar.research_evidence import append_research_observation, research_projection
from radar.schemas import ListingInput, ResearchObservation
from radar.sources import dedup_key, import_rows, upsert_listing

NOW = datetime(2027, 1, 7, 12, tzinfo=UTC)
SOURCE_TIME = datetime(2027, 1, 5, 8, tzinfo=UTC)
SHOP_ID = "900000001"
SHOP_URL = f"https://shopgoodwill.com/item/{SHOP_ID}"
HIBID_ID = "900000002"
HIBID_URL = f"https://hibid.com/lot/{HIBID_ID}/synthetic-research-lot"
COSTS = (
    "item_price",
    "buyer_premium",
    "buyer_service_fee",
    "sales_tax",
    "shipping_cost",
    "handling_fee",
)


@pytest.fixture
def session(tmp_path):
    engine, sessions = database(f"sqlite:///{tmp_path / 'synthetic-research.db'}")
    with sessions() as session:
        yield session
    engine.dispose()


def synthetic_item(**updates):
    data = dict(
        source="ShopGoodwill",
        source_listing_id=SHOP_ID,
        listing_url=SHOP_URL,
        raw_title="SYNTHETIC mixed jewelry research lot",
        synthetic=True,
        currency="XXX",
        shipping_status="UNKNOWN",
        original_search_phrase="  SYNTHETIC blue stone lot  ",
        raw_search_phrase="  SYNTHETIC blue stone lot  ",
        cost_status={key: "UNKNOWN" for key in COSTS},
    )
    data.update(updates)
    return ListingInput.model_validate(data)


def observation_data(**updates):
    data = dict(
        observation_id="synthetic-research-first",
        source="ShopGoodwill",
        source_listing_id=SHOP_ID,
        source_url=SHOP_URL,
        recorded_at=NOW,
        observed_date=date(2027, 1, 6),
        observed_at=None,
        basis="OWNER_REPORTED_SCREENSHOTS_AND_TERMS",
        artifacts_received=False,
        note="SYNTHETIC owner report; original source artifacts not received",
        currency="XXX",
        item_quote="20.00",
        quote_kind="CURRENT_BID",
        next_minimum_bid="21.00",
        shipping_quote="12.00",
        handling_quote="3.00",
        availability="ACTIVE_AT_OBSERVATION",
        shipping_state="ZIP_QUOTE_OBSERVED",
        destination_postal_code="99669",
        final_fulfillment_confirmed=False,
        priority="HIGH",
        priority_reason="SYNTHETIC owner research priority, not a purchase decision",
    )
    data.update(updates)
    return data


def observation(**updates):
    return ResearchObservation.model_validate(observation_data(**updates))


def create_listing(session, item=None):
    item = item or synthetic_item()
    row = Listing(
        dedup_key=f"synthetic-research-{item.source_listing_id}",
        data=item.model_dump(mode="json"),
        original_data={"synthetic": True, "immutable_original": "SYNTHETIC email evidence"},
        review_state="RESEARCH",
        first_seen=SOURCE_TIME,
        last_seen=SOURCE_TIME,
    )
    session.add(row)
    session.flush()
    return row


def add_source_history(session, row):
    session.add(IntakeSource(id="synthetic-research-source", data={"synthetic": True}))
    session.flush()
    candidate = DiscoveredItem(
        identity="synthetic-research-candidate",
        source="ShopGoodwill",
        ebay_item_id=SHOP_ID,
        normalized=deepcopy(row.data),
        original_data={"synthetic": True, "original_quote": "SYNTHETIC original"},
        listing_id=row.id,
        inbox_status="RESEARCH",
        first_seen=SOURCE_TIME,
        last_seen=SOURCE_TIME,
    )
    message = AlertMessage(
        source_id="synthetic-research-source",
        digest="synthetic-research-original-message",
        raw_b64="U1lOVEhFVElDIG9yaWdpbmFsIGVtYWls",
        safe_text="SYNTHETIC original email",
        subject="SYNTHETIC saved-search message",
        message_type="SEARCH_MATCH",
        data={"synthetic": True},
        synthetic=True,
        sent_at=SOURCE_TIME,
        imported_at=SOURCE_TIME,
        last_imported_at=SOURCE_TIME,
    )
    pattern = SearchPattern(
        id="synthetic-research-pattern",
        phrase="  SYNTHETIC blue stone lot  ",
        original_phrase="  SYNTHETIC blue stone lot  ",
        canonical_concept="SYNTHETIC research",
        anomaly_category="synthetic",
        appearances=2,
        useful_matches=1,
        active=False,
    )
    session.add_all([candidate, message, pattern])
    session.flush()
    session.add(
        AlertObservation(
            message_id=message.id,
            item_id=candidate.id,
            query_text="  SYNTHETIC blue stone lot  ",
            attribution_verified=False,
            data={"synthetic": True, "card_occurrences": []},
            created_at=SOURCE_TIME,
        )
    )
    session.flush()


def table_snapshot(session, models):
    session.flush()
    return {
        model.__tablename__: [
            {column.key: deepcopy(getattr(row, column.key)) for column in model.__table__.columns}
            for row in session.scalars(select(model).order_by(model.id))
        ]
        for model in models
    }


PROTECTED = (
    DiscoveredItem,
    AlertMessage,
    AlertObservation,
    SearchPattern,
    Feedback,
    ComparableSale,
)


def test_append_is_idempotent_and_retains_all_source_history(session):
    row = create_listing(session)
    add_source_history(session, row)
    session.commit()
    before = deepcopy(row.data)
    original = deepcopy(row.original_data)
    protected = table_snapshot(session, PROTECTED)
    first = observation()

    assert append_research_observation(session, row.id, first) is True
    session.commit()
    assert append_research_observation(session, row.id, first) is False
    session.commit()

    assert len(row.data["research_observations"]) == 1
    assert row.data["research_observations"][0] == first.model_dump(mode="json")
    assert row.original_data == original
    assert row.review_state == "RESEARCH"
    assert row.data["raw_search_phrase"] == before["raw_search_phrase"]
    assert row.data["item_end_time"] is None
    assert table_snapshot(session, PROTECTED) == protected
    edits = session.scalars(select(ListingEdit)).all()
    assert len(edits) == 1
    assert edits[0].before_data == before
    assert edits[0].after_data == row.data


def test_second_observation_preserves_first_and_recorded_time_is_not_source_time(session):
    row = create_listing(session)
    add_source_history(session, row)
    session.commit()
    protected = table_snapshot(session, PROTECTED)
    assert append_research_observation(session, row.id, observation())
    session.commit()
    first = deepcopy(row.data["research_observations"][0])
    second = observation(
        observation_id="synthetic-research-second",
        recorded_at=datetime(2027, 1, 8, 10, tzinfo=UTC),
        observed_date=date(2027, 1, 7),
        availability="ENDED",
        item_quote=None,
        quote_kind="UNKNOWN",
        next_minimum_bid=None,
        priority="HISTORICAL",
    )
    assert append_research_observation(session, row.id, second)
    session.commit()
    assert row.data["research_observations"][0] == first
    assert len(row.data["research_observations"]) == 2
    assert row.data["research_observations"][0]["observed_at"] is None
    assert row.data["research_observations"][0]["observed_date"] == "2027-01-06"
    assert row.review_state == "RESEARCH"
    assert row.data["item_end_time"] is None
    assert table_snapshot(session, PROTECTED) == protected
    projected = research_projection(ListingInput.model_validate(row.data))
    assert projected["availability"] == "ENDED"


def test_observation_id_conflict_does_not_overwrite_or_append_audit(session):
    row = create_listing(session)
    append_research_observation(session, row.id, observation())
    session.commit()
    before = table_snapshot(session, (Listing, ListingEdit))
    with pytest.raises(ValueError):
        append_research_observation(session, row.id, observation(item_quote="99.00"))
    session.commit()
    assert table_snapshot(session, (Listing, ListingEdit)) == before


@pytest.mark.parametrize("field", ["recorded_at", "observed_at", "reported_end_at"])
def test_naive_evidence_times_are_rejected_before_any_write(session, field):
    row = create_listing(session)
    session.commit()
    before = table_snapshot(session, (Listing, ListingEdit, Feedback, ComparableSale))
    with pytest.raises(ValidationError):
        invalid = observation(**{field: datetime(2027, 1, 6, 15)})
        append_research_observation(session, row.id, invalid)
    session.commit()
    assert table_snapshot(session, (Listing, ListingEdit, Feedback, ComparableSale)) == before


@pytest.mark.parametrize(
    "changes",
    [
        {"source_listing_id": "900000099", "source_url": "https://shopgoodwill.com/item/900000099"},
        {"source_url": "https://shopgoodwill.com/item/900000099"},
        {"source_url": "https://example.com/item/900000001"},
        {"source": "HiBid", "source_listing_id": HIBID_ID, "source_url": HIBID_URL},
    ],
)
def test_listing_input_rejects_mismatched_observation_identity(changes):
    data = synthetic_item().model_dump(mode="json")
    data["research_observations"] = [observation_data(**changes)]
    with pytest.raises(ValidationError):
        ListingInput.model_validate(data)


def test_ebay_url_cannot_be_hidden_under_a_hibid_source():
    data = synthetic_item(
        source="HiBid",
        source_listing_id=HIBID_ID,
        listing_url=f"https://www.ebay.com/itm/{HIBID_ID}",
    ).model_dump(mode="json")
    data["research_observations"] = [
        observation_data(source="HiBid", source_listing_id=HIBID_ID, source_url=HIBID_URL)
    ]
    with pytest.raises(ValidationError):
        ListingInput.model_validate(data)


@pytest.mark.parametrize(
    "source, item_id, mismatched_url",
    [
        ("ShopGoodwill", SHOP_ID, "https://shopgoodwill.com/item/900000099"),
        ("HiBid", HIBID_ID, "https://hibid.com/lot/900000099/synthetic-other-lot"),
    ],
)
def test_matching_parent_and_evidence_urls_must_still_name_the_bound_item(
    source, item_id, mismatched_url
):
    data = synthetic_item(
        source=source, source_listing_id=item_id, listing_url=mismatched_url
    ).model_dump(mode="json")
    data["research_observations"] = [
        observation_data(source=source, source_listing_id=item_id, source_url=mismatched_url)
    ]
    with pytest.raises(ValidationError):
        ListingInput.model_validate(data)


def test_append_rejects_wrong_listing_without_changing_any_row(session):
    row = create_listing(session)
    session.commit()
    before = table_snapshot(session, (Listing, ListingEdit))
    wrong = observation(
        source_listing_id="900000099", source_url="https://shopgoodwill.com/item/900000099"
    )
    with pytest.raises(ValueError):
        append_research_observation(session, row.id, wrong)
    session.commit()
    assert table_snapshot(session, (Listing, ListingEdit)) == before


def test_quoted_subtotal_and_burden_are_research_only_with_unknown_fees_and_currency(prefs):
    item = synthetic_item(research_observations=[observation().model_dump(mode="json")])
    result = research_projection(item)
    assert result["shipping_handling"] == Decimal("15.00")
    assert result["known_subtotal"] == Decimal("35.00")
    assert result["next_bid_known_subtotal"] == Decimal("36.00")
    assert result["shipping_item_ratio"] == Decimal("0.75")
    assert result["shipping_subtotal_ratio"] == Decimal("15") / Decimal("35")
    assert result["quoted_components"]["sales_tax"] is None
    assert result["quoted_components"]["buyer_premium"] is None
    assert set(result["missing_costs"]) >= {"buyer_premium", "sales_tax", "buyer_service_fee"}
    assert item.currency == "XXX"
    assert item.shipping_status == "UNKNOWN"
    a = analyze(item, prefs)
    assert a.landed is None
    assert a.maximum_buy.limiting_constraint == "UNKNOWN currency conversion"
    assert not a.melt.eligible
    assert a.tier != "BUY"


def test_historical_hammer_and_premium_do_not_become_current_or_paid_costs(session, prefs):
    item = synthetic_item(source="HiBid", source_listing_id=HIBID_ID, listing_url=HIBID_URL)
    row = create_listing(session, item)
    obs = observation(
        source="HiBid",
        source_listing_id=HIBID_ID,
        source_url=HIBID_URL,
        currency="USD",
        item_quote=None,
        quote_kind="UNKNOWN",
        next_minimum_bid=None,
        shipping_quote=None,
        handling_quote=None,
        realized_hammer="50.00",
        realized_currency="USD",
        buyer_premium_rate="0.20",
        sales_tax_rate="0.07",
        tax_basis="UNKNOWN",
        availability="ENDED",
        shipping_state="RESTRICTIONS_REVIEW",
        priority="HISTORICAL",
        shipping_restrictions=["SYNTHETIC eligibility and third-party shipping unresolved"],
    )
    assert append_research_observation(session, row.id, obs)
    session.commit()
    saved = ListingInput.model_validate(row.data)
    result = research_projection(saved)
    assert result["historical_components"]["realized_hammer"] == Decimal("50.00")
    assert result["historical_components"]["buyer_premium"] == Decimal("10.00")
    assert result["historical_subtotal"] == Decimal("60.00")
    assert result["historical_components"]["sales_tax"] is None
    assert "sales_tax" in result["historical_missing_costs"]
    assert result["quoted_components"]["item_price"] is None
    assert result["burden_penalty"] == 0
    assert saved.item_price is None
    assert saved.currency == "XXX"
    assert saved.shipping_status == "UNKNOWN"
    assert row.review_state == "RESEARCH"
    assert analyze(saved, prefs).landed is None
    assert session.scalars(select(Feedback)).all() == []
    assert session.scalars(select(ComparableSale)).all() == []


def test_no_observations_have_no_research_projection():
    assert research_projection(synthetic_item()) is None


def test_owner_report_cannot_claim_source_artifacts_were_received():
    with pytest.raises(ValidationError):
        observation(artifacts_received=True)


def test_no_item_quote_or_zero_quote_does_not_divide_by_zero_or_invent_burden():
    for value in (None, "0.00"):
        item = synthetic_item(
            research_observations=[
                observation(item_quote=value, next_minimum_bid=None).model_dump(mode="json")
            ]
        )
        result = research_projection(item)
        assert result["shipping_item_ratio"] is None


def test_existing_feedback_and_comparables_survive_research_append(session):
    row = create_listing(session)
    session.add_all(
        [
            Feedback(listing_id=row.id, action="Interested", note="SYNTHETIC prior owner action"),
            ComparableSale(listing_id=row.id, data={"synthetic": True, "untouched": True}),
        ]
    )
    session.commit()
    protected = table_snapshot(session, (Feedback, ComparableSale))
    assert append_research_observation(session, row.id, observation())
    session.commit()
    assert table_snapshot(session, (Feedback, ComparableSale)) == protected


def test_supported_canonical_quotes_remain_estimated_and_preserve_originals(session, prefs):
    row = create_listing(session)
    add_source_history(session, row)
    session.commit()
    protected = table_snapshot(session, PROTECTED)
    original = deepcopy(row.original_data)
    updates = {
        "item_price": "20.00",
        "shipping_cost": "12.00",
        "handling_fee": "3.00",
        "cost_status": {
            "item_price": "ESTIMATED",
            "shipping_cost": "ESTIMATED",
            "handling_fee": "ESTIMATED",
        },
        "shipping_status": "UNKNOWN",
        "shipping_evidence": "SYNTHETIC quote only; final fulfillment unconfirmed",
    }
    assert append_research_observation(session, row.id, observation(), canonical_updates=updates)
    session.commit()
    saved = ListingInput.model_validate(row.data)
    assert saved.item_price == Decimal("20.00")
    assert saved.shipping_cost == Decimal("12.00")
    assert saved.handling_fee == Decimal("3.00")
    assert saved.currency == "XXX"
    assert saved.shipping_status == "UNKNOWN"
    assert all(saved.cost_status[key] == "ESTIMATED" for key in updates["cost_status"])
    assert all(
        getattr(saved, key) is None for key in ("buyer_premium", "sales_tax", "buyer_service_fee")
    )
    assert analyze(saved, prefs).landed is None
    assert row.original_data == original
    assert table_snapshot(session, PROTECTED) == protected
    assert len(session.scalars(select(ListingEdit)).all()) == 1


@pytest.mark.parametrize(
    "updates",
    [
        {"source": "eBay"},
        {"raw_search_phrase": "SYNTHETIC rewritten query"},
        {"material_evidence": "verified_sterling"},
        {"weight_verified": True},
        {"item_price": "99.00", "cost_status": {"item_price": "ESTIMATED"}},
        {"item_price": "20.00", "cost_status": {"item_price": "CONFIRMED"}},
        {"sales_tax": "0.00", "cost_status": {"sales_tax": "ESTIMATED"}},
        {
            "shipping_status": "CONFIRMED_TO_ALASKA",
            "shipping_evidence": "SYNTHETIC ZIP quote is not final fulfillment",
        },
        {"shipping_status": "CONFIRMED_UNAVAILABLE"},
    ],
)
def test_unsupported_canonical_updates_are_atomic(session, updates):
    row = create_listing(session)
    session.commit()
    before = table_snapshot(session, (Listing, ListingEdit, Feedback, ComparableSale))
    with pytest.raises(ValueError):
        append_research_observation(session, row.id, observation(), canonical_updates=updates)
    session.commit()
    assert table_snapshot(session, (Listing, ListingEdit, Feedback, ComparableSale)) == before


def test_status_only_edit_cannot_activate_an_unsupported_stored_quote(session):
    row = create_listing(session, synthetic_item(shipping_cost="45.00"))
    session.commit()
    before = table_snapshot(session, (Listing, ListingEdit))
    with pytest.raises(ValueError):
        append_research_observation(
            session,
            row.id,
            observation(shipping_quote=None),
            canonical_updates={"cost_status": {"shipping_cost": "ESTIMATED"}},
        )
    session.commit()
    assert table_snapshot(session, (Listing, ListingEdit)) == before


def test_unit_only_edit_cannot_relabel_existing_verified_net_weight(session):
    row = create_listing(
        session,
        synthetic_item(
            stated_weight="10",
            weight_kind="net_silver",
            weight_unit="g",
            weight_verified=True,
            weight_evidence="SYNTHETIC prior independent weight evidence",
        ),
    )
    session.commit()
    before = table_snapshot(session, (Listing, ListingEdit))
    with pytest.raises(ValueError):
        append_research_observation(
            session, row.id, observation(), canonical_updates={"weight_unit": "dwt"}
        )
    session.commit()
    assert table_snapshot(session, (Listing, ListingEdit)) == before


def test_currency_change_requires_prior_clearing_of_profile_and_invoice_assumptions(session):
    row = create_listing(
        session,
        synthetic_item(
            currency="CAD",
            buyer_profile="synthetic-prior-profile",
            invoice_fee_allocation="4.00",
            invoice_allocation_confirmed=True,
            invoice_allocation_evidence="SYNTHETIC prior invoice allocation",
        ),
    )
    session.commit()
    before = table_snapshot(session, (Listing, ListingEdit))
    updates = {key: None for key in COSTS}
    updates.update(
        currency="USD",
        item_price="20.00",
        shipping_cost="12.00",
        handling_fee="3.00",
        cost_status={
            key: "ESTIMATED"
            if key in {"item_price", "shipping_cost", "handling_fee"}
            else "UNKNOWN"
            for key in COSTS
        },
    )
    with pytest.raises(ValueError):
        append_research_observation(
            session, row.id, observation(currency="USD"), canonical_updates=updates
        )
    session.commit()
    assert table_snapshot(session, (Listing, ListingEdit)) == before


def test_shipping_burden_penalty_is_research_ranking_and_requires_a_value_guard(prefs, item):
    quote = observation(shipping_quote="27.00", handling_quote="3.00")
    unsupported = synthetic_item(
        collector_evidence="SYNTHETIC unsupported collector excitement",
        material_evidence="seller_claim",
        stated_weight="100",
        weight_kind="gross",
        research_observations=[quote.model_dump(mode="json")],
    )
    unsupported_analysis = analyze(unsupported, prefs)
    before = unsupported_analysis.model_dump(mode="json")
    result = research_projection(unsupported, unsupported_analysis)
    assert result["shipping_subtotal_ratio"] == Decimal("0.60")
    assert result["burden_penalty"] > 0
    assert result["burden_exempt"] is False
    assert unsupported_analysis.model_dump(mode="json") == before
    assert not unsupported_analysis.melt.eligible

    supported_data = item.model_dump(mode="json")
    supported_data.update(
        source="ShopGoodwill",
        source_listing_id=SHOP_ID,
        listing_url=SHOP_URL,
        raw_title="SYNTHETIC supported research economics",
        synthetic=True,
        item_price="20.00",
        shipping_cost="27.00",
        handling_fee="3.00",
        resale_low="200.00",
        resale_high="220.00",
        resale_basis="SYNTHETIC owner-confirmed resale scenario",
        research_observations=[
            observation(currency="USD", shipping_quote="27.00", handling_quote="3.00").model_dump(
                mode="json"
            )
        ],
    )
    supported = ListingInput.model_validate(supported_data)
    supported_analysis = analyze(supported, prefs)
    assert supported_analysis.tier == "BUY CANDIDATE"
    guarded = research_projection(supported, supported_analysis)
    assert guarded["shipping_subtotal_ratio"] == Decimal("0.60")
    assert guarded["burden_exempt"] is True
    assert guarded["burden_penalty"] == 0

    for changes in ({"currency": "XXX"}, {"shipping_quote": "60.00"}):
        changed = supported.model_dump(mode="json")
        changed["research_observations"][0].update(changes)
        mismatch = research_projection(ListingInput.model_validate(changed), supported_analysis)
        assert mismatch["burden_exempt"] is False
        assert mismatch["burden_penalty"] > 0


def test_non_usd_canonical_item_cannot_use_a_numerical_melt_burden_exemption(item, prefs):
    data = item.model_dump(mode="json")
    data.update(
        source="ShopGoodwill",
        source_listing_id=SHOP_ID,
        listing_url=SHOP_URL,
        raw_title="SYNTHETIC independently verified net-silver scenario",
        synthetic=True,
        stated_weight="100",
        weight_kind="net_silver",
        weight_verified=True,
        weight_evidence="SYNTHETIC independent net-weight evidence",
        material_evidence="verified_sterling",
        research_observations=[
            observation(currency="USD", shipping_quote="27.00").model_dump(mode="json")
        ],
    )
    usd_item = ListingInput.model_validate(data)
    numeric_analysis = analyze(usd_item, prefs)
    assert numeric_analysis.melt.eligible
    non_usd = usd_item.model_copy(update={"currency": "XXX"})
    projected = research_projection(non_usd, numeric_analysis)
    assert projected["burden_exempt"] is False
    assert projected["burden_penalty"] > 0


@pytest.mark.parametrize("replacement", ["clear", "alter", "append"])
def test_reimport_cannot_replace_existing_audited_research_history(session, replacement):
    item = synthetic_item()
    row = create_listing(session, item)
    row.dedup_key = dedup_key(item)
    append_research_observation(session, row.id, observation())
    session.commit()
    before = table_snapshot(session, (Listing, ListingEdit, Feedback, ComparableSale))
    incoming = deepcopy(row.data)
    if replacement == "clear":
        incoming["research_observations"] = []
    elif replacement == "alter":
        incoming["research_observations"][0]["note"] = "SYNTHETIC unaudited replacement"
    else:
        incoming["research_observations"].append(
            observation(observation_id="synthetic-unaudited-extra").model_dump(mode="json")
        )
    with pytest.raises(ValueError):
        import_rows(session, json.dumps([incoming]), "json")
    session.commit()
    assert table_snapshot(session, (Listing, ListingEdit, Feedback, ComparableSale)) == before


@pytest.mark.parametrize("include_identical_history", [False, True])
def test_normal_reimport_preserves_research_and_immutable_originals(
    session, include_identical_history
):
    item = synthetic_item()
    row = create_listing(session, item)
    row.dedup_key = dedup_key(item)
    append_research_observation(session, row.id, observation())
    session.commit()
    original = deepcopy(row.original_data)
    history = deepcopy(row.data["research_observations"])
    edits = table_snapshot(session, (ListingEdit, Feedback, ComparableSale))
    incoming = deepcopy(row.data)
    incoming["condition"] = "SYNTHETIC later observed condition"
    if not include_identical_history:
        incoming.pop("research_observations")
    result = import_rows(session, json.dumps([incoming]), "json")
    session.commit()
    assert result == {"created": 0, "updated": 1, "rows": 1}
    assert row.data["condition"] == incoming["condition"]
    assert row.data["research_observations"] == history
    assert row.original_data == original
    assert table_snapshot(session, (ListingEdit, Feedback, ComparableSale)) == edits


def test_new_import_can_retain_schema_valid_research_evidence(session):
    item = synthetic_item(research_observations=[observation().model_dump(mode="json")])
    row, created = upsert_listing(session, item, item.model_dump(mode="json"))
    session.commit()
    assert created is True
    assert ListingInput.model_validate(row.data).research_observations == item.research_observations
    assert session.scalars(select(Feedback)).all() == []


def test_unrelated_ebay_deletion_preserves_marketplace_research_and_both_audit_snapshots(session):
    from radar.ebay_deletion.processing import SAFE_FIELDS, sanitize_database, tombstone
    from radar.ebay_deletion.protocol import digest

    for source, item_id, url in (
        ("ShopGoodwill", SHOP_ID, SHOP_URL),
        ("HiBid", HIBID_ID, HIBID_URL),
    ):
        first = observation(source=source, source_listing_id=item_id, source_url=url)
        row = create_listing(
            session,
            synthetic_item(
                source=source,
                source_listing_id=item_id,
                listing_url=url,
                research_observations=[first.model_dump(mode="json")],
            ),
        )
        append_research_observation(
            session,
            row.id,
            first.model_copy(update={"observation_id": "synthetic-second-audited-observation"}),
        )
    target = create_listing(
        session,
        synthetic_item(
            source="eBay",
            source_listing_id="900000003",
            listing_url="https://www.ebay.com/itm/900000003",
        ),
    )
    target.original_data = {"seller": {"userId": "SYNTHETIC-DELETED-ACCOUNT"}}
    session.commit()
    protected = {
        row.id: deepcopy((row.data, row.original_data))
        for row in session.scalars(select(Listing).where(Listing.id != target.id))
    }
    audits = table_snapshot(session, (ListingEdit,))
    secret = b"SYNTHETIC-RESEARCH-DELETION-TEST-KEY"
    identities = {"userId": digest(secret, "userId", "SYNTHETIC-DELETED-ACCOUNT")}
    assert sanitize_database(session.bind.url.database, identities, secret) == ("SANITIZED", 1)
    session.expire_all()
    assert protected == {
        row.id: (row.data, row.original_data)
        for row in session.scalars(select(Listing).where(Listing.id != target.id))
    }
    assert table_snapshot(session, (ListingEdit,)) == audits
    assert "research_observations" not in SAFE_FIELDS
    removed = tombstone({"source": "eBay", "research_observations": [{"private": "SYNTHETIC"}]})
    assert ListingInput.model_validate(removed).research_observations == []
