"""Synthetic regressions for defects observed in private notifications.

These examples are not evidence of a real saved-search format or a live bargain.
"""

from copy import deepcopy
from email.message import EmailMessage

import pytest
from sqlalchemy import select

from radar.db import AlertMessage, DiscoveredItem
from radar.intake.parsers import parse_message
from radar.intake.registry import SourceDefinition
from radar.intake.service import import_alerts, promote_candidate
from radar.intake.views import auction_research, inbox_cards, search_status, targeted_research
from radar.main import preferences
from radar.schemas import Preferences


def email_html(body, subject="SYNTHETIC featured auction"):
    message = EmailMessage()
    message["Subject"] = subject
    message["X-Turquoise-Synthetic"] = "true"
    message.set_content(body, subtype="html")
    return message.as_bytes()


def parse(body, subject="SYNTHETIC featured auction"):
    return parse_message(
        email_html(body, subject), "hibid", SourceDefinition(name="HiBid", domains=["hibid.com"])
    )


def test_image_anchor_alt_is_local_text_not_image_analysis():
    result = parse("""<table><tr><td><a href="https://hibid.com/lot/990000001">
        <img src="https://tracking.invalid/private.png" alt="SYNTHETIC turqoise cuff">
        </a></td></tr><tr><td>Different card text: $999</td></tr></table>
        <a href="https://hibid.com/lot/990000002"><img alt="SYNTHETIC sterling lot"></a>
        <a href="https://hibid.com/catalog/990000000">View Auction</a>
        <p>BIDDING BEGINS CLOSING</p><p>November 1 at 3:30 PM ET</p>""")
    assert [c.item.raw_title for c in result.candidates] == [
        "SYNTHETIC turqoise cuff",
        "SYNTHETIC sterling lot",
    ]
    assert result.message_type == "PROMOTIONAL_NEWSLETTER"
    assert result.data["closing_time_raw"] == "November 1 at 3:30 PM ET"
    assert result.data["closing_at"] is None  # No invented year or UTC offset.
    for c in result.candidates:
        assert c.item.item_price is None and c.item.item_end_time is None
        assert c.item.sale_auction_id == "990000000"
        assert not c.query_supported and c.query is None
        assert "tracking.invalid" not in c.item.model_dump_json()


@pytest.mark.parametrize(
    "images",
    [
        '<img alt="Image">',
        "<img alt>",
        '<img alt="SYNTHETIC first"><img alt="SYNTHETIC second">',
        '<script><img alt="SYNTHETIC unsafe"></script>',
    ],
)
def test_ambiguous_generic_or_active_content_alt_does_not_become_title(images):
    result = parse(f'<a href="https://hibid.com/lot/990000001">{images}</a>')
    assert result.candidates[0].item.raw_title.startswith("TITLE UNAVAILABLE")


def test_visible_title_wins_and_html_in_alt_stays_text(client):
    data = email_html("""<a href="https://hibid.com/lot/990000001">
        <img alt="SYNTHETIC other">SYNTHETIC visible title</a>
        <a href="https://hibid.com/lot/990000002">
        <img alt="&lt;script&gt;bad&lt;/script&gt;"></a>""")
    with client.app.state.sessions.begin() as s:
        import_alerts(s, "hibid", [data])
    page = client.get("/inbox?focus=auction")
    assert page.status_code == 200
    assert "SYNTHETIC visible title" in page.text
    assert "&lt;script&gt;bad&lt;/script&gt;" in page.text
    assert "<script>bad</script>" not in page.text


@pytest.mark.parametrize(
    "heading", ["Top auctions to watch in Test State", "Auction invitation", "Featured Auction"]
)
def test_promotion_with_preheader_padding_does_not_gain_query_credit(heading):
    result = parse(
        f"<p>\n\n\u034f\u200c</p><p>{heading}</p><p>Saved search: turqoise</p>",
        subject="SYNTHETIC notification",
    )
    assert result.message_type == "PROMOTIONAL_NEWSLETTER"
    assert result.data["query_explicit"] is None


def test_conflicting_catalog_closing_quotes_stay_unknown():
    result = parse(
        "<p>BIDDING BEGINS CLOSING</p><p>Monday at noon</p>"
        "<p>BIDDING BEGINS CLOSING</p><p>Tuesday at noon</p>"
    )
    assert result.data["closing_time_raw"] is None


