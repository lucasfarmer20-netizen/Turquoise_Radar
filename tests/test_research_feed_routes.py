"""Synthetic owner-report scenarios; no private listing evidence or network calls."""

from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import select

from radar.db import Feedback, Listing, Setting
from radar.schemas import ListingInput, ResearchObservation

NOW = datetime(2026, 11, 3, 18, tzinfo=UTC)


def research_item(key, *, source="ShopGoodwill", **evidence):
    domain = "shopgoodwill.com/item" if source == "ShopGoodwill" else "hibid.com/lot"
    url = f"https://{domain}/{key}"
    observation = ResearchObservation(
        observation_id=f"synthetic-{key}",
        source=source,
        source_listing_id=key,
        source_url=url,
        recorded_at=NOW,
        observed_date=date(2026, 11, 3),
        **evidence,
    )
    return ListingInput(
        source=source,
        source_listing_id=key,
        listing_url=url,
        raw_title=f"SYNTHETIC research {key}",
        synthetic=True,
        currency="XXX",
        buying_options=["AUCTION"],
        research_observations=[observation],
    )


def save(session, item):
    row = Listing(
        dedup_key=f"synthetic:{item.source_listing_id}",
        data=item.model_dump(mode="json"),
        original_data={"synthetic_original": "preserve this source evidence"},
        review_state="RESEARCH",
        last_seen=NOW,
    )
    session.add(row)
    session.flush()
    return row.id


def test_realized_result_is_archived_without_owner_pass_and_read_views_preserve_rows(client):
    item = research_item(
        "900000001",
        source="HiBid",
        currency="USD",
        availability="ENDED",
        realized_hammer="60",
        realized_currency="USD",
        buyer_premium_rate="0.20",
        sales_tax_rate="0.05",
        priority="HISTORICAL",
        shipping_state="RESTRICTIONS_REVIEW",
        shipping_restrictions=["SYNTHETIC seller may require third-party shipping."],
    )
    with client.app.state.sessions.begin() as session:
        listing_id = save(session, item)
        before = session.get(Listing, listing_id).data.copy()
    active = client.get("/feed?dataset=fixture&scope=needs_photos")
    assert active.status_code == 200
    assert active.context["cards"] == []
    archive = client.get("/feed?dataset=fixture&scope=archive")
    assert len(archive.context["cards"]) == 1
    card = archive.context["cards"][0]
    assert card["state"] == "RESEARCH"
    assert card["observation_count"] == 0
    assert card["research_observation_count"] == 1
    assert card["compact"]["assessment"] == "NEEDS INFO"
    assert "Realized" in archive.text
    detail = client.get(f"/listings/{listing_id}")
    assert detail.status_code == 200
    assert "72.00 USD" in detail.text
    assert "Known historical subtotal" in detail.text
    assert "calculated from posted rate, not confirmed charged" in detail.text
    assert "Final delivered total UNKNOWN" in detail.text
    assert "Seller restrictions requiring review" in detail.text
    assert "Final fulfillment: UNCONFIRMED" in detail.text
    assert "Current acquisition quote: UNKNOWN · ended opportunity" in detail.text
    assert "Original alert & transaction history" in detail.text
    assert client.get("/feed/evaluation?dataset=fixture").status_code == 200
    with client.app.state.sessions() as session:
        assert session.get(Listing, listing_id).data == before
        assert session.get(Listing, listing_id).review_state == "RESEARCH"
        assert list(session.scalars(select(Feedback))) == []


def test_quoted_costs_currency_provenance_and_deadline_conversions(client):
    item = research_item(
        "900000002",
        availability="ACTIVE_AT_OBSERVATION",
        item_quote="12",
        quote_kind="CURRENT_BID",
        next_minimum_bid="13",
        shipping_quote="16",
        handling_quote="2",
        shipping_state="ZIP_QUOTE_OBSERVED",
        shipping_method="SYNTHETIC carrier",
        reported_end_at="2026-11-05T05:00:00Z",
        reported_end_zone="America/Los_Angeles",
        gross_weight_g="31",
        material_claims=["SYNTHETIC seller claims sterling"],
        reported_marks=["SYNTHETIC owner reports a mark on one component"],
        seller_test_claims=["SYNTHETIC seller reports a test"],
        priority="HIGH",
    )
    with client.app.state.sessions.begin() as session:
        listing_id = save(session, item)
    response = client.get("/feed?dataset=fixture&scope=needs_photos")
    assert response.status_code == 200
    assert response.context["cards"][0]["availability"] == "ACTIVE_AT_OBSERVATION"
    assert "currency unconfirmed" in response.text
    assert response.context["cards"][0]["compact"]["assessment"] == "NEEDS INFO"
    assert "Known quoted subtotal" not in response.text
    detail = client.get(f"/listings/{listing_id}")
    assert detail.status_code == 200
    for text in (
        "30.00 XXX",
        "31.00 XXX",
        "ISO currency unverified",
        "150.0% of observed item price",
        "60.0% of known quoted subtotal",
        "ZIP-specific quote observed",
        "Final fulfillment: UNCONFIRMED",
        "exact capture time and timezone UNKNOWN",
        "files have not been received",
        "2026-11-04 21:00:00 PST",
        "2026-11-04 20:00:00 AKST",
        "2026-11-05 05:00:00 UTC",
        "current availability unverified",
        "Final delivered acquisition cost UNKNOWN",
    ):
        assert text in detail.text
    assert "$30.00" not in detail.text
    assert "Seller material claims" in detail.text
    assert "Owner-reported visible marks" in detail.text
    assert "Seller-reported testing" in detail.text
    assert "Eligible silver weight and independently verified purity: UNKNOWN" in detail.text


