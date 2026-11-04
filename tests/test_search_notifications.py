"""Synthetic observed-format regressions; no real messages or source requests."""

from copy import deepcopy
from decimal import Decimal
from email.message import EmailMessage
from urllib.parse import urlencode

import pytest
from sqlalchemy import select

from radar.db import AlertMessage, AlertObservation, DiscoveredItem, Feedback, IntakeSource, Listing
from radar.intake.parsers import parse_message
from radar.intake.registry import SourceDefinition
from radar.intake.service import import_alerts, observation_occurrences, promote_candidate

SHOP_FORMAT = "shopgoodwill-personal-shopper-v1"
HIBID_FORMAT = "hibid-saved-search-v1"
SEARCH_DIVIDER = "*" * 34


def definition(source, formats=(), *, sample_kind="OWNER_PROVIDED"):
    return SourceDefinition(
        name={"shopgoodwill": "ShopGoodwill", "hibid": "HiBid"}[source],
        domains=[source + ".com"],
        sample_kind=sample_kind,
        sample_verified=True,
        sample_reference="SYNTHETIC prior format review simulation",
        reviewed_email_formats={key: "SYNTHETIC review evidence" for key in formats},
    )


def message(subject, body, *, synthetic=True, date="Tue, 03 Nov 2026 12:00:00 +0000"):
    mail = EmailMessage()
    mail["Subject"] = "SYNTHETIC " + subject
    mail["Date"] = date
    if synthetic:
        mail["X-Turquoise-Synthetic"] = "true"
    mail.set_content(body)
    return mail.as_bytes()


def shop_card(identifier, title, price="$12.34", *, url_id=None, extra=""):
    return (
        f"Item #: {identifier}\nTitle: {title}\nPrice: {price}\nBids: 2\n"
        "Started: 11/2/2026 3:00:00 AM\n"
        f"URL: https://shopgoodwill.com/item/{url_id or identifier}\n{extra}"
    )


def shop_mail(cards, query="blue stone ring", *, synthetic=True, extra="", **kwargs):
    return message(
        "shopgoodwill.com - Your Personal Shopper",
        "Your Personal Shopper has found items that match your search.\n"
        f"Search Keyword(s): {query}\n"
        "Personal Shopper Ends: 11/20/2026 12:00:00 AM\n"
        f"{extra}\n\n" + "\n\n".join(cards),
        synthetic=synthetic,
        **kwargs,
    )


def hibid_card(identifier, title):
    url = f"https://hibid.com/lot/{identifier}/synthetic-title"
    return f"{title}\n ({url})\n11/12/2026 10:00 AM\n\n{title}\n ({url})\nBid Now\n ({url})"


def hibid_mail(
    cards,
    query="blue stone ring",
    *,
    heading=None,
    subject_query=None,
    search_url=None,
    synthetic=True,
    tail="",
    **kwargs,
):
    heading = heading if heading is not None else query.title()
    subject_query = subject_query if subject_query is not None else query.title()
    if search_url is None:
        search_url = "https://hibid.com/lots?" + urlencode({"q": query})
    return message(
        f"New lots for your saved {subject_query} search - SYNTHETIC daily email",
        f"Your saved search has new lots\n{SEARCH_DIVIDER}\n{heading}\n{SEARCH_DIVIDER}\n"
        f"View Saved Search\n ({search_url})\n\n" + "\n\n".join(cards) + tail,
        synthetic=synthetic,
        **kwargs,
    )


def by_id(parsed):
    return {candidate.item.source_listing_id: candidate for candidate in parsed.candidates}


