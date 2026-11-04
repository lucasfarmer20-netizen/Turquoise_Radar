"""Synthetic owner-editor currency corrections; never touch real research records."""

import re
from copy import deepcopy

import pytest
from sqlalchemy import select

from radar.db import Feedback, Listing, ListingEdit
from radar.owner_routes import ACQUISITION


def create_listing(client, item, currency="XXX"):
    data = item.model_dump(mode="json")
    data.update(
        source="SYNTHETIC currency editor",
        raw_title="SYNTHETIC recorded-price ring",
        currency=currency,
        synthetic=True,
        buyer_profile="synthetic_fee_profile",
        invoice_fee_allocation="1.25",
        invoice_allocation_confirmed=True,
        invoice_allocation_evidence="SYNTHETIC original allocation evidence",
        shipping_evidence="SYNTHETIC original destination and amount evidence",
    )
    model = type(item).model_validate(data)
    response = client.post("/listings", data={"payload": model.model_dump_json()})
    assert response.status_code == 200
    with client.app.state.sessions.begin() as session:
        row = session.scalar(select(Listing))
        row.review_state = "WATCH"
        session.add(
            Feedback(listing_id=row.id, action="Interested", note="SYNTHETIC retained feedback")
        )
        return row.id


def snapshot(client, listing_id):
    with client.app.state.sessions() as session:
        row = session.get(Listing, listing_id)
        return deepcopy(
            dict(
                data=row.data,
                original=row.original_data,
                dedup_key=row.dedup_key,
                state=row.review_state,
                last_seen=row.last_seen,
                edits=[
                    (edit.before_data, edit.after_data)
                    for edit in session.scalars(select(ListingEdit).order_by(ListingEdit.id))
                ],
                feedback=[
                    (feedback.action, feedback.note)
                    for feedback in session.scalars(select(Feedback).order_by(Feedback.id))
                ],
            )
        )


@pytest.mark.parametrize("currency,label", [("XXX", "UNKNOWN (XXX)"), ("CAD", "CAD")])
def test_acquisition_editor_labels_the_recorded_currency(client, item, currency, label):
    listing_id = create_listing(client, item, currency)
    response = client.get(f"/listings/{listing_id}/edit")
    assert response.status_code == 200
    for key in [*ACQUISITION, "invoice_fee_allocation"]:
        match = re.search(r'<label>([^<]+)<input name="' + key + r'"', response.text)
        assert match and f"· {label}" in match[1]
        assert "USD" not in match[1]
    assert 'name="currency"' in response.text
    assert 'name="confirm_currency_change"' in response.text
    assert "save" in response.text.lower() and "then" in response.text.lower()
    assert "new quotes" in response.text.lower()
    assert "convert" in response.text.lower() or "conversion" in response.text.lower()


@pytest.mark.parametrize("confirmation", [None, "no", "true"])
def test_unconfirmed_currency_change_rolls_back_other_edits(client, item, confirmation):
    listing_id = create_listing(client, item)
    before = snapshot(client, listing_id)
    form = {"currency": "USD", "condition": "SYNTHETIC must not be saved", "item_price": "99"}
    if confirmation is not None:
        form["confirm_currency_change"] = confirmation
    response = client.post(f"/listings/{listing_id}/edit", data=form, follow_redirects=False)
    assert response.status_code == 422
    assert snapshot(client, listing_id) == before


