import re
from collections import defaultdict

from sqlalchemy import select

from radar.analysis import analyze
from radar.db import (
    AlertMessage,
    AlertObservation,
    DiscoveredItem,
    DiscoveryObservation,
    Listing,
    SavedSourceSearch,
)
from radar.schemas import ListingInput
from radar.workflow import queue_state

AUCTION_PRIORITY = {
    "ShopGoodwill": 0,
    "HiBid": 1,
    "CTBids": 2,
    "EstateSales.org": 2,
    "EstateSales.net": 2,
    "Invaluable": 3,
    "LiveAuctioneers": 4,
}
COST_LABELS = {
    "item_price": "Item / bid",
    "buyer_premium": "Buyer premium",
    "buyer_service_fee": "Buyer service fee",
    "handling_fee": "Handling",
    "sales_tax": "Tax",
    "shipping_cost": "Quoted shipping",
}


def targeted_research(card):
    """Specific title cues plus jewelry/lot context; never material or maker evidence."""
    text, leads = card["item"].raw_title.casefold(), card["leads"]
    if "jewelry" not in leads and not re.search(r"\b(lots?|mixed|jars?)\b", text):
        return False
    return bool(
        ("misspelling" in leads and re.search(r"\b(turqoise|turquiose|navaho|navjo)\b", text))
        or ("silver" in leads and re.search(r"\b(sterling|925)\b", text))
        or ("poor description" in leads and re.search(r"\bblue\s+(stone|rock)\b", text))
    )


def auction_research(cards):
    """Research vocabulary and source priority, never an inferred bargain score."""

    def priority(card):
        source = {"invaluable": "Invaluable", "liveauctioneers": "LiveAuctioneers"}.get(
            card.get("auction_platform"), card["item"].source.removesuffix(" FIXTURE")
        )
        return AUCTION_PRIORITY.get(source)

    eligible = [c for c in cards if priority(c) is not None and c.get("availability") != "ENDED"]
    return sorted(
        eligible,
        key=lambda c: (
            priority(c),
            -targeted_research(c),
            -("jewelry" in c["leads"]),
            -len(c["leads"]),
        ),
    )


def alert_history(session, listing_id):
    return list(
        session.execute(
            select(AlertObservation, AlertMessage)
            .join(AlertMessage, AlertMessage.id == AlertObservation.message_id)
            .join(DiscoveredItem, DiscoveredItem.id == AlertObservation.item_id)
            .where(DiscoveredItem.listing_id == listing_id)
            .order_by(AlertObservation.id)
        )
    )


def inbox_cards(session, prefs):
    history = defaultdict(list)
    for o, m in session.execute(
        select(AlertObservation, AlertMessage)
        .join(AlertMessage, AlertMessage.id == AlertObservation.message_id)
        .order_by(AlertObservation.id)
    ):
        history[o.item_id].append((o, m))
    api_counts = defaultdict(int)
    for o in session.scalars(select(DiscoveryObservation)):
        api_counts[o.item_id] += 1
    cards = []
    for row in session.scalars(select(DiscoveredItem).order_by(DiscoveredItem.last_seen.desc())):
        item = ListingInput.model_validate(row.normalized)
        a = analyze(item, prefs)
        listing = session.get(Listing, row.listing_id) if row.listing_id else None
        text = item.raw_title.casefold()
        leads = []
        if re.search(
            r"\b(jewelry|jewellery|rings?|earrings?|bracelets?|cuffs?|pendants?|"
            r"necklaces?|brooches|brooch|conchos?)\b",
            text,
        ):
            leads.append("jewelry")
        if re.search(r"\brings?\b", text):
            leads.append("ring")
        if any(w in text for w in ("lot", "mixed", "jar")):
            leads.append("lot")
        if any(w in text for w in ("silver", "sterling", "925")):
            leads.append("silver")
        if any(w in text for w in ("signed", "hallmark", "stamped")):
            leads.append("collector")
        if any(w in text for w in ("turquiose", "turqoise", "navaho", "navjo")):
            leads.append("misspelling")
        if any(w in text for w in ("southwestern", "turquoise", "cuff", "concho")):
            leads.append("southwestern")
        if any(w in text for w in ("blue stone", "blue rock", "unmarked", "unidentified")):
            leads.append("poor description")
        costs = [
            dict(label=label, value=getattr(item, key), status=item.cost_status.get(key, "UNKNOWN"))
            for key, label in COST_LABELS.items()
        ]
        cards.append(
            dict(
                row=row,
                owner_item=ListingInput.model_validate(listing.data) if listing else None,
                quoted_premium_rate=next(
                    (
                        o.data.get("buyer_premium_rate")
                        for o, m in reversed(history[row.id])
                        if o.data.get("buyer_premium_rate") is not None
                    ),
                    None,
                ),
                item=item,
                a=a,
                history=history[row.id],
                auction_platform=next(
                    (o.data.get("platform") for o, m in history[row.id] if o.data.get("platform")),
                    None,
                ),
                availability="ENDED"
                if any(o.data.get("availability") == "ENDED" for o, m in history[row.id])
                else "UNKNOWN",
                leads=leads,
                costs=costs,
                state="IRRELEVANT"
                if row.inbox_status == "IRRELEVANT"
                else queue_state(listing, analyze(ListingInput.model_validate(listing.data), prefs))
                if listing
                else "NEW",
                observations=len(history[row.id]) + api_counts[row.id],
            )
        )
    return cards


def estate_groups(cards, messages=()):
    groups = {}
    for card in cards:
        item = card.get("owner_item") or card["item"]
        for kind, value in [("seller", item.seller_id), ("auction", item.sale_auction_id)]:
            if value:
                key = (item.source, kind, value)
                groups.setdefault(
                    key, dict(source=item.source, kind=kind, identity=value, cards=[])
                )["cards"].append(card)
    for group in groups.values():
        group["notices"] = [
            m
            for m in messages
            if group["kind"] == "auction"
            and m.data.get("auction_id") == group["identity"]
            and m.data.get("source_name") == group["source"]
        ]
        group["rings"] = sum("ring" in c["leads"] for c in group["cards"])
        group["lots"] = sum("lot" in c["leads"] for c in group["cards"])
    return list(groups.values())


def search_status(session):
    observations = list(
        session.execute(
            select(AlertObservation, AlertMessage).join(
                AlertMessage, AlertMessage.id == AlertObservation.message_id
            )
        )
    )
    statuses = []
    for saved in session.scalars(
        select(SavedSourceSearch).order_by(SavedSourceSearch.source_id, SavedSourceSearch.id)
    ):
        matched = {
            o.item_id
            for o, m in observations
            if m.source_id == saved.source_id
            and not m.synthetic
            and o.attribution_verified
            and o.query_text == saved.phrase
            and m.message_type == "SEARCH_MATCH"
        }
        statuses.append(dict(saved=saved, real_matches=len(matched)))
    return statuses
