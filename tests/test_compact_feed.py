"""Synthetic compact-feed evidence and local photos; no private records or source calls."""

import io
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import httpx
import httpx2
import pytest
from PIL import Image
from sqlalchemy import event, select

from radar.compact_feed import compact_projection, decorate_compact_cards
from radar.db import (
    AlertMessage,
    AlertObservation,
    ComparableSale,
    DiscoveredItem,
    Feedback,
    IntakeSource,
    Listing,
    ListingEdit,
    VisionCase,
    VisionPhoto,
    VisionRun,
    database,
)
from radar.ebay_deletion.processing import tombstone
from radar.jewelry.images import clean_image, write_photo
from radar.schemas import ListingInput, ResearchAttachment, ResearchObservation
from radar.sourcing_feed import build_feed

NOW = datetime(2027, 2, 3, 18, tzinfo=UTC)  # 09:00 Alaska standard time.
RECENT = NOW - timedelta(minutes=15)
LABELS = {"POTENTIAL DEAL", "FAIR PRICE", "TOO EXPENSIVE", "NEEDS INFO"}


@pytest.fixture(autouse=True)
def reject_network(monkeypatch):
    def reject(*args, **kwargs):
        raise AssertionError("Compact cards may not request images, markets or paid inference")

    async def reject_async(*args, **kwargs):
        raise AssertionError("Compact cards may not request images, markets or paid inference")

    for library in (httpx, httpx2):
        monkeypatch.setattr(library.HTTPTransport, "handle_request", reject)
        monkeypatch.setattr(library.AsyncHTTPTransport, "handle_async_request", reject_async)
    monkeypatch.setattr("radar.sourcing_feed.utcnow", lambda: NOW)


@pytest.fixture
def session(tmp_path):
    engine, sessions = database(f"sqlite:///{tmp_path / 'synthetic-compact-feed.db'}")
    with sessions() as session:
        yield session
    engine.dispose()


@pytest.fixture
def photo_root(tmp_path):
    root = tmp_path / "synthetic-private-photos"
    root.mkdir(mode=0o700)
    return root


def listing(session, item, *, source_id="920000001", seen=RECENT, **updates):
    data = item.model_dump(mode="json")
    data.update(
        source="ShopGoodwill",
        source_listing_id=source_id,
        listing_url=f"https://shopgoodwill.com/item/{source_id}",
        raw_title="SYNTHETIC compact-feed ring",
        synthetic=True,
        image_urls=["https://example.invalid/SYNTHETIC-never-fetch-image.png"],
    )
    data.update(updates)
    model = ListingInput.model_validate(data)
    row = Listing(
        dedup_key=f"synthetic-compact-{source_id}",
        data=model.model_dump(mode="json"),
        original_data={"synthetic": True},
        review_state="RESEARCH",
        first_seen=seen,
        last_seen=seen,
    )
    session.add(row)
    session.flush()
    return row


def card_for(session, prefs, row):
    return next(card for card in build_feed(session, prefs) if card["listing_id"] == row.id)


def compact_for(session, prefs, row, photo_root=None):
    cards = decorate_compact_cards(
        session, [card_for(session, prefs, row)], now=NOW, photo_root=photo_root
    )
    return cards[0]["compact"]


def source_quote(session, row, *, sent_at=RECENT, amount=None, currency=None):
    if session.get(IntakeSource, "synthetic-compact-source") is None:
        session.add(IntakeSource(id="synthetic-compact-source", data={"synthetic": True}))
        session.flush()
    candidate = session.scalar(select(DiscoveredItem).where(DiscoveredItem.listing_id == row.id))
    if candidate is None:
        candidate = DiscoveredItem(
            identity=f"synthetic-compact-candidate-{row.id}",
            source=row.data["source"],
            ebay_item_id=row.data["source_listing_id"],
            normalized=deepcopy(row.data),
            original_data={"synthetic": True},
            listing_id=row.id,
            first_seen=NOW,
            last_seen=NOW,
        )
        session.add(candidate)
        session.flush()
    message = AlertMessage(
        source_id="synthetic-compact-source",
        digest="synthetic-compact-message-" + uuid4().hex,
        raw_b64="U1lOVEhFVElD",
        safe_text="SYNTHETIC inert source quote",
        subject="SYNTHETIC source notification",
        message_type="SEARCH_MATCH",
        data={"synthetic": True},
        sent_at=sent_at,
        imported_at=NOW,
        last_imported_at=NOW,
        synthetic=True,
    )
    session.add(message)
    session.flush()
    session.add(
        AlertObservation(
            item_id=candidate.id,
            message_id=message.id,
            query_text="SYNTHETIC silver ring",
            attribution_verified=True,
            data={
                "quoted_price": str(amount if amount is not None else row.data["item_price"]),
                "currency": currency or row.data["currency"],
            },
            created_at=NOW,
        )
    )
    session.flush()
    return candidate


