import base64
import json
from decimal import Decimal
from email.message import EmailMessage
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select

from radar.db import (
    AlertImport,
    AlertInvoice,
    AlertMessage,
    AlertObservation,
    DiscoveredItem,
    Feedback,
    IntakeSource,
    Listing,
    ListingEdit,
    SavedSourceSearch,
    SearchPattern,
)
from radar.intake.parsers import hibid_embedded_target, parse_message, safe_link
from radar.intake.registry import SourceDefinition
from radar.intake.service import import_alerts
from radar.intake.views import estate_groups, inbox_cards, search_status
from radar.main import preferences

ROOT = Path(__file__).resolve().parent.parent / "fixtures/alerts"


@pytest.fixture(autouse=True)
def no_website_or_mailbox_access(monkeypatch):
    def reject(*args, **kwargs):
        raise AssertionError("Intake must not fetch websites or mailboxes")

    async def reject_async(*args, **kwargs):
        raise AssertionError("Intake must not fetch websites or mailboxes")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", reject)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", reject_async)


def raw(name):
    return (ROOT / name).read_bytes()


def import_files(client, source, files):
    with client.app.state.sessions.begin() as s:
        batch = import_alerts(s, source, [raw(f) for f in files])
        return batch.id


@pytest.mark.parametrize(
    "file,kind",
    [
        ("synthetic-shop-search.eml", "SEARCH_MATCH"),
        ("synthetic-hibid-promo.eml", "PROMOTIONAL_NEWSLETTER"),
        ("synthetic-hibid-closing.eml", "AUCTION_CLOSING_REMINDER"),
        ("synthetic-shop-bid.eml", "BID_CONFIRMED"),
        ("synthetic-shop-outbid.eml", "OUTBID"),
        ("synthetic-shop-won.eml", "AUCTION_WON"),
        ("synthetic-shop-paid.eml", "PAYMENT_RECEIVED"),
        ("synthetic-shop-shipped.eml", "ORDER_SHIPPED"),
    ],
)
def test_message_classification(client, file, kind):
    source = "hibid" if "hibid" in file else "shopgoodwill"
    with client.app.state.sessions() as s:
        definition = SourceDefinition.model_validate(s.get(IntakeSource, source).data)
    parsed = parse_message(raw(file), source, definition)
    assert parsed.message_type == kind
    assert parsed.synthetic


@pytest.mark.parametrize(
    "file,kind,method,price",
    [
        ("synthetic-shop-style-receipt.eml", "PAYMENT_RECEIVED", "ITEM_LABEL", "18.00"),
        ("synthetic-shop-style-winning.eml", "AUCTION_WON", "ITEM_HASH", None),
        ("synthetic-shop-style-bid.eml", "BID_CONFIRMED", "NOTICE_BID_ITEM_CONTEXT", "10.00"),
    ],
)
def test_shopgoodwill_observed_style_id_markers(client, file, kind, method, price):
    with client.app.state.sessions() as s:
        definition = SourceDefinition.model_validate(s.get(IntakeSource, "shopgoodwill").data)
    parsed = parse_message(raw(file), "shopgoodwill", definition)
    assert parsed.message_type == kind
    assert len(parsed.candidates) == 1
    candidate = parsed.candidates[0]
    assert candidate.item.source_listing_id == "910000000001"
    assert candidate.evidence["identity_method"] == method
    assert candidate.item.item_price == (Decimal(price) if price else None)
    assert candidate.item.shipping_status == "UNKNOWN"
    assert candidate.query is None and not candidate.query_supported
    assert candidate.item.listing_url is None  # No invented/followed tracking destination.


def test_shopgoodwill_style_lifecycle_receipt_reconciliation_and_repeat(client):
    import_files(
        client,
        "shopgoodwill",
        ["synthetic-shop-style-bid.eml", "synthetic-shop-style-winning.eml"],
    )
    with client.app.state.sessions() as s:
        assert len(list(s.scalars(select(DiscoveredItem)))) == 1
        assert not list(s.scalars(select(Feedback)))
        assert not list(s.scalars(select(AlertInvoice)))
    batch = import_files(
        client,
        "shopgoodwill",
        ["synthetic-shop-style-receipt.eml", "synthetic-shop-style-receipt.eml"],
    )
    with client.app.state.sessions() as s:
        invoice = s.scalar(select(AlertInvoice))
        assert invoice.data["status"] == "RECONCILED"
        assert invoice.data["components"]["item_subtotal"] == "18.00"
        assert invoice.data["components"]["sales_tax"] == "0.00"
        assert invoice.data["components"]["shipping_handling"] == "9.00"
        assert invoice.data["components"]["order_total"] == "27.00"
        assert len(invoice.data["item_ids"]) == 1
        assert len(list(s.scalars(select(AlertObservation)))) == 3
        assert s.get(AlertImport, batch).stats["duplicate_messages"] == 1
        assert not list(s.scalars(select(Feedback)))
    assert client.post("/invoices/1/confirm", data=paid_form()).status_code == 200
    import_files(client, "shopgoodwill", ["synthetic-shop-style-bid.eml"])
    with client.app.state.sessions() as s:
        listing = s.get(Listing, 1)
        assert listing.review_state == "PURCHASED"
        assert listing.data["item_price"] == "18"
        assert listing.data["shipping_cost"] == "8"
        assert listing.data["shipping_status"] == "UNKNOWN"