def test_shop_cards_keep_titles_quotes_and_exact_query_separate():
    raw = shop_mail(
        [
            shop_card("990016001", "SYNTHETIC first ring", "$12.34"),
            shop_card("990016002", "SYNTHETIC second ring", "USD $45.67"),
        ],
        query="  blue stone ring  ",
    )
    parsed = parse_message(raw, "shopgoodwill", definition("shopgoodwill", [SHOP_FORMAT]))
    assert parsed.message_type == "SEARCH_MATCH"
    cards = by_id(parsed)
    assert set(cards) == {"990016001", "990016002"}
    assert cards["990016001"].item.raw_title == "SYNTHETIC first ring"
    assert cards["990016001"].item.item_price == Decimal("12.34")
    assert cards["990016001"].item.currency == "XXX"
    assert cards["990016002"].item.raw_title == "SYNTHETIC second ring"
    assert cards["990016002"].item.item_price == Decimal("45.67")
    assert cards["990016002"].item.currency == "USD"
    assert all(c.query == "  blue stone ring  " and c.query_supported for c in cards.values())
    assert all(c.evidence["format_id"] == SHOP_FORMAT for c in cards.values())
    assert all(c.evidence["availability"] == "UNKNOWN" for c in cards.values())


def test_shop_alert_expiry_and_started_do_not_become_lot_deadline():
    parsed = parse_message(
        shop_mail([shop_card("990016003", "SYNTHETIC timing evidence")]),
        "shopgoodwill",
        definition("shopgoodwill", [SHOP_FORMAT]),
    )
    candidate = parsed.candidates[0]
    assert candidate.item.item_end_time is None
    assert candidate.item.shipping_status == "UNKNOWN"
    assert candidate.item.shipping_cost is None
    assert candidate.item.buyer_premium is None
    assert candidate.item.buyer_service_fee is None
    assert candidate.item.handling_fee is None
    assert candidate.item.sales_tax is None
    assert candidate.item.image_urls == []
    assert parsed.sent_at.isoformat() == "2026-11-03T12:00:00+00:00"
    assert parsed.received_at is None


@pytest.mark.parametrize("price", ["$12.34junk", "$1,23.00", "$12.34 - $99.00"])
def test_shop_malformed_or_range_quotes_remain_unknown(price):
    parsed = parse_message(
        shop_mail([shop_card("990016004", "SYNTHETIC malformed quote", price)]),
        "shopgoodwill",
        definition("shopgoodwill", [SHOP_FORMAT]),
    )
    assert parsed.candidates[0].item.item_price is None


def test_shop_invalid_neighbor_identity_never_lends_its_fields_to_valid_card():
    raw = shop_mail(
        [
            shop_card("990016005", "SYNTHETIC valid neighbor", "$15.00"),
            shop_card("990016006", "SYNTHETIC mismatched neighbor", "$999.00", url_id="990016007"),
        ]
    )
    cards = by_id(parse_message(raw, "shopgoodwill", definition("shopgoodwill", [SHOP_FORMAT])))
    assert cards["990016005"].item.raw_title == "SYNTHETIC valid neighbor"
    assert cards["990016005"].item.item_price == Decimal("15.00")
    assert "990016006" not in cards and "990016007" not in cards


def test_conflicting_shop_query_headers_receive_no_query_credit():
    parsed = parse_message(
        shop_mail(
            [shop_card("990016008", "SYNTHETIC ambiguous search")],
            extra="Search Keyword(s): different synthetic phrase",
        ),
        "shopgoodwill",
        definition("shopgoodwill", [SHOP_FORMAT]),
    )
    assert parsed.candidates
    assert all(c.query is None and not c.query_supported for c in parsed.candidates)


@pytest.mark.parametrize(
    "heading", ["Recommended for you", "Featured Auctions", "Sponsored Auctions"]
)
def test_shop_recommendation_section_does_not_inherit_saved_search_query(heading):
    raw = shop_mail(
        [
            shop_card("990016018", "SYNTHETIC matched shop ring"),
            heading + "\n\n" + shop_card("990016019", "SYNTHETIC recommended shop sculpture"),
        ]
    )
    cards = by_id(parse_message(raw, "shopgoodwill", definition("shopgoodwill", [SHOP_FORMAT])))
    assert cards["990016018"].query == "blue stone ring"
    assert cards["990016018"].query_supported
    assert cards["990016019"].query is None
    assert not cards["990016019"].query_supported


