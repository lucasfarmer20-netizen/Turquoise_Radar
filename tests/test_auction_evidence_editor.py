"""Synthetic editor regressions; no real lot or notification validation implied."""

from copy import deepcopy
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from radar.analysis import analyze
from radar.db import AlertMessage, AlertObservation, Feedback, Listing, ListingEdit, MakerCase
from radar.intake.parsers import parse_message
from radar.intake.registry import SourceDefinition
from radar.intake.service import import_alerts, promote_candidate
from radar.schemas import ListingInput


@pytest.mark.parametrize(
    "suffix", ["", " To ensure delivery, add this sender to your address book."]
)
def test_feature_announcement_is_promotional_not_search_or_outbid_evidence(suffix):
    raw = b"""Subject: SYNTHETIC notification feature announcement
X-Turquoise-Synthetic: true

Daily saved search digests and text alerts for outbids.
Saved search: turqoise lot
https://hibid.com/lot/990008009
This is a promotional message.""" + suffix.encode()
    result = parse_message(raw, "hibid", SourceDefinition(name="HiBid", domains=["hibid.com"]))
    assert result.message_type == "PROMOTIONAL_NEWSLETTER"
    assert result.data["query_explicit"] is None
    assert all(c.query is None and not c.query_supported for c in result.candidates)


@pytest.fixture
def imported_listing(client):
    raw = b"""Subject: SYNTHETIC search match
X-Turquoise-Synthetic: true

Saved search: turqoise lot
Title: SYNTHETIC turqoise lot
Current bid: $15.00
Shipping: $12.00
Shipping indicator: SYNTHETIC original quote; Alaska unconfirmed
Auction closes: 2026-11-04T18:00:00-06:00
https://hibid.com/catalog/990008000
https://hibid.com/lot/990008001
"""
    with client.app.state.sessions.begin() as s:
        import_alerts(s, "hibid", [raw])
        listing = promote_candidate(s, 1)
        s.add(Feedback(listing_id=listing.id, action="Interested", note="SYNTHETIC owner note"))
        s.add(MakerCase(listing_id=listing.id, data={"title": "SYNTHETIC linked research"}))
        return listing.id


def snapshot(client, id_):
    with client.app.state.sessions() as s:
        row = s.get(Listing, id_)
        return deepcopy(
            {
                "data": row.data,
                "original": row.original_data,
                "seen": row.last_seen,
                "state": row.review_state,
                "audits": [(a.before_data, a.after_data) for a in s.scalars(select(ListingEdit))],
                "messages": [(m.raw_b64, m.data) for m in s.scalars(select(AlertMessage))],
                "observations": [
                    (o.data, o.query_text, o.attribution_verified)
                    for o in s.scalars(select(AlertObservation))
                ],
                "decisions": [(f.action, f.note) for f in s.scalars(select(Feedback))],
                "cases": [(c.listing_id, c.data) for c in s.scalars(select(MakerCase))],
            }
        )


@pytest.mark.parametrize(
    "deadline",
    [
        "2026-11-04T18:15:30-06:00",
        "2026-11-05T00:15:30Z",
        "2026-11-05T05:45:30+05:30",
    ],
)
def test_offset_deadline_roundtrip_shipping_and_original_history(
    client, imported_listing, deadline
):
    id_ = imported_listing
    before = snapshot(client, id_)
    evidence = "SYNTHETIC seller email, 2026-11-01\nQuote $12; 99669 not confirmed."
    response = client.post(
        f"/listings/{id_}/edit",
        data={
            "item_end_time": deadline,
            "shipping_evidence": evidence,
            "carrier": "SYNTHETIC USPS",
        },
    )
    assert response.status_code == 200
    after = snapshot(client, id_)
    saved = datetime.fromisoformat(after["data"]["item_end_time"])
    assert saved.astimezone(UTC) == datetime(2026, 11, 5, 0, 15, 30, tzinfo=UTC)
    assert saved.isoformat() in response.text
    assert after["data"]["shipping_evidence"] == evidence
    assert after["data"]["carrier"] == "SYNTHETIC USPS"
    assert after["data"]["shipping_cost"] == before["data"]["shipping_cost"]
    assert after["data"]["cost_status"] == before["data"]["cost_status"]
    assert after["data"]["shipping_status"] == "UNKNOWN"
    for key in ("original", "messages", "observations", "decisions", "cases"):
        assert after[key] == before[key]
    assert after["audits"] == [(before["data"], after["data"])]
    editor = client.get(f"/listings/{id_}/edit").text
    assert f'value="{saved.isoformat()}"' in editor
    assert evidence in editor and "SYNTHETIC USPS" in editor
    assert "Carrier / shipping evidence" not in editor
    assert '<label>Carrier<input name="carrier"' in editor
    assert "SYNTHETIC linked research" in response.text
    assert "/identify/cases/1" in response.text
    assert f"/identify?listing={id_}" in response.text