def test_shopgoodwill_style_multi_receipt_never_allocates_order_charges(client):
    import_files(client, "shopgoodwill", ["synthetic-shop-style-multi-receipt.eml"])
    with client.app.state.sessions() as s:
        invoice = s.scalar(select(AlertInvoice))
        assert invoice.data["status"] == "RECONCILED"
        assert invoice.data["components"]["order_total"] == "51.00"
        assert len(invoice.data["item_ids"]) == 2
        for item in s.scalars(select(DiscoveredItem)):
            assert item.normalized["shipping_cost"] is None
            assert item.normalized["handling_fee"] is None
            assert item.normalized["sales_tax"] is None
    assert client.post("/invoices/1/confirm", data=paid_form()).status_code == 422


@pytest.mark.parametrize(
    "subject,body",
    [
        ("Bid Notification", "SYNTHETIC contact number\n910000000099\n"),
        ("Payment Receipt", "Order: 910000000099\nTax: $0.00\n99669\n"),
        ("Newsletter", "Item\n910000000099\n"),
        ("Bid Notification", "Phone: 910000000099\nOrder ID: 910000000098\n"),
    ],
)
def test_shopgoodwill_never_guesses_arbitrary_numeric_identity(client, subject, body):
    with client.app.state.sessions() as s:
        definition = SourceDefinition.model_validate(s.get(IntakeSource, "shopgoodwill").data)
    message = f"Subject: SYNTHETIC shopgoodwill.com {subject}\n\n{body}".encode()
    assert not parse_message(message, "shopgoodwill", definition).candidates


def test_shopgoodwill_text_item_reference_limit(client):
    content = "Subject: SYNTHETIC Payment Receipt\n\n" + "\n".join(
        f"Item: {910000000000 + i}" for i in range(101)
    )
    response = client.post("/inbox/import", data={"source_id": "shopgoodwill", "content": content})
    assert response.status_code == 422
    with client.app.state.sessions() as s:
        assert not list(s.scalars(select(AlertMessage)))
        assert not list(s.scalars(select(DiscoveredItem)))


@pytest.mark.parametrize(
    "embedded,expected",
    [
        ("https:%2F%2Fhibid.com%2Flot%2F910000000101%2Fring", "https://hibid.com/lot/910000000101"),
        ("https://hibid.com/catalog/910000000100/estate", "https://hibid.com/catalog/910000000100"),
        (
            "https:%2F%2Fsynthetic-auctions.hibid.com%2Flot%2F910000000102%3Futm_source%3DSYNTHETIC",
            "https://synthetic-auctions.hibid.com/lot/910000000102",
        ),
    ],
)
def test_hibid_offline_explicit_embedded_targets(embedded, expected):
    wrapper = f"https://clicks.auctions.hibid.com/SYNTHETIC/{embedded}/1/SYNTHETIC"
    assert hibid_embedded_target(wrapper) == expected
    definition = SourceDefinition(name="HiBid", domains=["hibid.com"])
    assert safe_link(wrapper, definition) is None
    assert "SYNTHETIC" not in expected  # Canonical identities never contain wrapper tokens.


@pytest.mark.parametrize(
    "url",
    [
        "https://clicks.auctions.hibid.com/ls/click?upn=SYNTHETIC-OPAQUE",
        "https://clicks.auctions.hibid.com/SYNTHETIC?url=https://hibid.com/lot/1",
        "https://clicks.auctions.hibid.com.evil.invalid/SYNTHETIC/https://hibid.com/lot/1",
        "https://other.hibid.com/SYNTHETIC/https://hibid.com/lot/1",
        "http://clicks.auctions.hibid.com/SYNTHETIC/https://hibid.com/lot/1",
        "https://user@clicks.auctions.hibid.com/SYNTHETIC/https://hibid.com/lot/1",
        "https://clicks.auctions.hibid.com:8443/SYNTHETIC/https://hibid.com/lot/1",
        "https://clicks.auctions.hibid.com/SYNTHETIC/http://hibid.com/lot/1",
        "https://clicks.auctions.hibid.com/SYNTHETIC/https://hibid.com.evil.invalid/lot/1",
        "https://clicks.auctions.hibid.com/SYNTHETIC/https://user@hibid.com/lot/1",
        "https://clicks.auctions.hibid.com/SYNTHETIC/https://hibid.com:8443/lot/1",
        "https://clicks.auctions.hibid.com/SYNTHETIC/https://hibid.com/redirect/lot/1",
        "https://clicks.auctions.hibid.com/SYNTHETIC/https://hibid.com/lot/1evil",
        "https://clicks.auctions.hibid.com/SYNTHETIC/https://hibid.com/lot/1/../../catalog/2",
        "https://clicks.auctions.hibid.com/SYNTHETIC/https://hibid.com/lot/1/%252e%252e/catalog/2",
        "https://clicks.auctions.hibid.com/SYNTHETIC/https%253A%252F%252Fhibid.com%252Flot%252F1",
        "https://clicks.auctions.hibid.com/SYNTHETIC/https://hibid.com/lot/1/https://hibid.com/lot/2",
        "https://clicks.auctions.hibid.com/SYNTHETIC/https://hibid.com/lot/1%0A",
        "https://clicks.auctions.hibid.com/SYNTHETIC/https://hibid.com/lot/1#SYNTHETIC",
        "https://clicks.auctions.hibid.com/SYNTHETIC/https://hibid.com/lot/1\\evil",
        "https://clicks.auctions.hibid.com/SYNTHETIC/https://hibid.com/lot/1/" + "x" * 8192,
    ],
)
def test_hibid_opaque_unsafe_ambiguous_and_unverified_wrappers_stay_unresolved(url):
    assert hibid_embedded_target(url) is None


