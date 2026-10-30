import json
from decimal import Decimal as D

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from radar.analysis import analyze
from radar.capture import suggest
from radar.db import ComparableSale, Feedback, Listing, ListingEdit
from radar.finance import acquisition_costs, landed_cost, max_buy_price, scenario
from radar.schemas import BuyerProfile, FeeProfile
from radar.workflow import pattern_effectiveness


def fees(**updates):
    return FeeProfile(packaging="1", outbound_shipping="0", **updates)


def test_whatnot_distinct_fee_bases_and_fixed_once():
    s = scenario(
        D("50"),
        fees(),
        D("20"),
        D("8"),
        D("4"),
        shipping_status="CONFIRMED",
        tax_status="CONFIRMED",
    )
    assert s.commission_base == 50 and s.commission == D("4.00")
    assert s.processing_base == 62 and s.processing == D("2.10")
    assert s.net_resale == D("42.90")
    assert s.profit == D("22.90") and s.roi == D("1.145")
    assert "once per transaction" in " ".join(s.explanations)
    assert "item-only" in " ".join(s.explanations)


@pytest.mark.parametrize(
    "buyer_shipping,buyer_tax,missing",
    [(None, D(0), "buyer_shipping"), (D(0), None, "buyer_tax"), (None, None, "buyer_shipping")],
)
def test_unknown_buyer_payments_block_resale_and_max(buyer_shipping, buyer_tax, missing):
    s = scenario(D(50), fees(), D(20), buyer_shipping, buyer_tax)
    assert s.net_resale is None and s.profit is None and missing in s.missing
    maximum = max_buy_price(
        D(50),
        fees(),
        D(5),
        D(0),
        D(15),
        D(".6"),
        BuyerProfile(),
        buyer_shipping=buyer_shipping,
        buyer_tax=buyer_tax,
    )
    assert maximum.maximum_item_price is None


def test_named_assumptions_are_labeled_not_silent():
    f = fees(
        scenario_name="US small parcel, hypothetical",
        buyer_shipping_assumption="7",
        buyer_tax_assumption="3",
    )
    s = scenario(D(50), f, D(20))
    assert s.processing_base == 60
    assert s.input_status == {"buyer_shipping": "ESTIMATED", "buyer_tax": "ESTIMATED"}
    assert "US small parcel, hypothetical" in " ".join(s.explanations)
    with pytest.raises(ValidationError):
        fees(buyer_tax_assumption="0")


@pytest.mark.parametrize(
    "category,tier,rate", [("jewelry", "volume", "0.06"), ("coins", "standard", "0.04")]
)
def test_explicit_category_tiers(category, tier, rate):
    f = fees(commission_tiers={f"{category}:{tier}": rate})
    s = scenario(D(100), f, D(20), D(5), D(5), category, tier)
    assert s.commission == D(100) * D(rate)
    assert s.processing == D("3.49")


def test_unknown_commission_tier_not_guessed():
    s = scenario(D(50), fees(), D(20), D(0), D(0), tier="unconfigured")
    assert s.net_resale is None and s.commission is None


def test_packaging_and_outbound_not_assumed_zero():
    s = scenario(D(50), FeeProfile(), D(20), D(0), D(0))
    assert set(s.missing) == {"packaging", "outbound_shipping"}
    assert s.net_resale is None


@pytest.mark.parametrize("field", ["shipping_cost", "handling_fee"])
def test_estimated_vs_confirmed_cost(item, prefs, field):
    statuses = dict(item.cost_status, **{field: "ESTIMATED"})
    estimated = analyze(item.model_copy(update={"cost_status": statuses}), prefs)
    actual = analyze(item, prefs)
    assert estimated.landed == actual.landed
    assert estimated.tier != "BUY CANDIDATE" and actual.tier == "BUY CANDIDATE"
    statuses[field] = "UNKNOWN"
    unknown = analyze(item.model_copy(update={"cost_status": statuses}), prefs)
    assert unknown.landed is None and unknown.maximum_buy.maximum_item_price is None


@pytest.mark.parametrize("field", ["resale_buyer_shipping", "resale_buyer_tax"])
def test_listing_unknown_buyer_transaction_no_buy(item, prefs, field):
    a = analyze(item.model_copy(update={field: None}), prefs)
    assert a.low.profit is None and a.maximum_buy.maximum_item_price is None
    assert a.tier != "BUY CANDIDATE"