def test_auction_focus_preserves_unknown_costs_dedup_and_research_handoff(client):
    raw = email_html("""<a href="https://hibid.com/lot/990000001">
        <img alt="SYNTHETIC turqoise cuff"></a>
        <a href="https://hibid.com/catalog/990000000">View auction</a>""")
    with client.app.state.sessions.begin() as s:
        import_alerts(s, "hibid", [raw])
        again = import_alerts(s, "hibid", [raw])
        assert again.stats["duplicate_messages"] == 1
        c = inbox_cards(s, preferences(s))[0]
        assert "misspelling" in c["leads"]
        assert all(cost["value"] is None for cost in c["costs"])
        assert all(x["real_matches"] == 0 for x in search_status(s))
        row = promote_candidate(s, c["row"].id)
        listing_id, candidate_id = row.id, c["row"].id
        assert row.review_state == "RESEARCH"
        s.add(
            DiscoveredItem(
                identity="SYNTHETIC-mercari",
                source="Mercari",
                ebay_item_id="demo",
                normalized={"source": "Mercari", "raw_title": "SYNTHETIC supplemental ring"},
                original_data={},
            )
        )
    page = client.get("/inbox?focus=auction&opportunity=misspelling")
    assert page.status_code == 200
    assert "Auction research" in page.text and "SYNTHETIC turqoise cuff" in page.text
    assert "SYNTHETIC supplemental ring" not in page.text
    assert "Lot closes: UNKNOWN" in page.text and "Purchase-cost evidence" in page.text
    assert "Quoted buyer premium rate: UNKNOWN" in page.text
    assert "SYNTHETIC supplemental ring" in client.get("/inbox").text
    assert f"/identify?listing={listing_id}" in client.get(f"/inbox/candidates/{candidate_id}").text
    with client.app.state.sessions() as s:
        assert len(list(s.scalars(select(AlertMessage)))) == 1


def test_source_priority_and_quoted_cost_status_do_not_depend_on_ebay(client):
    with client.app.state.sessions.begin() as s:
        for index, (source, title) in enumerate(
            [
                ("HiBid", "SYNTHETIC sterling mixed lot"),
                ("HiBid", "SYNTHETIC bracelet"),
                ("CTBids", "SYNTHETIC turqoise cuff"),
                ("ShopGoodwill", "SYNTHETIC blue rock"),
                ("Mercari", "SYNTHETIC supplemental"),
            ]
        ):
            s.add(
                DiscoveredItem(
                    identity=f"SYNTHETIC-priority-{index}",
                    source=source,
                    ebay_item_id=f"SYNTHETIC-{index}",
                    original_data={},
                    normalized={
                        "source": source,
                        "raw_title": title,
                        "item_price": "12.00",
                        "handling_fee": "0.00",
                        "cost_status": {"item_price": "ESTIMATED", "handling_fee": "CONFIRMED"},
                    },
                )
            )
        s.flush()
        cards = auction_research(inbox_cards(s, preferences(s)))
        assert [c["item"].source for c in cards] == ["ShopGoodwill", "HiBid", "HiBid", "CTBids"]
        assert cards[1]["item"].raw_title == "SYNTHETIC sterling mixed lot"
        for card in cards:
            costs = {c["label"]: c for c in card["costs"]}
            assert costs["Item / bid"]["status"] == "ESTIMATED"
            assert costs["Handling"]["value"] == 0
            assert costs["Handling"]["status"] == "CONFIRMED"
            assert costs["Buyer premium"]["value"] is None
            assert costs["Buyer premium"]["status"] == "UNKNOWN"
            assert card["item"].shipping_status == "UNKNOWN"
    page = client.get("/inbox?focus=auction")
    assert page.status_code == 200
    assert "Handling · CONFIRMED" in page.text
    assert "Item / bid · ESTIMATED" in page.text


@pytest.mark.parametrize(
    "amount,destination",
    [
        ("12.00", "UNKNOWN"),
        ("12.00", "CONFIRMED_UNAVAILABLE"),
        (None, "UNKNOWN"),
        ("12.00", "CONFIRMED_TO_ALASKA"),
        (None, "CONFIRMED_TO_ALASKA"),
    ],
)
def test_shipping_quote_confirmation_is_separate_from_alaska(
    client, item, prefs, amount, destination
):
    data = item.model_dump(mode="json")
    data.update(
        source="HiBid FIXTURE",
        synthetic=True,
        raw_title="SYNTHETIC shipping ring",
        shipping_cost=amount,
        shipping_status=destination,
        resale_low="100",
        resale_high="120",
    )
    data["cost_status"]["shipping_cost"] = "CONFIRMED" if amount is not None else "UNKNOWN"
    original = deepcopy(data)
    with client.app.state.sessions.begin() as s:
        row = DiscoveredItem(
            identity="SYNTHETIC-shipping",
            source=data["source"],
            ebay_item_id="SYNTHETIC-shipping",
            normalized=data,
            original_data={},
        )
        s.add(row)
        s.flush()
        card = inbox_cards(s, prefs)[0]
        quote = next(c for c in card["costs"] if c["label"] == "Quoted shipping")
        assert quote["value"] == card["item"].shipping_cost
        assert quote["status"] == data["cost_status"]["shipping_cost"]
        assert card["item"].shipping_status == destination
        if destination == "CONFIRMED_UNAVAILABLE":
            assert card["a"].tier == "PASS"
        elif destination == "UNKNOWN" or amount is None:
            assert card["a"].tier != "BUY CANDIDATE"
        else:
            assert card["a"].tier == "BUY CANDIDATE"
        assert row.normalized == original
    page = client.get("/inbox?focus=auction")
    assert page.status_code == 200
    quote_html = f"<dt>Quoted shipping · {quote['status']}</dt><dd>"
    assert quote_html + ("$12.00" if amount else "UNKNOWN") + " USD</dd>" in page.text
    assert f'<p class="shipping">{destination} · Soldotna 99669</p>' in page.text
    assert "Shipping to Alaska ·" not in page.text


