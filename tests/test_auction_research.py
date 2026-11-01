"""Synthetic regressions for defects observed in private notifications.

These examples are not evidence of a real saved-search format or a live bargain.
"""

from email.message import EmailMessage

import pytest
from sqlalchemy import select

from radar.db import AlertMessage, DiscoveredItem
from radar.intake.parsers import parse_message
from radar.intake.registry import SourceDefinition
from radar.intake.service import import_alerts, promote_candidate
from radar.intake.views import auction_research, inbox_cards, search_status
from radar.main import preferences


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
        assert cards[1]["item"].raw_title == "SYNTHETIC bracelet"
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
