"""Clearly synthetic email structures learned from private, reviewed examples."""

import base64
import json
from email.message import EmailMessage
from html import escape
from urllib.parse import quote, urlencode

import pytest
from sqlalchemy import select

from radar.db import (
    AlertMessage,
    AlertObservation,
    DiscoveredItem,
    IntakeSource,
    Listing,
    SavedSourceSearch,
    database,
)
from radar.intake.auction_email import explicit_target, target_identity
from radar.intake.parsers import parse_message
from radar.intake.registry import SourceDefinition, seed_sources
from radar.intake.service import confirm_query, import_alerts
from radar.intake.views import auction_research, inbox_cards
from radar.main import preferences

INV_SEARCH = "invaluable-saved-search-v1"
LA_OUTBID = "liveauctioneers-outbid-v1"
LA_ENDED = "liveauctioneers-ended-recommendations-v1"


def definition(platform, formats=()):
    return SourceDefinition(
        name={"invaluable": "Invaluable", "liveauctioneers": "LiveAuctioneers"}[platform],
        domains=[platform + ".com"],
        sample_verified=True,
        sample_kind="OWNER_PROVIDED",
        sample_reference="SYNTHETIC simulation of a prior format review",
        reviewed_email_formats={
            f: "SYNTHETIC fixture, not real-source validation" for f in formats
        },
    )


def message(subject, html, plain="SYNTHETIC fixture", synthetic=True):
    m = EmailMessage()
    m["Subject"] = "SYNTHETIC " + subject
    m["Date"] = "Sun, 01 Nov 2026 10:00:00 -0900"
    if synthetic:
        m["X-Turquoise-Synthetic"] = "true"
    m.set_content(plain)
    m.add_alternative(html, subtype="html")
    return m.as_bytes()


def b64(value):
    return base64.urlsafe_b64encode(value.encode()).decode().rstrip("=")


def live_link(id_, layout="path", slug="synthetic-blue-stone-ring"):
    target = f"https://www.liveauctioneers.com/item/{id_}_{slug}?action=bidnow&utm_source=SYNTHETIC"
    if layout == "path":
        return f"https://link.liveauctioneers.com/click/SYNTHETIC/{b64(target)}/SYNTHETIC"
    inner = json.dumps({"url": target, "id": "SYNTHETIC", "u": 1, "v": 2})
    outer = json.dumps({"s": "SYNTHETIC", "v": 1, "p": inner})
    return (
        "https://link3.liveauctioneers.com/track/click/99999999/www.liveauctioneers.com?"
        + urlencode({"p": b64(outer)})
    )


def inv_card(code, title, amount="", term="blue stone ring", number=10):
    target = f"https://www.invaluable.com/auction-lot/synthetic-title-{number}-c-{code}?utm_term={quote(term)}"
    anchor = (
        'href="https://click.e.invaluable.com/?qs=SYNTHETIC_OPAQUE" '
        f'title="{escape(target, quote=True)}"'
    )
    return f'''<td><table><tr><td>November 14</td></tr>
    <tr><td><a {anchor}><img src="https://images.example.invalid/private.jpg"
    alt="{escape(title, quote=True)}"></a></td></tr>
    <tr><td>{escape(title)}</td></tr><tr><td>SYNTHETIC Auctioneer {code}</td></tr>
    <tr><td>{escape(amount)}</td></tr>
    <tr><td><a {anchor}>See the current bid</a></td></tr></table></td>'''


def search_mail(cards, synthetic=True, heading="Blue Stone Ring"):
    return message(
        "Your next blue stone ring finds have arrived",
        "<p>Discover items matching your saved searches.</p><span>New for "
        + escape(heading)
        + "</span>"
        + cards,
        synthetic=synthetic,
    )