def test_hibid_html_anchor_without_href_value_needs_review(client):
    message = EmailMessage()
    message["Subject"] = "SYNTHETIC HiBid newsletter"
    message.set_content("SYNTHETIC empty anchor")
    message.add_alternative(
        "<html><body><a href>SYNTHETIC no URL</a></body></html>", subtype="html"
    )
    with client.app.state.sessions.begin() as s:
        import_alerts(s, "hibid", [message.as_bytes()])
        assert not list(s.scalars(select(DiscoveredItem)))
        assert s.scalar(select(AlertMessage)).data["status"] == "NEEDS_REVIEW"


def test_hibid_embedded_newsletter_lots_catalog_and_repeated_import(client):
    batch = import_files(client, "hibid", ["synthetic-hibid-embedded-promo.eml"] * 2)
    with client.app.state.sessions() as s:
        assert s.get(AlertImport, batch).stats["duplicate_messages"] == 1
        message = s.scalar(select(AlertMessage))
        assert message.message_type == "PROMOTIONAL_NEWSLETTER"
        assert message.data["wrapped_targets_recovered_offline"] == 3
        catalog = message.data["catalogs"][0]
        assert catalog["auction_id"] == "910000000100"
        assert catalog["url"] == "https://hibid.com/catalog/910000000100"
        items = list(s.scalars(select(DiscoveredItem)))
        assert len(items) == 2
        assert {i.normalized["item_price"] for i in items} == {"12.00", "20.00"}
        assert all(i.normalized["shipping_status"] == "UNKNOWN" for i in items)
        assert all(i.normalized["shipping_cost"] is None for i in items)
        assert all(i.normalized["raw_search_phrase"] is None for i in items)
        for item in items:
            assert "clicks.auctions" not in json.dumps(item.normalized)
            assert "SYNTHETIC/https" not in json.dumps(item.original_data)
        for observation in s.scalars(select(AlertObservation)):
            assert observation.data["identity_method"] == "EXPLICIT_EMBEDDED_HTTPS_TARGET"
            assert observation.query_text is None and not observation.attribution_verified
    # A later direct-link alert is another observation of the same source item.
    direct = b"Subject: SYNTHETIC HiBid newsletter\nX-Turquoise-Synthetic: true\n\nhttps://hibid.com/lot/910000000101/ring"
    with client.app.state.sessions.begin() as s:
        import_alerts(s, "hibid", [direct])
        assert len(list(s.scalars(select(DiscoveredItem)))) == 2
        assert len(list(s.scalars(select(AlertObservation)))) == 3


def test_hibid_embedded_catalog_closing_notice_never_becomes_a_lot(client):
    import_files(client, "hibid", ["synthetic-hibid-embedded-closing.eml"])
    with client.app.state.sessions() as s:
        message = s.scalar(select(AlertMessage))
        assert message.message_type == "AUCTION_CLOSING_REMINDER"
        assert message.data["catalogs"][0]["auction_id"] == "910000000100"
        assert message.data["closing_time_raw"] == "November 5, 2026 7:00 PM CST"
        assert message.data["closing_timezone_raw"] == "CST"
        assert message.data["closing_at"] is None  # No invented timezone offset.
        assert not list(s.scalars(select(DiscoveredItem)))


def test_hibid_opaque_closing_notice_is_private_review_evidence_only(client):
    import_files(client, "hibid", ["synthetic-hibid-opaque-closing.eml"])
    with client.app.state.sessions() as s:
        message = s.scalar(select(AlertMessage))
        assert message.data["status"] == "NEEDS_REVIEW"
        assert message.data["catalogs"] == []
        assert not list(s.scalars(select(DiscoveredItem)))
        definition = SourceDefinition.model_validate(s.get(IntakeSource, "shopgoodwill").data)
        assert not parse_message(
            raw("synthetic-hibid-embedded-promo.eml"), "shopgoodwill", definition
        ).candidates


def test_full_synthetic_intake_fifteen_items_and_no_auto_purchases(client):
    for entry in json.loads((ROOT / "manifest.json").read_text()):
        import_files(client, entry["source"], entry["files"])
    with client.app.state.sessions() as s:
        items = list(s.scalars(select(DiscoveredItem)))
        assert len(items) == 15
        assert sum(i.source == "ShopGoodwill FIXTURE" for i in items) == 5
        assert sum(i.source == "HiBid FIXTURE" for i in items) == 5
        assert all(i.normalized["piece_count"] == 1 for i in items)  # never a five-item email "lot"
        assert all(i.normalized["shipping_status"] == "UNKNOWN" for i in items)
        assert all(i.normalized["resale_low"] is None for i in items)
        assert not list(s.scalars(select(Feedback))) and not list(s.scalars(select(Listing)))
        assert len(list(s.scalars(select(AlertObservation)))) == 23
    for route in [
        "/inbox",
        "/sources",
        "/saved-searches",
        "/estate-groups",
        "/invoices",
        "/inbox/import",
    ]:
        r = client.get(route)
        assert r.status_code == 200, route
    assert (
        client.get("/inbox?source=ShopGoodwill%20FIXTURE").text.count(
            'class="listing-card inbox-card"'
        )
        == 5
    )
    assert client.get("/inbox?opportunity=ring").text.count('class="listing-card inbox-card"') >= 5