@pytest.mark.parametrize(
    "deadline",
    [
        "2026-11-04T18:00:00",
        "2026-11-04",
        "November 4 at 6 PM CST",
        "tomorrow",
        "1793836800",
        "2026-13-04T18:00:00Z",
        "2026-11-04T18:00:00+25:00",
    ],
)
def test_invalid_or_new_timezone_free_deadline_rolls_back_entire_edit(
    client, imported_listing, deadline
):
    id_ = imported_listing
    before = snapshot(client, id_)
    response = client.post(
        f"/listings/{id_}/edit",
        data={
            "item_end_time": deadline,
            "carrier": "must not save",
            "shipping_evidence": "must not save",
            "shipping_cost": "1.00",
            "shipping_status": "CONFIRMED_TO_ALASKA",
        },
        follow_redirects=False,
    )
    assert response.status_code == 422
    assert snapshot(client, id_) == before


def test_omission_preserves_and_explicit_empty_clears_existing_fields(client, imported_listing):
    id_ = imported_listing
    assert (
        client.post(
            f"/listings/{id_}/edit",
            data={
                "item_end_time": "2026-11-04T18:15:30-06:00",
                "shipping_evidence": "SYNTHETIC quote",
                "carrier": "SYNTHETIC carrier",
            },
        ).status_code
        == 200
    )
    initial = snapshot(client, id_)["data"]
    assert (
        client.post(f"/listings/{id_}/edit", data={"condition": "SYNTHETIC worn"}).status_code
        == 200
    )
    after = snapshot(client, id_)["data"]
    for key in ("item_end_time", "shipping_evidence", "carrier"):
        assert after[key] == initial[key]
    assert (
        client.post(
            f"/listings/{id_}/edit",
            data={
                "item_end_time": "",
                "shipping_evidence": "",
                "carrier": "",
            },
        ).status_code
        == 200
    )
    after = snapshot(client, id_)["data"]
    assert all(after[k] is None for k in ("item_end_time", "shipping_evidence", "carrier"))
    page = client.get(f"/listings/{id_}").text
    assert "Individual lot closes: UNKNOWN" in page and "Carrier: UNKNOWN" in page


@pytest.mark.parametrize("echo", [False, True])
def test_untouched_legacy_naive_deadline_remains_editable(client, imported_listing, echo):
    id_ = imported_listing
    old = "2025-01-02T03:04:05"
    with client.app.state.sessions.begin() as s:
        row = s.get(Listing, id_)
        row.data = {**row.data, "item_end_time": old}
    form = {"shipping_evidence": "SYNTHETIC unrelated edit"}
    if echo:
        form["item_end_time"] = old
    response = client.post(f"/listings/{id_}/edit", data=form)
    assert response.status_code == 200 and "Timezone unrecorded" in response.text
    assert snapshot(client, id_)["data"]["item_end_time"] == old
    before = snapshot(client, id_)
    assert (
        client.post(
            f"/listings/{id_}/edit", data={"item_end_time": "2025-01-02T03:05:05"}
        ).status_code
        == 422
    )
    assert snapshot(client, id_) == before


@pytest.mark.parametrize("destination", ["UNKNOWN", "CONFIRMED_UNAVAILABLE", "CONFIRMED_TO_ALASKA"])
def test_evidence_text_does_not_change_amount_destination_or_financial_gates(
    client, item, prefs, destination
):
    data = item.model_dump(mode="json")
    data.update(
        synthetic=True,
        raw_title="SYNTHETIC cost case",
        shipping_status=destination,
        resale_low="100",
        resale_high="120",
    )
    assert (
        client.post(
            "/listings", data={"payload": ListingInput.model_validate(data).model_dump_json()}
        ).status_code
        == 200
    )
    before = snapshot(client, 1)
    first = analyze(ListingInput.model_validate(before["data"]), prefs)
    assert (
        client.post(
            "/listings/1/edit",
            data={
                "shipping_evidence": "SYNTHETIC <script>not active</script> Alaska available",
            },
        ).status_code
        == 200
    )
    after = snapshot(client, 1)
    second = analyze(ListingInput.model_validate(after["data"]), prefs)
    assert first == second
    assert after["data"]["shipping_status"] == destination
    assert after["data"]["shipping_cost"] == before["data"]["shipping_cost"]
    assert after["data"]["cost_status"] == before["data"]["cost_status"]
    if destination == "UNKNOWN":
        assert second.tier != "BUY CANDIDATE"
    elif destination == "CONFIRMED_UNAVAILABLE":
        assert second.tier == "PASS"
    else:
        assert second.tier == "BUY CANDIDATE"
    page = client.get("/listings/1").text
    assert "&lt;script&gt;not active&lt;/script&gt;" in page
    assert "<script>not active</script>" not in page