def supported_comparables(session, row, low="80.00", high="90.00"):
    rows = []
    for amount in (low, high):
        comparable = ComparableSale(
            data={
                "category": "jewelry",
                "materials": "SYNTHETIC silver",
                "condition": "SYNTHETIC comparable condition",
                "sale_price": amount,
                "selling_fees": None,
                "sale_date": "2027-02-01",
                "evidence": "SYNTHETIC simulated comparable sale; no real transaction",
                "notes": "SYNTHETIC test only",
                "listing_id": None,
                "synthetic": True,
            }
        )
        session.add(comparable)
        rows.append(comparable)
    session.flush()
    row.data = ListingInput.model_validate(
        row.data
        | {
            "resale_low": low,
            "resale_high": high,
            "resale_confirmed": True,
            "resale_basis": "SYNTHETIC explicitly selected comparable range",
            "resale_comparable_ids": [entry.id for entry in rows],
        }
    ).model_dump(mode="json")
    return rows


def photo(
    session,
    photo_root,
    *,
    listing_id=None,
    candidate_id=None,
    role="FRONT",
    owner="local-owner",
    color="turquoise",
):
    case = (
        session.scalar(select(VisionCase).where(VisionCase.listing_id == listing_id))
        if listing_id
        else None
    )
    if case is None:
        case = VisionCase(listing_id=listing_id, candidate_id=candidate_id, owner_id=owner)
        session.add(case)
        session.flush()
    stream = io.BytesIO()
    Image.new("RGB", (32, 32), color).save(stream, "PNG")
    clean = clean_image(stream.getvalue())
    row = VisionPhoto(
        id=uuid4().hex,
        case_id=case.id,
        role=role,
        original_hash=clean.original_hash,
        content_hash=clean.content_hash,
        data={"synthetic": True, "bytes": len(clean.data), "metadata_removed": True},
        active=True,
    )
    session.add(row)
    write_photo(photo_root, row.id, clean.data)
    session.flush()
    return row, case


def test_unknown_currency_and_absent_costs_remain_unknown(session, item, prefs):
    row = listing(session, item, currency="XXX", item_price=None, shipping_cost=None)
    result = compact_for(session, prefs, row)
    assert result["assessment"] == "NEEDS INFO"
    assert result["needs_info"] is True
    assert "unknown" in result["price_text"].lower()
    assert "0.00" not in result["price_text"]
    assert result["reason"] and len(result["reason"]) < 240
    assert result["photo_url"] is None


def test_owner_resale_scenario_without_comparable_rows_is_not_value_support(session, item, prefs):
    row = listing(
        session,
        item,
        resale_low="250",
        resale_high="300",
        resale_basis="SYNTHETIC owner scenario only",
    )
    card = card_for(session, prefs, row)
    assert card["a"].tier == "BUY CANDIDATE"
    result = compact_for(session, prefs, row)
    assert result["assessment"] == "NEEDS INFO"
    assert result["reason"]


@pytest.mark.parametrize(
    "low, high, expected",
    [
        ("80.00", "90.00", "POTENTIAL DEAL"),
        ("30.00", "40.00", "FAIR PRICE"),
        ("15.00", "25.00", "TOO EXPENSIVE"),
    ],
)
def test_four_label_assessment_uses_supported_net_value_thresholds(
    session, item, prefs, low, high, expected
):
    row = listing(session, item)
    supported_comparables(session, row, low, high)
    source_quote(session, row)
    result = compact_for(session, prefs, row)
    assert result["assessment"] == expected
    assert result["needs_info"] is False
    assert result["reason"] and len(result["reason"]) < 240