@pytest.mark.parametrize(
    "source, format_id", [("shopgoodwill", SHOP_FORMAT), ("hibid", HIBID_FORMAT)]
)
def test_global_review_or_another_format_cannot_enable_exact_query_credit(source, format_id):
    raw = (
        shop_mail([shop_card("990016009", "SYNTHETIC review gate")])
        if source == "shopgoodwill"
        else hibid_mail([hibid_card("990016009", "SYNTHETIC review gate")])
    )
    for formats in ((), ("unrelated-template-v1",)):
        parsed = parse_message(raw, source, definition(source, formats))
        assert parsed.candidates
        assert all(
            c.query == "blue stone ring" and not c.query_supported for c in parsed.candidates
        )
        assert all(not c.evidence["format_reviewed"] for c in parsed.candidates)
    synthetic_review = parse_message(
        raw, source, definition(source, [format_id], sample_kind="SYNTHETIC")
    )
    assert all(not c.query_supported for c in synthetic_review.candidates)
    reviewed = parse_message(raw, source, definition(source, [format_id]))
    assert all(c.query_supported for c in reviewed.candidates)


@pytest.mark.parametrize(
    "source, format_id, subject",
    [
        ("shopgoodwill", SHOP_FORMAT, "shopgoodwill.com - Your Personal Shopper"),
        ("hibid", HIBID_FORMAT, "New lots for your saved Blue Stone Ring search"),
    ],
)
def test_recognized_subject_with_missing_body_structure_cannot_fall_back_to_global_credit(
    client, source, format_id, subject
):
    card = (
        shop_card("990016020", "SYNTHETIC missing structure")
        if source == "shopgoodwill"
        else hibid_card("990016020", "SYNTHETIC missing structure")
    )
    # This fake unflagged message exercises the non-synthetic attribution branch.
    raw = message(subject, "Saved search: blue stone ring\n\n" + card, synthetic=False)
    reviewed = definition(source, [format_id])
    parsed = parse_message(raw, source, reviewed)
    assert parsed.data["format_id"] == format_id
    assert not parsed.data["format_reviewed"]
    assert parsed.candidates
    assert all(c.query is None and not c.query_supported for c in parsed.candidates)
    with client.app.state.sessions.begin() as session:
        session.get(IntakeSource, source).data = reviewed.model_dump(mode="json")
        import_alerts(session, source, [raw])
        observations = list(session.scalars(select(AlertObservation)))
        assert observations
        assert all(not o.attribution_verified and o.query_text is None for o in observations)


@pytest.mark.parametrize(
    "source, format_id, subject, path",
    [
        ("shopgoodwill", SHOP_FORMAT, "shopgoodwill.com - Your Personal Shopper", "item"),
        ("hibid", HIBID_FORMAT, "New lots for your saved Blue Stone Ring search", "lot"),
    ],
)
def test_html_only_recognized_subject_stays_unverified(source, format_id, subject, path):
    mail = EmailMessage()
    mail["Subject"] = "SYNTHETIC " + subject
    mail["X-Turquoise-Synthetic"] = "true"
    mail.set_content(
        "<p>Saved search: blue stone ring</p><p>Title: SYNTHETIC HTML-only item</p>"
        f'<a href="https://{source}.com/{path}/990016021">SYNTHETIC item</a>',
        subtype="html",
    )
    parsed = parse_message(mail.as_bytes(), source, definition(source, [format_id]))
    assert parsed.data["format_id"] == format_id
    assert not parsed.data["format_reviewed"]
    assert not parsed.candidates


def test_hibid_exact_url_query_survives_title_case_heading_and_duplicate_item_links():
    raw = hibid_mail(
        [
            hibid_card("990016010", "SYNTHETIC blue stone cuff"),
            hibid_card("990016011", "SYNTHETIC blue stone ring"),
        ],
        query="blue  stone ring",
        heading="Blue Stone Ring",
        subject_query="Blue Stone Ring",
    )
    parsed = parse_message(raw, "hibid", definition("hibid", [HIBID_FORMAT]))
    cards = by_id(parsed)
    assert set(cards) == {"990016010", "990016011"}
    assert len(parsed.candidates) == 2
    assert all(c.query == "blue  stone ring" and c.query_supported for c in cards.values())
    assert cards["990016010"].item.raw_title == "SYNTHETIC blue stone cuff"
    assert cards["990016011"].item.raw_title == "SYNTHETIC blue stone ring"
    for candidate in cards.values():
        assert candidate.item.item_price is None
        assert candidate.item.item_end_time is None
        assert candidate.item.shipping_status == "UNKNOWN"
        assert candidate.item.image_urls == []