def test_invaluable_cards_keep_queries_titles_prices_and_sellers_separate():
    cards = "<table><tr>" + inv_card(
        "0000000001", "SYNTHETIC blue stone ring", "Current Bid: USD $12.00"
    )
    cards += (
        inv_card("0000000002", "SYNTHETIC silver cuff", "Starting Bid: USD $99.00")
        + "</tr></table>"
    )
    cards += (
        "<h2>Sponsored</h2><table><tr>"
        + inv_card("0000000003", "SYNTHETIC sponsored ring", "Estimate: USD $100 - $200")
        + "</tr></table>"
    )
    parsed = parse_message(search_mail(cards), "invaluable", definition("invaluable", [INV_SEARCH]))
    assert parsed.message_type == "SEARCH_MATCH" and len(parsed.candidates) == 3
    first, second, sponsored = parsed.candidates
    assert str(first.item.item_price) == "12.00" and first.item.currency == "USD"
    assert first.item.seller_id == "SYNTHETIC Auctioneer 0000000001"
    assert second.item.item_price is None and sponsored.item.item_price is None
    assert second.evidence["price_quotes"][0]["kind"] == "STARTING_BID"
    assert sponsored.evidence["price_quotes"][0]["kind"] == "ESTIMATE"
    assert first.query == second.query == "blue stone ring"
    assert first.query_supported and second.query_supported
    assert sponsored.query is None and not sponsored.query_supported
    assert sponsored.evidence["message_role"] == "RECOMMENDED"
    for c in parsed.candidates:
        assert c.item.item_end_time is None and c.evidence["auction_time_raw"] == "November 14"
        assert c.item.shipping_status == "UNKNOWN" and c.item.buyer_premium is None
        assert c.item.buyer_service_fee is None and c.item.image_urls == []
        assert c.evidence["remote_image_references"] > 0
        assert "qs=" not in str(c.item.listing_url)


def test_review_of_another_template_does_not_validate_search_query_credit(client):
    # A synthetic payload without its flag simulates the non-synthetic attribution branch.
    raw = search_mail(
        "<table><tr>" + inv_card("0000000001", "SYNTHETIC ring") + "</tr></table>", synthetic=False
    )
    with client.app.state.sessions.begin() as s:
        source = s.get(IntakeSource, "invaluable")
        source.data = definition("invaluable", ["invaluable-outbid-v1"]).model_dump(mode="json")
        import_alerts(s, "invaluable", [raw])
        o = s.scalar(select(AlertObservation))
        assert o.query_text == "blue stone ring" and not o.attribution_verified
        assert not o.data["format_reviewed"]
        source.data = definition("invaluable", [INV_SEARCH]).model_dump(mode="json")
        import_alerts(s, "invaluable", [raw.replace(b"Your next", b"Your next new")])
        observations = list(s.scalars(select(AlertObservation).order_by(AlertObservation.id)))
        assert [o.attribution_verified for o in observations] == [False, True]


@pytest.mark.parametrize("layout", ["path", "json"])
def test_liveauctioneers_explicit_targets_are_offline_and_canonical(layout):
    href = live_link("900010001", layout)
    recovered = explicit_target(href, "", "liveauctioneers")
    assert recovered is not None
    kind, id_, clean = target_identity(recovered[0], "liveauctioneers")
    assert kind == "item" and id_ == "900010001"
    assert clean == "https://www.liveauctioneers.com/item/900010001_synthetic-blue-stone-ring"
    assert (
        target_identity(
            "https://www.liveauctioneers.com/item/900010001_changed-slug", "liveauctioneers"
        )[1]
        == id_
    )


@pytest.mark.parametrize(
    "href",
    [
        "https://click.e.invaluable.com/?qs=OPAQUE",
        "https://link.liveauctioneers.com/click/opaque/not-a-target/token",
        "https://link.liveauctioneers.com/redirect?url=https://www.liveauctioneers.com/item/123_x",
        "https://link3.liveauctioneers.com/track/click/123/www.liveauctioneers.com?p=OPAQUE",
        "https://evil.example.invalid/item/123_blue-ring",
        "https://www.liveauctioneers.com.evil.example.invalid/item/123_ring",
        "https://user@www.liveauctioneers.com/item/123_ring",
        "http://www.liveauctioneers.com/item/123_ring",
        "https://www.liveauctioneers.com:444/item/123_ring",
        "https://www.liveauctioneers.com/item/123_ring/../account",
        "https://www.liveauctioneers.com/item/123_ring%2faccount",
    ],
)
def test_opaque_or_unsafe_links_do_not_become_lots(href):
    assert explicit_target(href, "", "liveauctioneers") is None


def test_invaluable_title_metadata_catalogs_and_global_item_identity():
    first = "https://www.invaluable.com/auction-lot/first-title-10-c-0000000001"
    changed = "https://www.invaluable.com/auction-lot/changed-title-99-c-0000000001"
    different = "https://www.invaluable.com/auction-lot/first-title-10-c-0000000002"
    assert target_identity(first, "invaluable")[1] == target_identity(changed, "invaluable")[1]
    assert target_identity(first, "invaluable")[1] != target_identity(different, "invaluable")[1]
    assert (
        target_identity("https://www.invaluable.com/catalog/abcdefghij", "invaluable")[0]
        == "catalog"
    )
    assert target_identity("https://www.invaluable.com/auction-lot/ring-80", "invaluable") is None
    assert (
        target_identity("https://www.liveauctioneers.com/catalog/123_synthetic", "liveauctioneers")
        is None
    )
    assert (
        explicit_target("https://click.e.invaluable.com/?qs=SYNTHETIC", first, "invaluable")[0]
        == first
    )
    assert (
        explicit_target("https://evil.example.invalid/?qs=SYNTHETIC", first, "invaluable") is None
    )