@pytest.mark.parametrize(
    "field,value",
    [("resale_confirmed", False), ("condition", None), ("material_evidence", "unknown")],
)
def test_missing_evidence_blocks_buy(item, prefs, field, value):
    assert analyze(item.model_copy(update={field: value}), prefs).tier != "BUY CANDIDATE"


def test_acquisition_bases_fixed_lot_invoice_and_profile_metadata(item, prefs):
    profile = BuyerProfile(
        premium_rate=".20",
        premium_base="item_shipping",
        service_rate=".10",
        service_base="item_shipping",
        fixed_marketplace_fee="2",
        per_lot_fee="3",
        per_invoice_fee="6",
        tax_rate=".05",
        tax_base="invoice",
        source="Synthetic invoice terms",
        as_of="2026-10-29",
        verification_status="CONFIRMED",
    )
    prefs.buyer_profiles["auction"] = profile
    item = item.model_copy(
        update=dict(
            item_price=D(20),
            shipping_cost=D(10),
            handling_fee=D(1),
            buyer_premium=None,
            buyer_service_fee=None,
            sales_tax=None,
            buyer_profile="auction",
            invoice_fee_allocation=D(3),
            invoice_allocation_confirmed=True,
            invoice_allocation_evidence="Confirmed allocation of synthetic two-lot invoice",
        )
    )
    costs, total, unknown = landed_cost(item, prefs)
    assert costs["buyer_premium"] == D(6)
    assert costs["buyer_service_fee"] == D(11)
    assert costs["sales_tax"] == D("2.40")
    assert total == D("50.40") and not unknown
    missing = item.model_copy(update={"invoice_allocation_confirmed": False})
    assert landed_cost(missing, prefs)[1] is None
    with pytest.raises(ValidationError):
        BuyerProfile(per_invoice_fee="6")


def test_fee_base_missing_shipping_cannot_calculate(item, prefs):
    prefs.buyer_profiles["auction"] = BuyerProfile(service_rate=".10", service_base="item_shipping")
    item = item.model_copy(
        update={"buyer_profile": "auction", "shipping_cost": None, "buyer_service_fee": None}
    )
    assert acquisition_costs(item, item.item_price, prefs)["buyer_service_fee"] is None


def test_max_bid_recalculates_transaction_and_buyer_fee_bases():
    f = fees()
    buyer = BuyerProfile(
        premium_rate=".2",
        service_rate=".1",
        service_base="item_shipping",
        per_lot_fee="2",
        tax_rate=".05",
        tax_base="invoice",
    )
    result = max_buy_price(
        D(60), f, D(8), D(1), D(15), D(".6"), buyer, buyer_shipping=D(9), buyer_tax=D(4)
    )
    assert result.profit >= 15 and result.roi >= D(".6")
    price = result.maximum_item_price + D(".01")
    from radar.finance import acquisition_components

    cost = sum(acquisition_components(price, D(8), D(1), buyer).values())
    net = scenario(D(60), f, None, D(9), D(4)).net_resale
    assert net - cost < 15 or (net - cost) / cost < D(".6")
    zero_transaction = max_buy_price(
        D(60), f, D(8), D(1), D(15), D(".6"), buyer, buyer_shipping=D(0), buyer_tax=D(0)
    )
    assert zero_transaction.maximum_item_price > result.maximum_item_price


def test_deterministic_capture_preserves_unverified_text():
    text = (
        "Estate sterling ring size 7.5\nCurrent bid: $12.50\nShipping: $8\n"
        "Weight 6.4 dwt\n3 pieces\nSeller ID: seller-A\nAuction: estate-1\n"
        "Query: nature america"
    )
    item = suggest(text, "https://www.ebay.com/itm/123")
    assert item.source == "eBay" and item.item_price == D("12.50")
    assert item.shipping_cost == 8 and item.stated_weight == D("6.4") and item.weight_unit == "dwt"
    assert item.piece_count == 3 and item.ring_size == "7.5"
    assert item.seller_id == "seller-A" and item.sale_auction_id == "estate-1"
    assert item.raw_search_phrase == "nature america" and item.pasted_text == text
    assert item.material_evidence == "seller_claim" and not item.weight_verified
    assert item.shipping_status == "UNKNOWN" and item.weight_kind == "gross"


def test_capture_does_not_misread_shipping_as_price():
    item = suggest("Shipping $8\nOld ring\nWeight unknown")
    assert item.item_price is None and item.shipping_cost == 8
    mixed = suggest("Mixed lot of 3 sterling rings with a plated clasp\n$15\nFree shipping")
    assert mixed.material_evidence == "seller_claim"
    assert mixed.shipping_cost == 0 and mixed.shipping_status == "UNKNOWN"