@pytest.mark.parametrize(
    "fault", ["missing_row", "wrong_range", "wrong_dataset", "unconfirmed", "invalid_record"]
)
def test_comparable_ids_and_claims_alone_are_not_support(session, item, prefs, fault):
    row = listing(session, item)
    comparables = supported_comparables(session, row)
    source_quote(session, row)
    if fault == "missing_row":
        session.delete(comparables[0])
    elif fault == "wrong_range":
        row.data = row.data | {"resale_low": "200", "resale_high": "300"}
    elif fault == "wrong_dataset":
        comparables[0].data = comparables[0].data | {"synthetic": False}
    elif fault == "unconfirmed":
        row.data = row.data | {"resale_confirmed": False}
    else:
        comparables[0].data = comparables[0].data | {"evidence": ""}
    session.flush()
    assert compact_for(session, prefs, row)["assessment"] == "NEEDS INFO"


@pytest.mark.parametrize(
    "fault",
    ["unknown_zero_tax", "estimated_shipping", "unconfirmed_alaska", "unconfirmed_currency"],
)
def test_incomplete_delivery_evidence_cannot_produce_economic_labels(session, item, prefs, fault):
    row = listing(session, item)
    supported_comparables(session, row)
    if fault == "unknown_zero_tax":
        row.data = row.data | {"cost_status": row.data["cost_status"] | {"sales_tax": "UNKNOWN"}}
    elif fault == "estimated_shipping":
        row.data = row.data | {
            "cost_status": row.data["cost_status"] | {"shipping_cost": "ESTIMATED"}
        }
    elif fault == "unconfirmed_alaska":
        row.data = row.data | {"shipping_status": "UNKNOWN"}
    else:
        row.data = row.data | {"currency": "XXX"}
    source_quote(session, row)
    result = compact_for(session, prefs, row)
    assert result["assessment"] == "NEEDS INFO"
    if fault == "unknown_zero_tax":
        assert card_for(session, prefs, row)["a"].costs["sales_tax"] is None
    if fault == "unconfirmed_currency":
        assert "USD" not in result["price_text"]
        assert result["price_text"] == "15.00 · currency unconfirmed"


def test_unconfirmed_resale_fee_profile_does_not_establish_fair_or_expensive(session, item, prefs):
    row = listing(session, item)
    supported_comparables(session, row, "30.00", "40.00")
    source_quote(session, row)
    profile = prefs.resale_profiles[prefs.resale_channel].model_copy(
        update={"verification_status": "ESTIMATED"}
    )
    uncertain = prefs.model_copy(update={"resale_profiles": {prefs.resale_channel: profile}})
    assert compact_for(session, uncertain, row)["assessment"] == "NEEDS INFO"


@pytest.mark.parametrize(
    "price, expected",
    [("15.00", "POTENTIAL DEAL"), ("60.00", "FAIR PRICE"), ("75.00", "TOO EXPENSIVE")],
)
def test_verified_net_melt_supports_only_its_configured_conservative_floor(
    session, item, prefs, price, expected
):
    row = listing(
        session,
        item,
        item_price=price,
        resale_low=None,
        resale_high=None,
        resale_basis=None,
        stated_weight="100",
        weight_kind="net_silver",
        weight_verified=True,
        weight_evidence="SYNTHETIC independently verified net silver weight",
        material_evidence="verified_sterling",
    )
    source_quote(session, row)
    current_spot = prefs.model_copy(
        update={"spot_timestamp": NOW, "spot_source": "SYNTHETIC current spot"}
    )
    assert compact_for(session, current_spot, row)["assessment"] == expected


@pytest.mark.parametrize(
    "spot_time",
    [None, datetime(2027, 2, 3, 17), NOW - timedelta(days=1), NOW + timedelta(minutes=1)],
)
def test_melt_label_requires_current_aware_nonfuture_spot_evidence(session, item, prefs, spot_time):
    row = listing(
        session,
        item,
        resale_low=None,
        resale_high=None,
        resale_basis=None,
        stated_weight="100",
        weight_kind="net_silver",
        weight_verified=True,
        weight_evidence="SYNTHETIC independently verified net silver weight",
        material_evidence="verified_sterling",
    )
    card = card_for(session, prefs, row)
    assert card["a"].melt.eligible  # Existing analysis remains unchanged; compact is stricter.
    card["spot_timestamp"] = spot_time
    result = compact_projection(card, now=NOW, observed_at=RECENT)
    assert result["assessment"] == "NEEDS INFO"