def test_message_idempotency_and_independent_repeat_observations(client):
    first = import_files(client, "shopgoodwill", ["synthetic-shop-search.eml"])
    second = import_files(client, "shopgoodwill", ["synthetic-shop-search.eml"])
    altered = raw("synthetic-shop-search.eml").replace(b"12:01:00", b"12:02:00")
    with client.app.state.sessions.begin() as s:
        import_alerts(s, "shopgoodwill", [altered])
    with client.app.state.sessions() as s:
        assert s.get(AlertImport, first).stats["new_candidates"] == 5
        assert s.get(AlertImport, second).stats["duplicate_messages"] == 1
        assert s.get(AlertMessage, 1).import_count == 2
        assert len(list(s.scalars(select(AlertMessage)))) == 2
        assert len(list(s.scalars(select(DiscoveredItem)))) == 5
        assert len(list(s.scalars(select(AlertObservation)))) == 10
        assert base64.b64decode(s.get(AlertMessage, 1).raw_b64) == raw("synthetic-shop-search.eml")


def test_received_sent_import_and_listing_times_are_distinct(client):
    import_files(client, "shopgoodwill", ["synthetic-shop-search.eml"])
    with client.app.state.sessions() as s:
        message = s.get(AlertMessage, 1)
        assert message.sent_at != message.received_at
        assert message.imported_at != message.received_at
        assert s.get(DiscoveredItem, 1).normalized["listing_time"] is None
        definition = SourceDefinition.model_validate(s.get(IntakeSource, "shopgoodwill").data)
    p = parse_message(
        b"Subject: Alert\nDate: Thu, 29 Oct 2026 12:00:00 -0800\n\nNo item",
        "shopgoodwill",
        definition,
    )
    assert p.sent_at is not None and p.received_at is None


def test_hibid_catalog_not_lot_and_timezone_not_invented(client):
    import_files(client, "hibid", ["synthetic-hibid-closing.eml"])
    with client.app.state.sessions() as s:
        m = s.get(AlertMessage, 1)
        assert m.message_type == "AUCTION_CLOSING_REMINDER"
        assert m.data["catalogs"][0]["auction_id"] == "900000000100"
        assert m.data["closing_time_raw"] == "2026-11-05T19:00:00-06:00"
        assert m.data["closing_timezone_raw"] == "-06:00"
        assert not list(s.scalars(select(DiscoveredItem)))
    assert "not treated as a single jewelry lot" in client.get("/inbox/messages/1").text


def test_hibid_multiple_lots_premiums_and_shipping_unknown(client):
    import_files(client, "hibid", ["synthetic-hibid-promo.eml"])
    with client.app.state.sessions() as s:
        items = list(s.scalars(select(DiscoveredItem)))
        assert len(items) == 5
        assert {i.normalized["sale_auction_id"] for i in items} == {"900000000100"}
        assert {i.normalized["seller_id"] for i in items} == {"SYNTHETIC auction house"}
        assert all(i.normalized["buyer_premium"] is None for i in items)
        assert all(
            i.normalized["shipping_cost"] is None and i.normalized["shipping_status"] == "UNKNOWN"
            for i in items
        )
        obs = list(s.scalars(select(AlertObservation)))
        assert all(o.data["buyer_premium_rate"] == "0.15" for o in obs)
        assert all(o.query_text is None and not o.attribution_verified for o in obs)
        assert all(i.normalized["item_end_time"] is None for i in items)
        definition = SourceDefinition.model_validate(s.get(IntakeSource, "hibid").data)
    fee_text = (
        b"Subject: SYNTHETIC lot alert\n\nTitle: SYNTHETIC ring\n"
        b"https://hibid.com/lot/900000000999/demo\nBuyer premium: $2.50\n"
        b"Service fee: $1.00\nSales tax: $0.50\nShipping/Handling: $9.00"
    )
    proposed = parse_message(fee_text, "hibid", definition).candidates[0]
    assert proposed.item.buyer_premium == Decimal("2.50")
    assert proposed.item.buyer_service_fee == Decimal("1.00")
    assert proposed.item.sales_tax == Decimal("0.50")
    assert proposed.item.cost_status["buyer_service_fee"] == "ESTIMATED"
    assert proposed.evidence["quoted_shipping_handling"] == "9.00"
    assert proposed.item.shipping_cost is None and proposed.item.handling_fee is None
    with client.app.state.sessions.begin() as s:
        import_alerts(s, "hibid", [fee_text])
        import_alerts(s, "hibid", [fee_text.replace(b"Service fee: $1.00", b"Service fee: $1.50")])
        item = s.scalar(select(DiscoveredItem).where(DiscoveredItem.ebay_item_id == "900000000999"))
        assert item.normalized["buyer_service_fee"] == "1.50"
        assert item.normalized["sales_tax"] == "0.50"
        assert item.listing_id is None
        assert (
            len(
                list(s.scalars(select(AlertObservation).where(AlertObservation.item_id == item.id)))
            )
            == 2
        )