def test_capture_preview_and_explicit_owner_confirmation(client):
    text = "Blue ring sterling size 7\nPrice: $15\nShipping: $5\nQuery: nature america"
    r = client.post("/capture/preview", data={"pasted_text": text})
    assert r.status_code == 200 and "Review every suggestion" in r.text
    assert "nature america" in r.text
    assert (
        client.post("/capture/save", data={"raw_title": "x", "source": "manual"}).status_code == 422
    )
    saved = client.post(
        "/capture/save",
        data={
            "owner_confirmed": "yes",
            "source": "manual",
            "raw_title": "Owner edited ring",
            "item_price": "14",
            "item_price_status": "ESTIMATED",
            "pasted_text": text,
            "raw_search_phrase": "nature america",
        },
    )
    assert saved.status_code == 200 and "Owner edited ring" in saved.text
    with client.app.state.sessions() as s:
        row = s.get(Listing, 1)
        assert row.data["pasted_text"] == text and row.original_data["pasted_text"] == text
        assert row.data["item_price"] == "14" and row.review_state == "RESEARCH"
        assert row.data["cost_status"]["item_price"] == "ESTIMATED"
    assert (
        "BUY CANDIDATE"
        not in client.get("/queue?state=BUY%20CANDIDATE").text.split("Opportunity radar")[-1]
    )


def test_partial_evidence_edit_audit_queue_and_feedback(client, item):
    client.post("/listings", data={"payload": item.model_dump_json()})
    assert "NEW" in client.get("/queue").text
    original = item.model_dump(mode="json")
    r = client.post(
        "/listings/1/edit", data={"shipping_cost": "8", "shipping_cost_status": "ESTIMATED"}
    )
    assert r.status_code == 200 and "ESTIMATED" in r.text
    with client.app.state.sessions() as s:
        row = s.get(Listing, 1)
        assert row.data["resale_basis"] == original["resale_basis"]
        assert row.original_data["shipping_cost"] == "5"
        assert len(list(s.scalars(select(ListingEdit)))) == 1
    client.post("/listings/1/feedback", data={"action": "Interested"})
    assert "WATCH" in client.get("/queue?state=WATCH").text
    client.post("/listings/1/feedback", data={"action": "Purchased"})
    with client.app.state.sessions() as s:
        assert s.get(Listing, 1).review_state == "PURCHASED"
        assert len(list(s.scalars(select(Feedback)))) == 2
    assert client.post("/listings/1/state", data={"state": "BUY CANDIDATE"}).status_code == 422
    assert client.get("/listings/1/edit").status_code == 200


def test_source_statistics_separate_inferred_actual_and_unique_feedback(client, item):
    for id_, source, phrase, title in [
        ("a", "source A", "nature america", "ring"),
        ("b", "source B", "nature america", "ring"),
        ("c", "source A", None, "nature america ring"),
    ]:
        raw = item.model_dump(mode="json") | dict(
            source_listing_id=id_, source=source, raw_search_phrase=phrase, raw_title=title
        )
        client.post("/listings", data={"payload": json.dumps(raw)})
    for lid in (1, 3):
        for action in ("Interested", "Purchased", "Purchased", "False positive"):
            client.post(f"/listings/{lid}/feedback", data={"action": action})
        client.post(f"/listings/{lid}/feedback", data={"action": "Sold", "realized_profit": "20"})
        client.post(f"/listings/{lid}/feedback", data={"action": "Sold", "realized_profit": "25"})
    with client.app.state.sessions() as s:
        stats = pattern_effectiveness(s)
        a = next(g for g in stats if g["source"] == "source A" and g["kind"] == "SOURCE QUERY")
        inferred = next(g for g in stats if g["kind"] == "INFERRED")
        b = next(g for g in stats if g["source"] == "source B")
        assert a["matches"] == 1 and a["purchased"] == 1 and a["conversion"] == 1
        assert a["realized_profit"] == 25 and a["false_positives"] == 1
        assert b["purchased"] == 0 and inferred["priority"] == 100
        assert a["pattern"].successful_purchases == 1  # inferred purchase not query success
    assert client.get("/effectiveness").status_code == 200


def comp(**updates):
    return dict(
        category="ring",
        materials="sterling seller claim",
        condition="used",
        sale_price="45",
        sale_date="2026-10-01",
        evidence="SYNTHETIC recorded sale fixture",
        **updates,
    )