def test_new_owner_activity_cannot_supply_a_missing_source_quote_date(session, item, prefs):
    row = listing(session, item, seen=NOW)
    supported_comparables(session, row)
    result = compact_for(session, prefs, row)
    assert result["assessment"] == "NEEDS INFO"
    assert "unknown" in result["observed_label"].lower()


@pytest.mark.parametrize(
    "sent_at, expected",
    [
        (datetime(2027, 2, 3, 8, 59, 59, tzinfo=UTC), "NEEDS INFO"),
        (datetime(2027, 2, 3, 9, tzinfo=UTC), "POTENTIAL DEAL"),
        (NOW + timedelta(hours=1), "NEEDS INFO"),
    ],
)
def test_source_quote_freshness_uses_alaska_day_and_rejects_future_quotes(
    session, item, prefs, sent_at, expected
):
    row = listing(session, item)
    supported_comparables(session, row)
    source_quote(session, row, sent_at=sent_at)
    assert compact_for(session, prefs, row)["assessment"] == expected


def test_newer_changed_source_quote_cannot_reuse_older_cheaper_economics(session, item, prefs):
    row = listing(session, item)
    supported_comparables(session, row)
    source_quote(session, row, sent_at=RECENT, amount="15.00")
    source_quote(session, row, sent_at=NOW - timedelta(minutes=1), amount="150.00")
    result = compact_for(session, prefs, row)
    assert result["assessment"] == "NEEDS INFO"
    assert "150.00" in result["price_text"]
    assert Decimal(row.data["item_price"]) == Decimal("15.00")


def test_conflicting_quotes_with_identical_source_time_fail_closed(session, item, prefs):
    row = listing(session, item)
    supported_comparables(session, row)
    source_quote(session, row, sent_at=RECENT, amount="15.00")
    source_quote(session, row, sent_at=RECENT, amount="150.00")
    assert compact_for(session, prefs, row)["assessment"] == "NEEDS INFO"


def test_new_research_price_cannot_reuse_stale_canonical_costs(session, item, prefs):
    row = listing(session, item)
    supported_comparables(session, row)
    observation = ResearchObservation(
        observation_id="synthetic-new-research-quote",
        source="ShopGoodwill",
        source_listing_id=row.data["source_listing_id"],
        source_url=row.data["listing_url"],
        recorded_at=NOW,
        observed_date=NOW.date(),
        observed_at=RECENT,
        availability="ACTIVE_AT_OBSERVATION",
        item_quote="500.00",
        currency="USD",
    )
    row.data = ListingInput.model_validate(
        row.data | {"research_observations": [observation]}
    ).model_dump(mode="json")
    result = compact_for(session, prefs, row)
    assert result["assessment"] == "NEEDS INFO"
    assert "500.00" in result["price_text"]


@pytest.mark.parametrize(
    "new_terms",
    [
        {"shipping_quote": "100.00"},
        {
            "shipping_state": "RESTRICTIONS_REVIEW",
            "shipping_restrictions": ["SYNTHETIC new customer restriction requires review"],
        },
    ],
)
def test_new_cost_or_shipping_report_blocks_stale_value_even_without_item_quote(
    session, item, prefs, new_terms
):
    row = listing(session, item)
    supported_comparables(session, row)
    source_quote(session, row, sent_at=RECENT)
    observation = ResearchObservation(
        observation_id="synthetic-new-delivery-evidence",
        source="ShopGoodwill",
        source_listing_id=row.data["source_listing_id"],
        source_url=row.data["listing_url"],
        recorded_at=NOW,
        observed_date=NOW.date(),
        observed_at=NOW - timedelta(minutes=1),
        currency="USD",
        note="SYNTHETIC changed delivery terms, without a new item-price quote",
        **new_terms,
    )
    row.data = ListingInput.model_validate(
        row.data | {"research_observations": [observation]}
    ).model_dump(mode="json")
    assert compact_for(session, prefs, row)["assessment"] == "NEEDS INFO"