def test_exact_query_case_spaces_and_provisional_attribution(client):
    body = (
        raw("synthetic-shop-search.eml")
        .replace(b"Saved search: nature america", b"Saved search:  Nature America  ")
        .replace(b"\n", b"\r\n")
    )
    with client.app.state.sessions.begin() as s:
        import_alerts(s, "shopgoodwill", [body])
    with client.app.state.sessions() as s:
        o = s.scalar(select(AlertObservation))
        assert o.query_text == " Nature America  " and not o.attribution_verified
        assert (
            s.get(DiscoveredItem, o.item_id).normalized["raw_search_phrase"] == " Nature America  "
        )
    assert client.post("/inbox/observations/1/confirm-query", data={}).status_code == 422
    assert (
        client.post("/inbox/observations/1/confirm-query", data={"confirm": "yes"}).status_code
        == 200
    )
    with client.app.state.sessions() as s:
        assert s.get(AlertObservation, 1).attribution_verified
        assert s.scalar(select(SearchPattern).where(SearchPattern.phrase == " Nature America  "))


def test_generic_title_typo_and_promotions_never_query_credit(client):
    import_files(client, "ctbids", ["synthetic-ctbids.eml"])
    client.post("/inbox/candidates/1/review")
    client.post("/listings/1/feedback", data={"action": "Purchased"})
    from radar.workflow import pattern_effectiveness

    with client.app.state.sessions() as s:
        assert all(o.query_text is None for o in s.scalars(select(AlertObservation)))
        assert not pattern_effectiveness(s)
        assert all(status["real_matches"] == 0 for status in search_status(s))
    assert (
        client.post("/inbox/observations/1/confirm-query", data={"confirm": "yes"}).status_code
        == 422
    )


def test_existing_review_and_purchase_evidence_survive_later_alert(client):
    import_files(client, "shopgoodwill", ["synthetic-shop-search.eml"])
    client.post("/inbox/candidates/1/review")
    client.post(
        "/listings/1/edit",
        data={
            "shipping_cost": "8",
            "shipping_cost_status": "CONFIRMED",
            "condition": "Owner evidence",
        },
    )
    client.post("/listings/1/feedback", data={"action": "Purchased"})
    with client.app.state.sessions() as s:
        before = dict(s.get(Listing, 1).data)
    import_files(client, "shopgoodwill", ["synthetic-shop-bid.eml", "synthetic-shop-paid.eml"])
    client.post("/inbox/candidates/1/review")
    with client.app.state.sessions() as s:
        assert len(list(s.scalars(select(Listing)))) == 1
        assert s.get(Listing, 1).data == before
        assert s.get(Listing, 1).review_state == "PURCHASED"
        assert len(list(s.scalars(select(Feedback)))) == 1
    assert (
        client.post("/inbox/candidates/1/status", data={"status": "IRRELEVANT"}).status_code == 200
    )
    with client.app.state.sessions() as s:
        assert s.get(Listing, 1).review_state == "PURCHASED"


def test_multi_email_purchase_reconciliation_without_bid_ownership(client):
    import_files(
        client,
        "shopgoodwill",
        ["synthetic-shop-bid.eml", "synthetic-shop-outbid.eml", "synthetic-shop-won.eml"],
    )
    with client.app.state.sessions() as s:
        invoice = s.scalar(select(AlertInvoice))
        assert invoice.data["status"] == "INCOMPLETE"
        assert not list(s.scalars(select(Feedback)))
    import_files(client, "shopgoodwill", ["synthetic-shop-paid.eml", "synthetic-shop-shipped.eml"])
    with client.app.state.sessions() as s:
        invoice = s.scalar(select(AlertInvoice))
        assert invoice.data["status"] == "RECONCILED"
        assert invoice.data["components"]["order_total"] == "27.00"
        assert invoice.data["components"]["item_subtotal"] == "18.00"  # not bid 10
        assert invoice.data["components"]["shipping_handling"] == "9.00"
        assert invoice.confirmed_at is None
        assert not list(s.scalars(select(Listing)))


def paid_form(**updates):
    return dict(
        confirm_owned="yes",
        item_price="18",
        buyer_premium="0",
        buyer_service_fee="0",
        sales_tax="0",
        shipping_cost="8",
        handling_fee="1",
        shipping_status="UNKNOWN",
        **updates,
    )


def test_paid_receipt_confirmation_actual_costs_idempotent(client):
    import_files(client, "shopgoodwill", ["synthetic-shop-paid.eml"])
    assert client.post("/invoices/1/confirm", data={}).status_code == 422
    bad = paid_form()
    bad["shipping_cost"] = "9"
    assert client.post("/invoices/1/confirm", data=bad).status_code == 422
    assert client.post("/invoices/1/confirm", data=paid_form()).status_code == 200
    assert client.post("/invoices/1/confirm", data=paid_form()).status_code == 200
    with client.app.state.sessions() as s:
        row = s.get(Listing, 1)
        assert row.review_state == "PURCHASED"
        assert row.data["item_price"] == "18" and row.data["shipping_cost"] == "8"
        assert row.data["cost_status"]["shipping_cost"] == "CONFIRMED"
        assert (
            row.data["shipping_status"] == "UNKNOWN"
        )  # receipt payment is not Alaska delivery proof
        assert len(list(s.scalars(select(Feedback)))) == 1
        assert len(list(s.scalars(select(ListingEdit)))) == 1