def test_hibid_footer_query_url_credits_only_heading_scoped_cards_and_unwraps_link_titles():
    body = """SYNTHETIC preheader lot  (
https://hibid.com/lot/990016023/synthetic-preheader )

Your saved search has new lots
**********************************
Blue Stone Ring
**********************************
View Saved Search
 (https://clicks.alerts.hibid.com/ls/click?upn=SYNTHETIC_OPAQUE)

SYNTHETIC first digest ring  (
https://hibid.com/lot/990016024/synthetic-first )
11/12/2026 10:00 AM

SYNTHETIC second digest cuff  (
https://hibid.com/lot/990016025/synthetic-second )
11/13/2026 10:00 AM

Recommended for you
SYNTHETIC recommended sculpture  (
https://hibid.com/lot/990016026/synthetic-recommendation )

Manage saved search
 (https://hibid.com/lots?q=blue+stone+ring)
"""
    raw = message("New lots for your saved Blue Stone Ring search - SYNTHETIC daily email", body)
    parsed = parse_message(raw, "hibid", definition("hibid", [HIBID_FORMAT]))
    cards = by_id(parsed)
    assert set(cards) == {"990016023", "990016024", "990016025", "990016026"}
    assert cards["990016024"].item.raw_title == "SYNTHETIC first digest ring"
    assert cards["990016025"].item.raw_title == "SYNTHETIC second digest cuff"
    for identifier in ("990016024", "990016025"):
        assert cards[identifier].query == "blue stone ring"
        assert cards[identifier].query_supported
    for identifier in ("990016023", "990016026"):
        assert cards[identifier].query is None
        assert not cards[identifier].query_supported


def test_same_hibid_lot_before_and_within_search_retains_distinct_query_observations(client):
    body = """SYNTHETIC preheader occurrence  (
https://hibid.com/lot/990016027/synthetic-title )

Your saved search has new lots
**********************************
Blue Stone Ring
**********************************
SYNTHETIC digest occurrence  (
https://hibid.com/lot/990016027/synthetic-title )

Manage saved search
 (https://hibid.com/lots?q=blue+stone+ring)
"""
    raw = message("New lots for your saved Blue Stone Ring search - SYNTHETIC daily email", body)
    reviewed = definition("hibid", [HIBID_FORMAT])
    parsed = parse_message(raw, "hibid", reviewed)
    assert len(parsed.candidates) == 2
    assert {c.item.source_listing_id for c in parsed.candidates} == {"990016027"}
    assert [c.item.raw_title for c in parsed.candidates] == [
        "SYNTHETIC preheader occurrence",
        "SYNTHETIC digest occurrence",
    ]
    assert [c.query for c in parsed.candidates] == [None, "blue stone ring"]
    assert [c.query_supported for c in parsed.candidates] == [False, True]
    with client.app.state.sessions.begin() as session:
        session.get(IntakeSource, "hibid").data = reviewed.model_dump(mode="json")
        import_alerts(session, "hibid", [raw])
        assert len(list(session.scalars(select(DiscoveredItem)))) == 1
        observations = list(session.scalars(select(AlertObservation)))
        assert len(observations) == 1
        occurrences = observation_occurrences(observations[0])
        assert [o.query_text for o in occurrences] == [None, "blue stone ring"]
        assert [o.attribution_verified for o in occurrences] == [False, True]
        assert [o.data["message_role"] for o in occurrences] == ["UNVERIFIED", "SEARCH_SECTION"]


def test_hibid_card_title_matching_query_cannot_replace_missing_delimited_search_heading():
    query = "SYNTHETIC blue stone ring"
    raw = message(
        f"New lots for your saved {query.title()} search - SYNTHETIC daily email",
        "Your saved search has new lots\n\n"
        + query
        + "  (\nhttps://hibid.com/lot/990016028/synthetic-title )\n\n"
        "Manage saved search\n (https://hibid.com/lots?" + urlencode({"q": query}) + ")\n",
    )
    parsed = parse_message(raw, "hibid", definition("hibid", [HIBID_FORMAT]))
    assert parsed.candidates
    assert all(c.query is None and not c.query_supported for c in parsed.candidates)