def test_same_day_date_only_owner_quote_keeps_exact_time_uncertainty(session, item, prefs):
    row = listing(session, item)
    supported_comparables(session, row)
    observation = ResearchObservation(
        observation_id="synthetic-date-only-quote",
        source="ShopGoodwill",
        source_listing_id=row.data["source_listing_id"],
        source_url=row.data["listing_url"],
        recorded_at=NOW,
        observed_date=NOW.date(),
        observed_at=None,
        availability="ACTIVE_AT_OBSERVATION",
        item_quote=row.data["item_price"],
        currency="USD",
    )
    row.data = ListingInput.model_validate(
        row.data | {"research_observations": [observation]}
    ).model_dump(mode="json")
    result = compact_for(session, prefs, row)
    assert result["assessment"] == "POTENTIAL DEAL"
    assert "exact time unknown" in result["observed_label"].lower()


def test_seller_gross_weight_and_marks_are_not_a_supported_melt_floor(session, item, prefs):
    row = listing(
        session,
        item,
        resale_low=None,
        resale_high=None,
        resale_basis=None,
        stated_weight="100",
        weight_kind="gross",
        weight_verified=False,
        material_evidence="seller_claim",
        hallmark_text="SYNTHETIC seller says 925",
    )
    result = compact_for(session, prefs, row)
    assert result["assessment"] == "NEEDS INFO"


def test_stale_price_is_labelled_last_observed_without_live_claim(session, item, prefs):
    row = listing(session, item, seen=NOW - timedelta(days=3))
    source_quote(session, row, sent_at=NOW - timedelta(days=3))
    result = compact_for(session, prefs, row)
    assert result["assessment"] == "NEEDS INFO"
    wording = (result["price_label"] + " " + result["observed_label"]).lower()
    assert "last observed" in wording
    assert "2027-01-31" in result["observed_label"]
    assert "current" not in result["price_label"].lower()


@pytest.mark.parametrize(
    "end, expected_ended",
    [(NOW - timedelta(seconds=1), True), (NOW, True), (NOW + timedelta(seconds=1), False)],
)
def test_countdown_uses_aware_instant_at_expiry_boundary(session, item, prefs, end, expected_ended):
    row = listing(session, item, item_end_time=end)
    result = compact_projection(card_for(session, prefs, row), now=NOW)
    assert result["ended"] is expected_ended
    assert "AKST" in result["deadline_text"]
    assert datetime.fromisoformat(result["deadline_iso"].replace("Z", "+00:00")) == end
    if expected_ended:
        assert (
            "passed" in result["countdown_label"].lower()
            or "ended" in result["countdown_label"].lower()
        )
        assert result["assessment"] == "NEEDS INFO"
    else:
        assert "ended" not in result["countdown_label"].lower()
        assert "unknown" not in result["countdown_label"].lower()


@pytest.mark.parametrize("end", [None, datetime(2027, 2, 4, 14)])
def test_countdown_never_guesses_missing_or_naive_deadline_timezone(session, item, prefs, end):
    row = listing(session, item, item_end_time=end)
    result = compact_projection(card_for(session, prefs, row), now=NOW)
    assert result["ended"] is False
    assert result["deadline_iso"] is None
    assert "unknown" in result["countdown_label"].lower()


@pytest.mark.parametrize("end", [None, datetime(2027, 2, 4, 14)])
def test_fixed_price_without_trustworthy_deadline_is_buy_it_now(session, item, prefs, end):
    row = listing(session, item, item_end_time=end, buying_options=["FIXED_PRICE"])
    card = card_for(session, prefs, row)
    result = compact_projection(card, now=NOW)
    assert result["countdown_label"] == "Buy It Now"
    assert "current availability requires confirmation" in result["deadline_text"]
    assert result["deadline_iso"] is None
    assert result["ended"] is False
    comparison = deepcopy(card)
    comparison["item"] = card["item"].model_copy(update={"buying_options": []})
    unchanged = compact_projection(comparison, now=NOW)
    assert {k: v for k, v in result.items() if k not in ("countdown_label", "deadline_text")} == {
        k: v for k, v in unchanged.items() if k not in ("countdown_label", "deadline_text")
    }