@pytest.mark.parametrize("title", ["turqoise lot", "sterling mixed lot", "blue stone lot"])
def test_specific_cue_without_jewelry_noun_precedes_bracelet(client, title):
    with client.app.state.sessions.begin() as s:
        for n, text in enumerate(["bracelet", title]):
            s.add(
                DiscoveredItem(
                    identity=f"SYNTHETIC-target-{n}",
                    source="HiBid FIXTURE",
                    ebay_item_id=str(n),
                    original_data={},
                    normalized={
                        "source": "HiBid FIXTURE",
                        "synthetic": True,
                        "raw_title": "SYNTHETIC " + text,
                    },
                )
            )
        s.flush()
        cards = inbox_cards(s, Preferences())
        before = deepcopy([c["row"].normalized for c in cards])
        ordered = auction_research(cards)
        assert ordered[0]["item"].raw_title == "SYNTHETIC " + title
        assert "jewelry" not in ordered[0]["leads"]
        assert targeted_research(ordered[0])
        assert [c["row"].normalized for c in cards] == before
        assert all(c["item"].material_evidence == "unknown" for c in ordered)
        assert all(c["a"].tier != "BUY CANDIDATE" for c in ordered)


@pytest.mark.parametrize(
    "title",
    [
        "lot",
        "silver",
        "unmarked",
        "unidentified",
        "parking lot sign",
        "silver paint lot",
    ],
)
def test_generic_or_nonjewelry_words_do_not_earn_targeted_priority(client, title):
    with client.app.state.sessions.begin() as s:
        for n, text in enumerate([title, "bracelet"]):
            s.add(
                DiscoveredItem(
                    identity=f"SYNTHETIC-negative-{n}",
                    source="HiBid FIXTURE",
                    ebay_item_id=str(n),
                    original_data={},
                    normalized={
                        "source": "HiBid FIXTURE",
                        "synthetic": True,
                        "raw_title": "SYNTHETIC " + text,
                    },
                )
            )
        s.flush()
        cards = auction_research(inbox_cards(s, Preferences()))
        assert not any(targeted_research(c) for c in cards)
        assert cards[0]["item"].raw_title == "SYNTHETIC bracelet"


def test_targeted_priority_preserves_source_precedence_and_stable_ties(client):
    with client.app.state.sessions.begin() as s:
        inputs = [
            ("HiBid", "turqoise lot"),
            ("HiBid", "blue stone lot"),
            ("HiBid", "sterling mixed lot"),
            ("ShopGoodwill", "bracelet"),
            ("CTBids", "turqoise lot"),
            ("EstateSales.org", "turqoise lot"),
            ("EstateSales.net", "turqoise lot"),
            ("Mercari", "turqoise lot"),
        ]
        for n, (source, title) in enumerate(inputs):
            s.add(
                DiscoveredItem(
                    identity=f"SYNTHETIC-stable-{n}",
                    source=source,
                    ebay_item_id=str(n),
                    original_data={},
                    normalized={
                        "source": source,
                        "synthetic": True,
                        "raw_title": "SYNTHETIC " + title,
                    },
                )
            )
        s.flush()
        cards = inbox_cards(s, Preferences())
        ordered = auction_research(cards)
        assert [c["item"].source for c in ordered[:4]] == [
            "ShopGoodwill",
            "HiBid",
            "HiBid",
            "HiBid",
        ]
        assert [c["row"].id for c in ordered[1:4]] == [
            c["row"].id for c in cards if c["item"].source == "HiBid"
        ]
        estate = {"CTBids", "EstateSales.org", "EstateSales.net"}
        assert [c["row"].id for c in ordered[4:]] == [
            c["row"].id for c in cards if c["item"].source in estate
        ]
        assert not any(c["item"].source == "Mercari" for c in ordered)
    assert "Mercari" in client.get("/inbox").text
    filtered = client.get("/inbox?focus=auction&source=HiBid&opportunity=misspelling")
    assert (
        "SYNTHETIC turqoise lot" in filtered.text
        and "SYNTHETIC blue stone lot" not in filtered.text
    )