def test_comparables_separate_confirmed_range_and_linked_outcomes(client, item):
    client.post("/listings", data={"payload": item.model_dump_json()})
    r = client.post(
        "/comparables", data={"content": json.dumps([comp(), comp(notes="second sample")])}
    )
    assert r.status_code == 200 and "n=2" in r.text
    with client.app.state.sessions() as s:
        assert len(list(s.scalars(select(ComparableSale)))) == 2
        assert s.get(Listing, 1).data["resale_comparable_ids"] == []
    assert (
        client.post("/listings/1/use-comparables", data={"comparable_id": "1"}).status_code == 422
    )
    r = client.post("/listings/1/use-comparables", data={"comparable_id": "1", "confirm": "yes"})
    assert r.status_code == 200 and "n=1" in r.text
    with client.app.state.sessions() as s:
        assert s.get(Listing, 1).data["resale_confirmed"] is True
        assert s.get(Listing, 1).data["resale_comparable_ids"] == [1]
    linked = comp(listing_id=1)
    assert client.post("/comparables", data={"content": json.dumps([linked])}).status_code == 422
    client.post("/listings/1/feedback", data={"action": "Purchased"})
    assert client.post("/comparables", data={"content": json.dumps([linked])}).status_code == 200
    assert "Linked actual resale outcomes" in client.get("/listings/1").text


def test_comparable_csv_and_atomic_invalid_import(client):
    csv = (
        "category,materials,condition,sale_price,sale_date,evidence\n"
        "ring,sterling,used,40,2026-10-01,SYNTHETIC CSV\n"
    )
    assert client.post("/comparables", data={"format": "csv", "content": csv}).status_code == 200
    bad = comp()
    bad["sale_price"] = "-1"
    assert (
        client.post("/comparables", data={"content": json.dumps([comp(), bad])}).status_code == 422
    )
    with client.app.state.sessions() as s:
        assert len(list(s.scalars(select(ComparableSale)))) == 1


def test_fee_calibration_form_and_legacy_metadata(client):
    assert client.get("/calibration").status_code == 200
    r = client.post(
        "/calibration",
        data=dict(
            commission=".08",
            processing=".029",
            processing_fixed=".30",
            packaging="1.50",
            outbound_shipping="0",
            source="TEST owner-confirmed schedule",
            as_of="2026-10-29",
            verification_status="CONFIRMED",
        ),
    )
    assert r.status_code == 200 and "1.50" in r.text
    assert client.post("/calibration", data={"packaging": "-1"}).status_code == 422
    from radar.schemas import Preferences

    p = Preferences.model_validate({"resale_profiles": {"whatnot_planning": {"commission": ".10"}}})
    assert p.resale_profiles["whatnot_planning"].as_of is None
    assert "Legacy" in p.resale_profiles["whatnot_planning"].source


def test_inferred_legacy_counters_cannot_boost_query_plan(client, item):
    from radar.anomalies import learned_priority
    from radar.db import SearchPattern
    from radar.workflow import attributed_patterns

    client.post(
        "/listings",
        data={
            "payload": item.model_copy(
                update={"raw_title": "nature america ring", "raw_search_phrase": None}
            ).model_dump_json()
        },
    )
    with client.app.state.sessions.begin() as s:
        p = s.scalar(select(SearchPattern).where(SearchPattern.phrase == "nature america"))
        p.successful_purchases = 100
        p.realized_profit = "1000"
    with client.app.state.sessions() as s:
        derived = next(p for p in attributed_patterns(s) if p.phrase == "nature america")
        assert derived.successful_purchases == 0 and learned_priority(derived) == 100
        # Original legacy counters are preserved for audit, not used for success ranking.
        assert s.get(SearchPattern, derived.id).successful_purchases == 100


def test_identity_edit_rekeys_and_reimport_preserves_owner_evidence(client, item):
    client.post("/listings", data={"payload": item.model_dump_json()})
    assert (
        client.post(
            "/listings/1/edit",
            data={"source_listing_id": "new-id", "condition": "Owner noted wear"},
        ).status_code
        == 200
    )
    from radar.sources import import_rows

    with client.app.state.sessions.begin() as s:
        import_rows(
            s,
            json.dumps(
                [dict(source="test", source_listing_id="new-id", raw_title="new observation")]
            ),
            "json",
        )
        rows = list(s.scalars(select(Listing)))
        assert len(rows) == 1 and rows[0].data["condition"] == "Owner noted wear"
        assert rows[0].data["resale_basis"] == "Manual test scenario"