@pytest.mark.parametrize("options", [[], ["AUCTION"], ["FIXED_PRICE", "AUCTION"]])
def test_unknown_or_auction_format_keeps_unknown_close(session, item, prefs, options):
    row = listing(session, item, item_end_time=None, buying_options=options)
    result = compact_projection(card_for(session, prefs, row), now=NOW)
    assert result["countdown_label"] == "Close time unknown"


@pytest.mark.parametrize("end", [NOW - timedelta(seconds=1), NOW + timedelta(hours=1)])
def test_fixed_price_preserves_recorded_deadline(session, item, prefs, end):
    row = listing(session, item, item_end_time=end, buying_options=["FIXED_PRICE"])
    result = compact_projection(card_for(session, prefs, row), now=NOW)
    assert result["deadline_iso"] == end.isoformat()
    assert result["countdown_label"].startswith("Recorded close")


def test_fixed_price_explicitly_ended_without_deadline_is_not_buy_it_now(session, item, prefs):
    row = listing(session, item, item_end_time=None, buying_options=["FIXED_PRICE"])
    card = card_for(session, prefs, row)
    card["availability"] = "ENDED"
    result = compact_projection(card, now=NOW)
    assert result["ended"] is True
    assert result["countdown_label"] != "Buy It Now"


def test_explicit_historical_hammer_is_not_a_current_offer(session, item, prefs):
    row = listing(
        session,
        item,
        source_id="920000003",
        source="HiBid",
        listing_url="https://hibid.com/lot/920000003/synthetic-history",
        item_price=None,
        currency="XXX",
    )
    observation = ResearchObservation(
        observation_id="synthetic-ended-evidence",
        source="HiBid",
        source_listing_id="920000003",
        source_url=row.data["listing_url"],
        recorded_at=RECENT,
        observed_date=NOW.date(),
        availability="ENDED",
        realized_hammer="55.00",
        realized_currency="USD",
        currency="USD",
        buyer_premium_rate="0.20",
        priority="HISTORICAL",
    )
    row.data = ListingInput.model_validate(
        row.data | {"research_observations": [observation]}
    ).model_dump(mode="json")
    result = compact_for(session, prefs, row)
    assert result["assessment"] == "NEEDS INFO"
    assert result["ended"] is True
    assert "realized" in result["price_label"].lower()
    assert "55.00" in result["price_text"]
    assert "USD" in result["price_text"]
    assert "histor" in result["reason"].lower() or "ended" in result["reason"].lower()
    assert row.data["item_price"] is None


def test_private_front_photo_wins_over_side_detail_without_remote_fallback(
    session, item, prefs, photo_root
):
    row = listing(session, item)
    side, _ = photo(session, photo_root, listing_id=row.id, role="SIDE_DETAIL", color="blue")
    front, _ = photo(session, photo_root, listing_id=row.id, role="FRONT", color="silver")
    session.commit()
    result = compact_for(session, prefs, row, photo_root)
    assert result["photo_id"] == front.id
    assert result["photo_id"] != side.id
    assert result["photo_url"] == f"/feed/photos/{front.id}"
    assert result["photo_role"] == "FRONT"


def test_detail_photo_does_not_replace_a_missing_front_photo(session, item, prefs, photo_root):
    row = listing(session, item)
    front, _ = photo(session, photo_root, listing_id=row.id, role="FRONT")
    photo(session, photo_root, listing_id=row.id, role="BACK_HALLMARK", color="silver")
    (photo_root / (front.id + ".png")).unlink()
    session.commit()
    result = compact_for(session, prefs, row, photo_root)
    assert result["photo_id"] is None
    assert result["photo_frame"] is None


def test_unpromoted_candidate_can_use_its_owned_private_product_photo(
    session, item, prefs, photo_root
):
    model = ListingInput.model_validate(
        item.model_dump(mode="json")
        | {
            "source": "ShopGoodwill",
            "source_listing_id": "920000009",
            "listing_url": "https://shopgoodwill.com/item/920000009",
            "raw_title": "SYNTHETIC unpromoted candidate",
            "synthetic": True,
        }
    )
    candidate = DiscoveredItem(
        identity="synthetic-unpromoted-compact",
        source="ShopGoodwill",
        ebay_item_id="920000009",
        normalized=model.model_dump(mode="json"),
        original_data={"synthetic": True},
        first_seen=RECENT,
        last_seen=RECENT,
    )
    session.add(candidate)
    session.flush()
    image, _ = photo(session, photo_root, candidate_id=candidate.id)
    session.commit()
    cards = decorate_compact_cards(
        session, build_feed(session, prefs), now=NOW, photo_root=photo_root
    )
    assert cards[0]["listing_id"] is None
    assert cards[0]["compact"]["photo_id"] == image.id
    assert cards[0]["compact"]["photo_url"] == f"/feed/photos/{image.id}"