def outbid_mail(id_="900010001", price="$70", layout="json"):
    link = escape(live_link(id_, layout), quote=True)
    return message(
        "You’ve Been Outbid on SYNTHETIC sterling cuff.",
        f'<a href="{link}"><img alt src="https://images.example.invalid/item.jpg"></a>'
        f'<p>The highest bid is now {price}.</p><a href="{link}">BID $100 NOW</a>',
    )


@pytest.mark.parametrize("price,currency", [("$70", "XXX"), ("USD $70", "USD"), ("CAD $70", "CAD")])
def test_outbid_price_is_not_the_call_to_action_or_a_shipping_quote(price, currency):
    p = parse_message(
        outbid_mail(price=price), "liveauctioneers", definition("liveauctioneers", [LA_OUTBID])
    )
    c = p.candidates[0]
    assert p.message_type == "OUTBID" and len(p.candidates) == 1
    assert str(c.item.item_price) == "70.00" and c.item.currency == currency
    assert c.query is None and not c.query_supported and c.item.item_end_time is None
    assert c.item.seller_id is None and c.item.sale_auction_id is None
    assert c.item.shipping_cost is None and c.item.shipping_status == "UNKNOWN"
    assert c.item.image_urls == [] and c.evidence["price_kind"] == "CURRENT_BID"


def ended_mail():
    primary = escape(live_link("900010001"), quote=True)
    recommendation = escape(live_link("900010002"), quote=True)
    return message(
        "You have good taste. Bidding has ended for SYNTHETIC cuff",
        f'<p>This item was sold</p><a href="{primary}"><img alt="SYNTHETIC ended cuff"></a>'
        f'<a href="{primary}">View Final Price</a><h1>Check out these other items you may love</h1>'
        f'<table><tr><td><a href="{recommendation}"><img alt="SYNTHETIC blue stone necklace"></a>'
        f'<p>7 Days Away</p><a href="{recommendation}">BID NOW</a></td></tr></table>',
    )


def test_ended_primary_and_recommendations_keep_distinct_roles_and_no_query_credit(client):
    with client.app.state.sessions.begin() as s:
        source = s.get(IntakeSource, "liveauctioneers")
        source.data = definition("liveauctioneers", [LA_OUTBID, LA_ENDED]).model_dump(mode="json")
        ended_raw = ended_mail()
        import_alerts(s, "liveauctioneers", [outbid_mail(), ended_raw])
        duplicate = import_alerts(s, "liveauctioneers", [ended_raw])
        assert duplicate.stats["duplicate_messages"] == 1 and duplicate.stats["new_candidates"] == 0
        assert len(list(s.scalars(select(AlertMessage)))) == 2
        assert len(list(s.scalars(select(DiscoveredItem)))) == 2
        observations = list(s.scalars(select(AlertObservation)))
        assert len(observations) == 3
        assert all(not o.attribution_verified and o.query_text is None for o in observations)
        with pytest.raises(ValueError):
            confirm_query(s, observations[-1].id)
        cards = inbox_cards(s, preferences(s))
        ended = next(c for c in cards if c["item"].source_listing_id == "900010001")
        assert ended["availability"] == "ENDED" and ended["item"].item_price is None
        assert [c["item"].source_listing_id for c in auction_research(cards)] == ["900010002"]
    assert "SYNTHETIC ended cuff" in client.get("/inbox").text
    assert "ENDED" in client.get("/inbox/candidates/1").text
    assert "SYNTHETIC ended cuff" not in client.get("/inbox?focus=auction").text
    assert "SYNTHETIC blue stone necklace" in client.get("/estate-groups").text
    assert "Seller / auction unknown" in client.get("/estate-groups").text


def test_invaluable_plain_alternative_preserves_auction_time_and_never_uses_local_lot_number():
    raw = message(
        "You were just outbid on SYNTHETIC earrings",
        "<p>Current Bid: $75</p>",
        plain="""SYNTHETIC
Lot 80: SYNTHETIC earrings
https://www.invaluable.com/auction-lot/synthetic-earrings-80-c-0000000001
https://www.invaluable.com/catalog/abcdefghij
November 15 2026 09:00 AM MST
Current Bid: $75
""",
    )
    p = parse_message(raw, "invaluable", definition("invaluable", ["invaluable-outbid-v1"]))
    c = p.candidates[0]
    assert c.item.source_listing_id == "0000000001" and c.item.sale_auction_id == "abcdefghij"
    assert (
        c.item.item_end_time is None
        and c.evidence["auction_time_raw"] == "November 15 2026 09:00 AM MST"
    )
    assert c.evidence["identity_method"] == "DIRECT_TEXT_ALTERNATIVE"