def test_multi_item_receipt_keeps_shipping_at_order_level(client):
    import_files(client, "shopgoodwill", ["synthetic-shop-multi-paid.eml"])
    with client.app.state.sessions() as s:
        invoice = s.scalar(select(AlertInvoice))
        assert invoice.data["status"] == "RECONCILED" and len(invoice.data["item_ids"]) == 2
        assert invoice.data["components"]["shipping_handling"] == "12.00"
        assert all(i.normalized["shipping_cost"] is None for i in s.scalars(select(DiscoveredItem)))
    assert client.post("/invoices/1/confirm", data=paid_form()).status_code == 422
    assert "No equal allocation" in client.get("/invoices/1").text
    # Nonzero order fees preceding the first lot must not become first-lot costs.
    other = (
        raw("synthetic-shop-multi-paid.eml")
        .replace(b"SYNTHETIC-ORDER-2", b"SYNTHETIC-ORDER-3")
        .replace(b"900000000002", b"900000000012")
        .replace(b"900000000003", b"900000000013")
        .replace(b"Sales tax: $0.00", b"Sales tax: $3.00")
        .replace(b"Shipping/Handling: $12.00", b"Shipping: $10.00\nHandling: $2.00")
        .replace(
            b"Order total: $48.00", b"Buyer premium: $5.00\nService fee: $2.00\nOrder total: $58.00"
        )
    )
    with client.app.state.sessions.begin() as s:
        import_alerts(s, "shopgoodwill", [other])
        invoice = s.get(AlertInvoice, 2)
        assert invoice.data["status"] == "RECONCILED"
        assert invoice.data["components"]["sales_tax"] == "3.00"
        for item in s.scalars(select(DiscoveredItem)):
            for fee in [
                "shipping_cost",
                "handling_fee",
                "sales_tax",
                "buyer_premium",
                "buyer_service_fee",
            ]:
                assert item.normalized[fee] is None


@pytest.mark.parametrize(
    "change,status", [(b"Order total: $27.00", "MISMATCH"), (b"Sales tax: $0.00", "INCOMPLETE")]
)
def test_receipt_mismatch_and_missing_components(client, change, status):
    data = raw("synthetic-shop-paid.eml")
    data = data.replace(
        change, b"Order total: $30.00" if status == "MISMATCH" else b"Tax not stated"
    )
    with client.app.state.sessions.begin() as s:
        import_alerts(s, "shopgoodwill", [data])
    with client.app.state.sessions() as s:
        assert s.scalar(select(AlertInvoice)).data["status"] == status
    assert client.post("/invoices/1/confirm", data=paid_form()).status_code == 422


def test_conflicting_payment_receipts_do_not_replace_paid_history(client):
    import_files(client, "shopgoodwill", ["synthetic-shop-paid.eml"])
    second = raw("synthetic-shop-paid.eml").replace(b"Order total: $27.00", b"Order total: $29.00")
    with client.app.state.sessions.begin() as s:
        import_alerts(s, "shopgoodwill", [second])
    with client.app.state.sessions() as s:
        assert s.scalar(select(AlertInvoice)).data["status"] == "CONFLICT"


def test_html_safety_no_resources_unsafe_links_or_attachment_content(client, caplog):
    msg = EmailMessage()
    msg["Subject"] = "SYNTHETIC Featured auctions newsletter"
    msg.set_content("Fallback")
    msg.add_alternative(
        (
            '<script>secret_bad_code()</script><img src="https://evil.invalid/pixel">'
            '<form>hidden</form><article><a href="javascript:alert(1)">bad</a>'
            '<a href="https://hibid.com.evil.invalid/lot/1">bad</a></article>'
            '<article><a href="https://hibid.com/lot/900000000101/demo">'
            "SYNTHETIC safe ring</a><p>Current bid: $9.00</p></article>"
        ),
        subtype="html",
    )
    msg.add_attachment(
        b"PRIVATE-DUMMY-ATTACHMENT", maintype="text", subtype="plain", filename="do-not-run.txt"
    )
    with client.app.state.sessions.begin() as s:
        import_alerts(s, "hibid", [msg.as_bytes()])
    response = client.get("/inbox/messages/1")
    assert response.status_code == 200
    assert '<img src="https://evil.invalid' not in response.text
    assert (
        'href="javascript:' not in response.text
        and "<script>secret_bad_code()" not in response.text
    )
    with client.app.state.sessions() as s:
        m = s.get(AlertMessage, 1)
        assert m.data["attachments_ignored"] == 1
        assert "PRIVATE-DUMMY-ATTACHMENT" not in m.safe_text
        assert len(list(s.scalars(select(DiscoveredItem)))) == 1
        assert "secret_bad_code" not in m.safe_text
    assert "PRIVATE-DUMMY-ATTACHMENT" not in caplog.text
    from radar.intake.parsers import safe_link

    definition = SourceDefinition(name="HiBid", domains=["hibid.com"])
    assert (
        safe_link("https://hibid.com/lot/1?utm_campaign=private&id=1", definition)
        == "https://hibid.com/lot/1?id=1"
    )
    assert safe_link("https://user:password@hibid.com/lot/1", definition) is None