@pytest.mark.parametrize(
    "options",
    [
        {"heading": "Different Synthetic Query"},
        {"subject_query": "Different Synthetic Query"},
        {"search_url": "https://hibid.com/lots"},
        {"search_url": "https://hibid.com/lots?q=blue+stone+ring&q=different"},
    ],
)
def test_hibid_missing_or_conflicting_query_evidence_receives_no_credit(options):
    raw = hibid_mail([hibid_card("990016012", "SYNTHETIC uncertain query")], **options)
    parsed = parse_message(raw, "hibid", definition("hibid", [HIBID_FORMAT]))
    assert parsed.candidates
    assert all(not c.query_supported for c in parsed.candidates)


def test_hibid_recommendation_section_does_not_inherit_saved_search_query():
    raw = hibid_mail(
        [hibid_card("990016013", "SYNTHETIC matched ring")],
        tail="\n\nRecommended for you\n\n"
        + hibid_card("990016014", "SYNTHETIC recommended sculpture"),
    )
    parsed = parse_message(raw, "hibid", definition("hibid", [HIBID_FORMAT]))
    cards = by_id(parsed)
    assert cards["990016013"].query == "blue stone ring"
    assert cards["990016013"].query_supported
    if "990016014" in cards:
        assert cards["990016014"].query is None
        assert not cards["990016014"].query_supported


def test_promotional_disclaimer_cannot_gain_search_credit_from_digest_shape():
    raw = hibid_mail(
        [hibid_card("990016015", "SYNTHETIC promotional ring")],
        tail="\nThis is a promotional message.\n",
    )
    parsed = parse_message(raw, "hibid", definition("hibid", [HIBID_FORMAT]))
    assert parsed.message_type == "PROMOTIONAL_NEWSLETTER"
    assert all(c.query is None and not c.query_supported for c in parsed.candidates)


def test_format_review_changes_only_new_evidence_and_reimports_stay_deduplicated(client):
    # Omit the synthetic flag only to exercise the real-attribution branch on fake data.
    raw = shop_mail([shop_card("990016016", "SYNTHETIC dedup ring")], synthetic=False)
    with client.app.state.sessions.begin() as session:
        source = session.get(IntakeSource, "shopgoodwill")
        source.data = definition("shopgoodwill").model_dump(mode="json")
        first = import_alerts(session, "shopgoodwill", [raw])
        original = session.scalar(select(AlertObservation))
        assert first.stats["new_candidates"] == 1 and not original.attribution_verified
        original_evidence = deepcopy(original.data)
        source.data = definition("shopgoodwill", [SHOP_FORMAT]).model_dump(mode="json")
        repeated = import_alerts(session, "shopgoodwill", [raw])
        assert repeated.stats["duplicate_messages"] == 1
        assert repeated.stats["new_candidates"] == repeated.stats["observations"] == 0
        assert not original.attribution_verified and original.data == original_evidence
        newer = shop_mail(
            [shop_card("990016016", "SYNTHETIC dedup ring", "$18.00")],
            synthetic=False,
            date="Wed, 04 Nov 2026 12:00:00 +0000",
        )
        result = import_alerts(session, "shopgoodwill", [newer])
        assert result.stats["duplicate_candidates"] == 1
        assert result.stats["new_candidates"] == 0
        observations = list(session.scalars(select(AlertObservation).order_by(AlertObservation.id)))
        assert [o.attribution_verified for o in observations] == [False, True]
        assert len(list(session.scalars(select(DiscoveredItem)))) == 1
        assert len(list(session.scalars(select(AlertMessage)))) == 2