def test_research_priority_and_history_remain_separate_from_model_decisions(client):
    with client.app.state.sessions.begin() as session:
        for key, priority in (("900000003", "LOW"), ("900000004", "HIGH")):
            item = research_item(
                key,
                availability="ACTIVE_AT_OBSERVATION",
                priority=priority,
                item_quote="10",
                shipping_quote="20",
                handling_quote="1",
            )
            if priority == "HIGH":
                earlier = item.research_observations[0].model_copy(
                    update={
                        "observation_id": "synthetic-previous",
                        "item_quote": Decimal("8"),
                        "recorded_at": datetime(2026, 11, 3, 17, tzinfo=UTC),
                    }
                )
                item = item.model_copy(
                    update={"research_observations": [earlier, *item.research_observations]}
                )
            listing_id = save(session, item)
    response = client.get("/feed?dataset=fixture&scope=needs_photos&sort=priority")
    assert response.status_code == 200
    assert [c["research"]["priority"] for c in response.context["cards"]] == ["HIGH", "LOW"]
    assert all(c["state"] == "RESEARCH" for c in response.context["cards"])
    detail = client.get(f"/listings/{listing_id}")
    assert detail.status_code == 200
    history = detail.context["research_history"]
    assert [r["latest"].recorded_at.hour for r in history] == [18, 17]
    assert "2 separate research updates" in detail.text


def test_existing_evidence_editor_preserves_research_reports_and_source_history(client):
    item = research_item("900000005", availability="ACTIVE_AT_OBSERVATION", item_quote="11")
    with client.app.state.sessions.begin() as session:
        listing_id = save(session, item)
        row = session.get(Listing, listing_id)
        row.first_seen = datetime(2026, 11, 2, 12, tzinfo=UTC)
        original = row.original_data.copy()
        reports = row.data["research_observations"]
        session.add(Feedback(listing_id=listing_id, action="Interested", note="SYNTHETIC owner"))
    with client.app.state.sessions() as session:
        first_seen = session.get(Listing, listing_id).first_seen
    response = client.post(
        f"/listings/{listing_id}/edit",
        data={"condition": "SYNTHETIC condition research", "research_observations": "[]"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    with client.app.state.sessions() as session:
        row = session.get(Listing, listing_id)
        assert row.data["condition"] == "SYNTHETIC condition research"
        assert row.data["research_observations"] == reports
        assert row.original_data == original
        assert row.first_seen == first_seen
        feedback = list(session.scalars(select(Feedback)))
        assert [(f.action, f.note) for f in feedback] == [("Interested", "SYNTHETIC owner")]


def test_ended_strong_financial_model_shows_historical_badge_without_changing_analysis(
    client, item, prefs
):
    report_item = research_item("900000006", source="HiBid", availability="ENDED")
    ended = item.model_copy(
        update={
            "source": report_item.source,
            "source_listing_id": report_item.source_listing_id,
            "listing_url": report_item.listing_url,
            "raw_title": report_item.raw_title,
            "synthetic": True,
            "research_observations": report_item.research_observations,
            "resale_low": Decimal("200"),
            "resale_high": Decimal("220"),
        }
    )
    with client.app.state.sessions.begin() as session:
        listing_id = save(session, ended)
        session.add(Setting(id=1, data=prefs.model_dump(mode="json")))
    archive = client.get("/feed?dataset=fixture&scope=archive")
    assert archive.status_code == 200
    card = archive.context["cards"][0]
    assert card["a"].tier == "BUY CANDIDATE"
    assert card["state"] == "RESEARCH"
    assert card["compact"]["assessment"] == "NEEDS INFO"
    assert "<strong>BUY CANDIDATE</strong>" not in archive.text
    filtered = client.get("/feed?dataset=fixture&scope=archive&state=RESEARCH")
    assert len(filtered.context["cards"]) == 1
    detail = client.get(f"/listings/{listing_id}")
    assert detail.status_code == 200
    assert detail.context["v"]["a"].tier == "BUY CANDIDATE"
    assert '<span class="badge review">HISTORICAL RESEARCH' in detail.text
    assert "Review state: RESEARCH" in detail.text
    dashboard = client.get("/")
    assert dashboard.status_code == 200
    assert dashboard.context["candidate_count"] == 0
    for page in (dashboard, client.get("/queue?state=RESEARCH")):
        assert page.status_code == 200
        assert '<span class="badge review">HISTORICAL RESEARCH</span>' in page.text
        assert '<span class="badge buy">BUY CANDIDATE</span>' not in page.text
        assert "Current acquisition quote: UNKNOWN" in page.text
    with client.app.state.sessions() as session:
        assert session.get(Listing, listing_id).review_state == "RESEARCH"