def test_attached_eml_is_not_traversed(client):
    msg = EmailMessage()
    msg["Subject"] = "SYNTHETIC empty notification"
    msg.set_content("No listing in the actual body")
    attached = EmailMessage()
    attached.set_content("https://shopgoodwill.com/item/900000000001")
    msg.add_attachment(attached)
    with client.app.state.sessions.begin() as s:
        import_alerts(s, "shopgoodwill", [msg.as_bytes()])
    with client.app.state.sessions() as s:
        assert not list(s.scalars(select(DiscoveredItem)))


def test_upload_limits_multiple_files_and_sanitized_errors(client):
    response = client.post(
        "/inbox/import",
        data={"source_id": "shopgoodwill"},
        files=[
            ("files", ("one.eml", raw("synthetic-shop-search.eml"), "message/rfc822")),
            ("files", ("same.eml", raw("synthetic-shop-search.eml"), "message/rfc822")),
        ],
    )
    assert response.status_code == 200 and "Private intake complete" in response.text
    assert (
        client.post(
            "/inbox/import",
            data={"source_id": "shopgoodwill"},
            files={"files": ("bad.exe", b"not executed")},
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/inbox/import",
            data={"source_id": "shopgoodwill"},
            files={"files": ("large.eml", b"x" * 512001)},
        ).status_code
        == 413
    )
    assert (
        client.post(
            "/inbox/import", data={"source_id": "shopgoodwill", "content": "x" * 3100000}
        ).status_code
        == 413
    )
    files = [("files", (f"{n}.eml", b"small", "message/rfc822")) for n in range(11)]
    assert (
        client.post("/inbox/import", data={"source_id": "shopgoodwill"}, files=files).status_code
        == 413
    )
    r = client.post(
        "/inbox/import", data={"source_id": "not-a-source", "content": "PRIVATE-DUMMY-DATA"}
    )
    assert r.status_code == 422 and "PRIVATE-DUMMY-DATA" not in r.text


def test_generic_manual_csv_and_source_identity_separation(client):
    row = {
        "source": "CTBids",
        "source_listing_id": "owner-id",
        "raw_title": "SYNTHETIC manual ring",
        "item_price": "10",
        "listing_url": "https://ctbids.com/owner-supplied/one",
    }
    assert (
        client.post(
            "/inbox/import-data", data={"source_id": "ctbids", "content": json.dumps([row])}
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/inbox/import-data", data={"source_id": "ctbids", "content": json.dumps([row])}
        ).status_code
        == 200
    )
    csv = (
        "source,source_listing_id,raw_title,item_price\n"
        "EstateSales.org,owner-id,SYNTHETIC other ring,12\n"
    )
    assert (
        client.post(
            "/inbox/import-data",
            data={"source_id": "estatesales-org", "format": "csv", "content": csv},
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/inbox/manual",
            data={
                "source_id": "mercari",
                "listing_url": "https://www.mercari.com/us/item/SYNTHETIC-001",
                "source_listing_id": "SYNTHETIC-001",
                "content": "SYNTHETIC old ring\nPrice: $12",
            },
        ).status_code
        == 200
    )
    with client.app.state.sessions() as s:
        assert len(list(s.scalars(select(DiscoveredItem)))) == 3
        assert not list(s.scalars(select(Listing)))


def test_saved_search_owner_state_and_real_attribution_gate(client):
    import_files(client, "shopgoodwill", ["synthetic-shop-search.eml"])
    with client.app.state.sessions() as s:
        saved = s.scalar(
            select(SavedSourceSearch).where(
                SavedSourceSearch.source_id == "shopgoodwill",
                SavedSourceSearch.phrase == "nature america",
            )
        )
        sid = saved.id
        assert all(v["real_matches"] == 0 for v in search_status(s))
    assert (
        client.post(
            f"/saved-searches/{sid}", data={"selected": "true", "configured": "true"}
        ).status_code
        == 200
    )
    client.post("/inbox/observations/1/confirm-query", data={"confirm": "yes"})
    with client.app.state.sessions() as s:
        assert s.get(SavedSourceSearch, sid).configured
        assert all(
            v["real_matches"] == 0 for v in search_status(s)
        )  # synthetic never counts as real
    assert client.get("/saved-searches?source=shopgoodwill").status_code == 200
    assert "Copy exact phrase" in client.get("/saved-searches?source=shopgoodwill").text


def test_optional_parser_rules_only_when_explicitly_verified(client):
    with client.app.state.sessions() as s:
        data = dict(s.get(IntakeSource, "other").data)
    data.update(domains=["estate.example.invalid"], item_path_prefix="/lots/")
    assert client.post("/sources/other", data={"payload": json.dumps(data)}).status_code == 422
    data.update(
        sample_verified=True,
        sample_reference="Permitted SYNTHETIC fixture",
        sample_kind="SYNTHETIC",
    )
    assert client.post("/sources/other", data={"payload": json.dumps(data)}).status_code == 422
    assert (
        client.post(
            "/sources/other", data={"payload": json.dumps(data), "confirm_sample": "yes"}
        ).status_code
        == 200
    )
    text = b"Subject: SYNTHETIC notice\n\nhttps://estate.example.invalid/lots/SYNTHETIC-1"
    with client.app.state.sessions.begin() as s:
        import_alerts(s, "other", [text])
    with client.app.state.sessions() as s:
        assert s.scalar(select(DiscoveredItem)).normalized["source_listing_id"] == "SYNTHETIC-1"