def test_new_bare_dollar_quote_refreshes_candidate_currency_without_changing_owner_history(client):
    first = shop_mail([shop_card("990016017", "SYNTHETIC owner linked ring", "USD $11.00")])
    next_message = shop_mail(
        [shop_card("990016017", "SYNTHETIC newly observed ring", "$19.25")],
        date="Wed, 04 Nov 2026 12:00:00 +0000",
    )
    with client.app.state.sessions.begin() as session:
        import_alerts(session, "shopgoodwill", [first])
        candidate = session.scalar(select(DiscoveredItem))
        owner = promote_candidate(session, candidate.id)
        owner.data = {**owner.data, "item_price": "80.00", "currency": "USD"}
        owner.review_state = "WATCH"
        feedback = Feedback(
            listing_id=owner.id, action="Interested", note="SYNTHETIC retained note"
        )
        session.add(feedback)
        session.flush()
        before = deepcopy((owner.data, owner.original_data, owner.last_seen, owner.review_state))
        observation = session.scalar(select(AlertObservation))
        prior_observation = deepcopy(observation.data)
        import_alerts(session, "shopgoodwill", [next_message])
        assert Decimal(candidate.normalized["item_price"]) == Decimal("19.25")
        assert candidate.normalized["currency"] == "XXX"
        assert (owner.data, owner.original_data, owner.last_seen, owner.review_state) == before
        assert observation.data == prior_observation
        assert [(f.action, f.note) for f in session.scalars(select(Feedback))] == [
            ("Interested", "SYNTHETIC retained note")
        ]
        assert session.get(Listing, candidate.listing_id) is owner


def test_currency_change_clears_old_candidate_costs_without_rewriting_owner_or_observations(client):
    legacy = message(
        "legacy source notification",
        "Currency: CAD\nTitle: SYNTHETIC legacy Canadian quote\nCurrent price: $20.00\n"
        "Shipping: $5.00\nhttps://shopgoodwill.com/item/990016022\n",
    )
    updated = shop_mail(
        [shop_card("990016022", "SYNTHETIC new USD quote", "USD $25.00")],
        date="Wed, 04 Nov 2026 12:00:00 +0000",
    )
    old_costs = {
        "buyer_premium": "2.00",
        "buyer_service_fee": "1.00",
        "sales_tax": "1.50",
        "shipping_cost": "5.00",
        "handling_fee": "0.50",
    }
    with client.app.state.sessions.begin() as session:
        import_alerts(session, "shopgoodwill", [legacy])
        candidate = session.scalar(select(DiscoveredItem))
        candidate.normalized = {
            **candidate.normalized,
            **old_costs,
            "buyer_profile": "SYNTHETIC CAD fee profile",
            "invoice_fee_allocation": "3.00",
            "invoice_allocation_confirmed": True,
            "invoice_allocation_evidence": "SYNTHETIC CAD invoice allocation",
            "cost_status": {
                **candidate.normalized["cost_status"],
                **{field: "CONFIRMED" for field in old_costs},
            },
        }
        assert candidate.normalized["currency"] == "CAD"
        assert Decimal(candidate.normalized["item_price"]) == Decimal("20.00")
        owner = promote_candidate(session, candidate.id)
        owner.review_state = "WATCH"
        session.add(
            Feedback(listing_id=owner.id, action="Interested", note="SYNTHETIC preserved feedback")
        )
        session.flush()
        owner_before = deepcopy(
            (owner.data, owner.original_data, owner.last_seen, owner.review_state)
        )
        original = session.scalar(select(AlertObservation))
        observation_before = deepcopy(
            (original.data, original.query_text, original.attribution_verified)
        )
        import_alerts(session, "shopgoodwill", [updated])
        assert candidate.normalized["currency"] == "USD"
        assert Decimal(candidate.normalized["item_price"]) == Decimal("25.00")
        for field in old_costs:
            assert candidate.normalized[field] is None
            assert candidate.normalized["cost_status"][field] == "UNKNOWN"
        assert candidate.normalized["buyer_profile"] is None
        assert candidate.normalized["invoice_fee_allocation"] is None
        assert candidate.normalized["invoice_allocation_confirmed"] is False
        assert candidate.normalized["invoice_allocation_evidence"] is None
        assert (
            owner.data,
            owner.original_data,
            owner.last_seen,
            owner.review_state,
        ) == owner_before
        assert (
            original.data,
            original.query_text,
            original.attribution_verified,
        ) == observation_before
        assert [(f.action, f.note) for f in session.scalars(select(Feedback))] == [
            ("Interested", "SYNTHETIC preserved feedback")
        ]