@pytest.mark.parametrize(
    "fault", ["missing", "corrupt", "wrong_owner", "inactive", "wrong_listing"]
)
def test_unavailable_or_unowned_photo_has_no_thumbnail_and_no_remote_url(
    session, item, prefs, photo_root, fault
):
    row = listing(session, item)
    image, case = photo(session, photo_root, listing_id=row.id)
    path = photo_root / (image.id + ".png")
    if fault == "missing":
        path.unlink()
    elif fault == "corrupt":
        path.write_bytes(b"SYNTHETIC-corrupt")
    elif fault == "wrong_owner":
        case.owner_id = "SYNTHETIC-other-owner"
    elif fault == "inactive":
        image.active = False
    else:
        other = listing(session, item, source_id="920000002")
        case.listing_id = other.id
    session.commit()
    result = compact_for(session, prefs, row, photo_root)
    assert result["photo_id"] is None
    assert result["photo_url"] is None


def test_shipping_document_attachment_is_not_used_as_the_product_thumbnail(
    session, item, prefs, photo_root
):
    row = listing(session, item)
    image, _ = photo(session, photo_root, listing_id=row.id, role="SIDE_DETAIL")
    observation = ResearchObservation(
        observation_id="synthetic-shipping-report",
        source="ShopGoodwill",
        source_listing_id=row.data["source_listing_id"],
        source_url=row.data["listing_url"],
        recorded_at=RECENT,
        observed_date=NOW.date(),
    )
    attachment = ResearchAttachment(
        artifact_id="a" * 32,
        source="ShopGoodwill",
        source_listing_id=row.data["source_listing_id"],
        source_url=row.data["listing_url"],
        observation_id=observation.observation_id,
        received_at=RECENT,
        original_sha256=image.original_hash,
        bytes=image.data["bytes"],
        kind="SCREENSHOT",
        display_name="SYNTHETIC-shipping-quote.png",
        vision_photo_id=image.id,
    )
    row.data = ListingInput.model_validate(
        row.data | {"research_observations": [observation], "research_attachments": [attachment]}
    ).model_dump(mode="json")
    session.commit()
    result = compact_for(session, prefs, row, photo_root)
    assert result["photo_id"] is None
    assert result["photo_url"] is None


def test_deleted_marketplace_listing_has_no_compact_card_or_photo(session, item, prefs, photo_root):
    row = listing(session, item, source="eBay", listing_url="https://www.ebay.com/itm/920000001")
    photo(session, photo_root, listing_id=row.id)
    row.data = tombstone(row.data)
    session.commit()
    assert (
        decorate_compact_cards(session, build_feed(session, prefs), now=NOW, photo_root=photo_root)
        == []
    )


def test_projection_is_read_only_and_returns_copied_cards(session, item, prefs, photo_root):
    row = listing(session, item)
    photo(session, photo_root, listing_id=row.id)
    session.commit()
    cards = build_feed(session, prefs)
    before = deepcopy(cards)
    row.review_state = "WATCH"  # A read must not flush unrelated pending owner work.

    def reject_write(connection, cursor, statement, parameters, context, executemany):
        assert statement.lstrip().split()[0].upper() not in {"INSERT", "UPDATE", "DELETE"}

    event.listen(session.bind, "before_cursor_execute", reject_write)
    try:
        decorated = decorate_compact_cards(session, cards, now=NOW, photo_root=photo_root)
    finally:
        event.remove(session.bind, "before_cursor_execute", reject_write)
    assert cards == before
    assert decorated[0] is not cards[0]
    assert "compact" not in cards[0]
    assert decorated[0]["compact"]["assessment"] in LABELS
    session.rollback()
    assert session.scalars(select(Feedback)).all() == []
    assert session.scalars(select(ListingEdit)).all() == []
    assert session.scalars(select(VisionRun)).all() == []