def test_no_description_similarity_merges_without_stable_identity(client):
    rows = [{"source": "CTBids", "raw_title": "SYNTHETIC same description"}] * 2
    assert (
        client.post(
            "/inbox/import-data", data={"source_id": "ctbids", "content": json.dumps(rows)}
        ).status_code
        == 422
    )
    with client.app.state.sessions() as s:
        assert not list(s.scalars(select(DiscoveredItem)))


def test_grouping_source_separation_and_owner_combined_shipping_evidence(client):
    import_files(client, "hibid", ["synthetic-hibid-promo.eml", "synthetic-hibid-closing.eml"])
    client.post("/inbox/candidates/1/review")
    client.post(
        "/listings/1/edit",
        data={
            "combined_shipping_available": "true",
            "combined_shipping_evidence": "SYNTHETIC confirmed quote; no discount allocated",
        },
    )
    with client.app.state.sessions() as s:
        groups = estate_groups(
            inbox_cards(s, preferences(s)), list(s.scalars(select(AlertMessage)))
        )
        auction = next(g for g in groups if g["kind"] == "auction")
        assert len(auction["cards"]) == 5 and auction["rings"] == 3 and auction["lots"] == 2
        assert auction["notices"][-1].data["closing_timezone_raw"] == "-06:00"
    response = client.get("/estate-groups")
    assert response.status_code == 200 and "OWNER CONFIRMED" in response.text
    assert "not a per-item deadline" in response.text


def test_real_attributable_search_requires_explicit_owner_evidence(client):
    # Simulate an owner-provided message without trusting its sender; content stays synthetic.
    data = raw("synthetic-shop-search.eml").replace(b"X-Turquoise-Synthetic: true\n", b"")
    with client.app.state.sessions.begin() as s:
        import_alerts(s, "shopgoodwill", [data])
    with client.app.state.sessions() as s:
        before = next(
            v
            for v in search_status(s)
            if v["saved"].source_id == "shopgoodwill" and v["saved"].phrase == "nature america"
        )
        assert before["real_matches"] == 0
    client.post("/inbox/observations/1/confirm-query", data={"confirm": "yes"})
    with client.app.state.sessions() as s:
        after = next(
            v
            for v in search_status(s)
            if v["saved"].source_id == "shopgoodwill" and v["saved"].phrase == "nature america"
        )
        assert after["real_matches"] == 1


def test_plaintext_batch_separator_preserves_messages_not_one_lot(client):
    content = (
        raw("synthetic-shop-bid.eml").decode()
        + "\n===NEXT MESSAGE===\n"
        + raw("synthetic-shop-outbid.eml").decode()
    )
    r = client.post("/inbox/import", data={"source_id": "shopgoodwill", "content": content})
    assert r.status_code == 200
    with client.app.state.sessions() as s:
        assert len(list(s.scalars(select(AlertMessage)))) == 2
        assert len(list(s.scalars(select(DiscoveredItem)))) == 1
        assert not list(s.scalars(select(Feedback)))


def test_final_paid_line_items_can_reconcile_without_global_subtotal(client):
    data = raw("synthetic-shop-paid.eml").replace(b"Items subtotal: $18.00\n", b"")
    with client.app.state.sessions.begin() as s:
        import_alerts(s, "shopgoodwill", [data])
    with client.app.state.sessions() as s:
        invoice = s.scalar(select(AlertInvoice))
        assert invoice.data["status"] == "RECONCILED" and invoice.data["derived_subtotal"]
        assert invoice.data["components"]["item_subtotal"] == "18.00"


def test_conflicting_final_line_items_and_non_usd_receipt_block_confirmation(client):
    data = (
        raw("synthetic-shop-paid.eml")
        .replace(b"Items subtotal: $18.00", b"Items subtotal: $20.00")
        .replace(b"Order total: $27.00", b"Order total: $29.00")
    )
    with client.app.state.sessions.begin() as s:
        import_alerts(s, "shopgoodwill", [data])
    with client.app.state.sessions() as s:
        assert s.scalar(select(AlertInvoice)).data["status"] == "CONFLICT"
    data = (
        raw("synthetic-shop-paid.eml").replace(b"SYNTHETIC-ORDER-1", b"SYNTHETIC-CAD-ORDER")
        + b"\nCurrency: CAD\n"
    )
    with client.app.state.sessions.begin() as s:
        import_alerts(s, "shopgoodwill", [data])
    with client.app.state.sessions() as s:
        assert list(s.scalars(select(AlertInvoice)))[-1].data["status"] == "UNSUPPORTED_CURRENCY"


def test_custom_sources_and_stable_registry_identity(client):
    r = client.post(
        "/sources",
        data={
            "source_id": "custom-owner-estate",
            "name": "SYNTHETIC independent estate",
            "domains": "estate.example.invalid",
        },
    )
    assert r.status_code == 200
    assert (
        client.post(
            "/sources",
            data={
                "source_id": "custom-owner-estate",
                "name": "Duplicate",
                "domains": "estate.example.invalid",
            },
        ).status_code
        == 422
    )
    import_files(client, "shopgoodwill", ["synthetic-shop-search.eml"])
    with client.app.state.sessions() as s:
        config = dict(s.get(IntakeSource, "shopgoodwill").data)
    config["name"] = "Renamed marketplace"
    assert (
        client.post("/sources/shopgoodwill", data={"payload": json.dumps(config)}).status_code
        == 422
    )