def test_existing_custom_platform_history_is_reused_and_visible(tmp_path):
    engine, sessions = database(f"sqlite:///{tmp_path / 'custom.db'}")
    with sessions.begin() as s:
        source = IntakeSource(
            id="custom-original-invaluable",
            data={
                **definition("invaluable", [INV_SEARCH]).model_dump(mode="json"),
                "name": "SYNTHETIC original auction source",
            },
        )
        s.add(source)
        s.flush()
        saved = SavedSourceSearch(
            source_id=source.id,
            phrase=" old blue stone ring ",
            group_name="Owner group",
            selected=True,
            configured=True,
        )
        s.add(saved)
        s.flush()
        saved_id = saved.id
        source_data = dict(source.data)
        seed_sources(s)
        seed_sources(s)
        assert s.get(IntakeSource, "invaluable") is None and source.data == source_data
        assert s.get(SavedSourceSearch, saved_id).phrase == " old blue stone ring "
        assert s.get(SavedSourceSearch, saved_id).configured
        assert (
            len(
                list(
                    s.scalars(
                        select(SavedSourceSearch).where(SavedSourceSearch.source_id == source.id)
                    )
                )
            )
            == 66
        )
        raw = search_mail(
            "<table><tr>" + inv_card("0000000001", "SYNTHETIC blue stone ring") + "</tr></table>"
        )
        import_alerts(s, source.id, [raw])
        cards = auction_research(inbox_cards(s, preferences(s)))
        assert (
            len(cards) == 1
            and cards[0]["item"].source == "SYNTHETIC original auction source FIXTURE"
        )
    engine.dispose()


def test_changed_slug_and_separate_queries_keep_one_item_and_both_observations(client):
    first = search_mail(
        "<table><tr>" + inv_card("0000000001", "SYNTHETIC blue stone ring") + "</tr></table>"
    )
    second = search_mail(
        "<table><tr>"
        + inv_card("0000000001", "SYNTHETIC renamed ring", term="old blue stone ring", number=99)
        + "</tr></table>",
        heading="Old Blue Stone Ring",
    )
    with client.app.state.sessions.begin() as s:
        import_alerts(s, "invaluable", [first, second])
        duplicate = import_alerts(s, "invaluable", [first])
        assert duplicate.stats["duplicate_messages"] == 1
        assert len(list(s.scalars(select(DiscoveredItem)))) == 1
        assert len(list(s.scalars(select(AlertMessage)))) == 2
        observations = list(s.scalars(select(AlertObservation)))
        assert len(observations) == 2
        assert {o.query_text for o in observations} == {"blue stone ring", "old blue stone ring"}
        assert all(not o.attribution_verified for o in observations)


@pytest.mark.parametrize("platform", ["invaluable", "liveauctioneers"])
def test_source_filters_workbench_and_identify_handoff(client, platform):
    raw = (
        outbid_mail()
        if platform == "liveauctioneers"
        else search_mail(
            "<table><tr>" + inv_card("0000000001", "SYNTHETIC blue stone ring") + "</tr></table>"
        )
    )
    with client.app.state.sessions.begin() as s:
        import_alerts(s, platform, [raw])
        item = s.scalar(select(DiscoveredItem))
        name, id_ = item.source, item.id
        source = s.get(IntakeSource, platform)
        assert not source.data["sample_verified"]
        assert (
            len(
                list(
                    s.scalars(
                        select(SavedSourceSearch).where(SavedSourceSearch.source_id == platform)
                    )
                )
            )
            == 65
        )
    page = client.get("/inbox", params={"focus": "auction", "source": name})
    assert page.status_code == 200 and f"/inbox/candidates/{id_}" in page.text
    response = client.post(f"/inbox/candidates/{id_}/review")
    assert response.status_code == 200 and "Identify this jewelry" in response.text
    assert "email section:" in response.text and "Buyer premium" in response.text
    with client.app.state.sessions() as s:
        listing = s.scalar(select(Listing))
        assert listing.data["shipping_status"] == "UNKNOWN"
        assert listing.data["item_end_time"] is None
    assert (
        client.post(
            "/sources",
            data={
                "source_id": "custom-duplicate",
                "name": "SYNTHETIC duplicate source",
                "domains": platform + ".com",
            },
        ).status_code
        == 422
    )
