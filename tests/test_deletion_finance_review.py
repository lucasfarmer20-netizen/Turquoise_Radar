"""Synthetic financial records only; deletion must not erase owner monetary evidence."""

import json
import sqlite3
from decimal import Decimal

from radar.db import Listing, ListingEdit, database
from radar.ebay_deletion.processing import (
    FINANCIAL_AMOUNT_FIELDS,
    SAFE_FIELDS,
    sanitize_database,
    tombstone,
)
from radar.ebay_deletion.protocol import digest
from radar.finance import landed_cost
from radar.schemas import ListingInput, Preferences

SECRET = b"SYNTHETIC-financial-review-key-000000000000"
ACCOUNT = "SYNTHETIC-FINANCIAL-ACCOUNT"
SELLER = "SYNTHETIC-FINANCIAL-SELLER"


def financial_record(increment):
    amounts = {
        "item_price": "23.4567",
        "buyer_premium": "2.3456",
        "buyer_service_fee": "1.2345",
        "sales_tax": "0.4567",
        "shipping_cost": "7.1234",
        "handling_fee": "0.7891",
        "invoice_fee_allocation": "0.5678",
        "resale_low": "68.3456",
        "resale_high": "89.4567",
        "resale_buyer_shipping": "8.3456",
        "resale_buyer_tax": "3.2345",
    }
    amounts = {key: str(Decimal(value) + increment) for key, value in amounts.items()}
    return ListingInput(
        source="eBay",
        seller_id=SELLER,
        raw_title="SYNTHETIC seller title " + SELLER,
        raw_description="SYNTHETIC seller-derived description " + SELLER,
        listing_url="https://www.ebay.com/itm/SYNTHETIC",
        **amounts,
        currency="USD",
        buyer_profile="owner-auction-profile",
        selling_category="owner-jewelry-category",
        commission_tier="owner-tier",
        cost_status={**{key: "CONFIRMED" for key in amounts}, "buyer_profile": "CONFIRMED"},
        resale_shipping_status="CONFIRMED",
        resale_tax_status="CONFIRMED",
        resale_confirmed=True,
        capture_confirmed=True,
        resale_basis="Owner supplied figures; source prose may name " + SELLER,
        invoice_allocation_confirmed=True,
        invoice_allocation_evidence="Owner allocation calculation; invoice from " + SELLER,
        shipping_status="CONFIRMED_TO_ALASKA",
        shipping_evidence="Destination evidence associated with " + SELLER,
        discovery_mode="production",
    ).model_dump(mode="json")


def assert_financial_record(retained, original):
    # Exact serialized decimals matter: no float conversion or unwanted cent rounding.
    for field in FINANCIAL_AMOUNT_FIELDS:
        assert retained[field] == original[field]
    for field in (
        "currency",
        "buyer_profile",
        "selling_category",
        "commission_tier",
        "resale_shipping_status",
        "resale_tax_status",
        "resale_confirmed",
        "capture_confirmed",
        "invoice_allocation_confirmed",
        "cost_status",
    ):
        assert retained[field] == original[field]
    parsed = ListingInput.model_validate(retained)
    assert parsed.invoice_allocation_confirmed and parsed.invoice_allocation_evidence
    assert "owner-confirmed invoice allocation" in parsed.invoice_allocation_evidence
    assert "original explanatory prose removed" in parsed.invoice_allocation_evidence
    assert "owner resale amounts and confirmation" in parsed.resale_basis
    assert SELLER not in json.dumps(retained)
    assert parsed.seller_id is None and parsed.listing_url is None
    assert parsed.shipping_status == "UNKNOWN"  # Delivery eligibility is separate from paid costs.
    assert landed_cost(parsed, Preferences()) == landed_cost(
        ListingInput.model_validate(original), Preferences()
    )


def test_complete_owner_finances_survive_current_and_independent_edit_snapshots(tmp_path):
    path = tmp_path / "synthetic-financial.db"
    before, after = financial_record(Decimal("0")), financial_record(Decimal("1.0000"))
    engine, sessions = database("sqlite:///" + str(path))
    with sessions.begin() as session:
        listing = Listing(
            dedup_key="synthetic-financial-listing",
            data=after,
            original_data={
                "provider": "eBay Browse",
                "mode": "production",
                "item": {"seller": {"userId": ACCOUNT, "username": SELLER}},
            },
        )
        session.add(listing)
        session.flush()
        session.add(ListingEdit(listing_id=listing.id, before_data=before, after_data=after))
    engine.dispose()
    identifiers = {"userId": digest(SECRET, "userId", ACCOUNT)}
    aliases = {digest(SECRET, "username", SELLER): identifiers["userId"]}
    result = sanitize_database(path, identifiers, SECRET, aliases=aliases)
    assert result[0] == "SANITIZED"
    with sqlite3.connect(path) as db:
        current = json.loads(db.execute("SELECT data FROM listings").fetchone()[0])
        edit_before, edit_after = map(
            json.loads, db.execute("SELECT before_data,after_data FROM listing_edits").fetchone()
        )
    assert_financial_record(current, after)
    assert_financial_record(edit_before, before)
    assert_financial_record(edit_after, after)


def test_retention_fields_use_actual_schema_and_missing_amount_statuses_are_coherent():
    assert SAFE_FIELDS <= ListingInput.model_fields.keys()
    record = ListingInput(source="eBay", raw_title="SYNTHETIC").model_dump(mode="json")
    record.update(
        cost_status={"sales_tax": "CONFIRMED", "tax": "CONFIRMED", "service_fee": "CONFIRMED"},
        resale_shipping_status="CONFIRMED",
        resale_tax_status="CONFIRMED",
        resale_confirmed=True,
        invoice_allocation_confirmed=True,
        invoice_allocation_evidence="SYNTHETIC explanation without an amount",
    )
    retained = ListingInput.model_validate(tombstone(record))
    assert retained.cost_status == {"sales_tax": "UNKNOWN"}
    assert retained.resale_shipping_status == retained.resale_tax_status == "UNKNOWN"
    assert retained.invoice_fee_allocation is None
    assert (
        not retained.invoice_allocation_confirmed and retained.invoice_allocation_evidence is None
    )
    assert not retained.resale_confirmed