@pytest.mark.parametrize(
    "old_currency,new_currency", [("XXX", "USD"), ("CAD", "USD"), ("USD", "XXX")]
)
def test_confirmed_currency_change_clears_old_costs_and_preserves_audit(
    client, item, old_currency, new_currency
):
    listing_id = create_listing(client, item, old_currency)
    before = snapshot(client, listing_id)
    form = {
        "currency": new_currency,
        "confirm_currency_change": "yes",
        "buyer_profile": "synthetic_replacement_profile",
        "invoice_fee_allocation": "7.50",
        "invoice_allocation_confirmed": "true",
        "invoice_allocation_evidence": "SYNTHETIC simultaneous allocation",
    }
    form.update({key: "88.00" for key in ACQUISITION})
    form.update({key + "_status": "CONFIRMED" for key in ACQUISITION})
    response = client.post(f"/listings/{listing_id}/edit", data=form, follow_redirects=False)
    assert response.status_code == 303
    after = snapshot(client, listing_id)
    assert after["data"]["currency"] == new_currency
    assert all(after["data"][key] is None for key in ACQUISITION)
    assert all(after["data"]["cost_status"][key] == "UNKNOWN" for key in ACQUISITION)
    for key in ("buyer_profile", "invoice_fee_allocation", "invoice_allocation_evidence"):
        assert after["data"][key] is None
    assert after["data"]["invoice_allocation_confirmed"] is False
    for key in ("original", "dedup_key", "state", "feedback"):
        assert after[key] == before[key]
    for key in (
        "resale_low",
        "resale_high",
        "resale_basis",
        "resale_confirmed",
        "shipping_status",
        "shipping_evidence",
    ):
        assert after["data"][key] == before["data"][key]
    assert after["edits"] == [(before["data"], after["data"])]


@pytest.mark.parametrize("echo_currency", [False, True])
def test_unchanged_currency_retains_normal_quote_edit_behavior(client, item, echo_currency):
    listing_id = create_listing(client, item, "CAD")
    before = snapshot(client, listing_id)
    form = {"shipping_cost": "8.25", "shipping_cost_status": "ESTIMATED"}
    if echo_currency:
        form["currency"] = "CAD"
    response = client.post(f"/listings/{listing_id}/edit", data=form, follow_redirects=False)
    assert response.status_code == 303
    after = snapshot(client, listing_id)
    assert after["data"]["currency"] == "CAD"
    assert after["data"]["shipping_cost"] == "8.25"
    assert after["data"]["cost_status"]["shipping_cost"] == "ESTIMATED"
    for key in [field for field in ACQUISITION if field != "shipping_cost"]:
        assert after["data"][key] == before["data"][key]
    assert after["data"]["buyer_profile"] == before["data"]["buyer_profile"]
    assert after["data"]["invoice_fee_allocation"] == before["data"]["invoice_fee_allocation"]
    assert after["original"] == before["original"]


def test_new_quotes_require_a_subsequent_edit_after_currency_confirmation(client, item):
    listing_id = create_listing(client, item)
    assert (
        client.post(
            f"/listings/{listing_id}/edit",
            data={"currency": "USD", "confirm_currency_change": "yes"},
            follow_redirects=False,
        ).status_code
        == 303
    )
    cleared = snapshot(client, listing_id)
    response = client.post(
        f"/listings/{listing_id}/edit",
        data={"currency": "USD", "item_price": "24.75", "item_price_status": "CONFIRMED"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    saved = snapshot(client, listing_id)
    assert saved["data"]["item_price"] == "24.75"
    assert saved["data"]["cost_status"]["item_price"] == "CONFIRMED"
    assert saved["data"]["shipping_cost"] is None
    assert saved["original"] == cleared["original"]
    assert saved["edits"][-1] == (cleared["data"], saved["data"])


@pytest.mark.parametrize("currency", ["", "usd", "US", "USDD", "12$", "USD<script>"])
def test_invalid_currency_is_rejected_atomically_even_with_confirmation(client, item, currency):
    listing_id = create_listing(client, item)
    before = snapshot(client, listing_id)
    response = client.post(
        f"/listings/{listing_id}/edit",
        data={
            "currency": currency,
            "confirm_currency_change": "yes",
            "shipping_cost": "1.00",
            "condition": "SYNTHETIC must not be saved",
        },
        follow_redirects=False,
    )
    assert response.status_code == 422
    assert snapshot(client, listing_id) == before
