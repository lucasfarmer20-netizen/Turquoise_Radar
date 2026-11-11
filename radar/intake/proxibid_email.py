"""Proxibid "New Items In Your Saved Search" emails.

Observed structure (owner mailbox, Nov 2026): every link is a click-tracking redirect on
email.proxibid.com, so links carry no identity and are never followed or kept. Each card is a
title link followed by a "VIEW ITEM" link, and each lot has one unlinked photo at

    https://images.proxibid.com/AuctionImages/<house>/<auction>/FullDetail/<lot>_1.jpg

The photo path is the only stable identity in the message: auction id + lot number. Cards are
paired with photos in document order, and only when the counts agree; a card without a photo
has no identity and is skipped. The listing link is the auction's public catalog page (the lot
page needs an internal lot id the email does not contain). Prices are not in the email.
"""

import re
from html.parser import HTMLParser

from radar.intake.parsers import (
    MAX_LINKS,
    Candidate,
    MessageType,
    ParsedMessage,
    email_date,
    iso_aware,
)
from radar.schemas import ListingInput

FORMAT = "proxibid-saved-search-v1"
IMAGE = re.compile(
    r"^https://images\.proxibid\.com/AuctionImages/(\d{1,12})/(\d{1,12})/FullDetail/"
    r"([A-Za-z0-9]{1,12})_\d{1,3}\.(?:jpe?g|png)$",
    re.I,
)
CTA = "view item"


class _Cards(HTMLParser):
    """Anchor texts and lot-photo references in document order; nothing is fetched."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.events = []
        self.text = []
        self._anchor = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "a":
            self._anchor = []
        elif tag == "img":
            src = (a.get("src") or "").split("?")[0].strip()
            m = IMAGE.match(src)
            if m:
                self.events.append(("img", m[1], m[2], m[3], src))

    def handle_endtag(self, tag):
        if tag == "a" and self._anchor is not None:
            label = " ".join("".join(self._anchor).split())
            self.events.append(("a", label))
            self._anchor = None

    def handle_data(self, data):
        self.text.append(data)
        if self._anchor is not None:
            self._anchor.append(data)


def cards(html):
    p = _Cards()
    p.feed(html)
    anchors = [e[1] for e in p.events if e[0] == "a"]
    titles = [
        anchors[i]
        for i in range(len(anchors) - 1)
        if anchors[i] and anchors[i].casefold() != CTA and anchors[i + 1].casefold() == CTA
    ]
    photos, seen = [], set()
    for e in p.events:
        if e[0] == "img" and (e[2], e[3]) not in seen:
            seen.add((e[2], e[3]))
            photos.append(e[1:])
    text = " ".join("".join(p.text).split())
    if len(titles) > MAX_LINKS:
        raise ValueError("Too many item references in one message")
    if not titles or len(titles) != len(photos):
        return [], text, len(titles), len(photos)
    return (
        [
            dict(title=t, house=h, auction=a, lot=lot, image=src)
            for t, (h, a, lot, src) in zip(titles, photos, strict=True)
        ],
        text,
        len(titles),
        len(photos),
    )


def parse_proxibid_email(message, source, html_parts, attachments, received_override):
    subject = str(message.get("Subject", ""))[:1000]
    if not html_parts or not re.search(r"saved search", subject, re.I):
        return None
    found, text, n_titles, n_photos = cards("\n".join(html_parts))
    if len(text) > 100000:
        raise ValueError("Decoded message text exceeds limit")
    sent = email_date(message.get("Date"))
    received_raw = str(message.get("Received", "")).rsplit(";", 1)[-1]
    received = iso_aware(received_override) if received_override else email_date(received_raw)
    if received_override and not received:
        raise ValueError("Received time must include an explicit timezone")
    synthetic = str(message.get("X-Turquoise-Synthetic", "")).lower() == "true"
    source_name = source.name + (" FIXTURE" if synthetic else "")
    candidates = []
    for index, c in enumerate(found):
        url = f"https://www.proxibid.com/asp/Catalog.asp?aid={c['auction']}"
        note = f"Proxibid auction {c['auction']}, lot {c['lot']} (find the lot in the catalog)"
        item = ListingInput(
            source=source_name,
            source_listing_id=f"{c['auction']}-{c['lot']}",
            sale_auction_id=c["auction"],
            seller_id=c["house"],
            listing_url=url,
            raw_title=c["title"][:500],
            raw_description=f"{c['title'][:400]}\n{note}",
            image_urls=[c["image"]],
            buying_options=["AUCTION"],
            buyer_profile=source.fee_profile,
            shipping_status="UNKNOWN",
            synthetic=synthetic,
            query_attribution_verified=False,
        )
        candidates.append(
            Candidate(
                item=item,
                evidence=dict(
                    occurrence_id=f"search-card-{index + 1}",
                    platform="proxibid",
                    source_name=source_name,
                    format_id=FORMAT,
                    identity_method="LOT_PHOTO_PATH (auction id + lot number)",
                    url=url,
                    title=c["title"],
                    quoted_price=None,
                    price_kind="UNKNOWN",
                    observed_at=sent.isoformat() if sent else None,
                    availability="UNKNOWN",
                    message_role="SEARCH_SECTION",
                    photographs="REFERENCED, NOT RETRIEVED AT IMPORT",
                ),
            )
        )
    return ParsedMessage(
        subject=subject,
        text=text,
        message_type=MessageType.SEARCH_MATCH,
        sent_at=sent,
        received_at=received,
        synthetic=synthetic,
        candidates=candidates,
        data=dict(
            source_name=source_name,
            platform="proxibid",
            format_id=FORMAT,
            status="PARSED" if candidates else "NEEDS_REVIEW",
            format_note=f"{n_titles} titled cards, {n_photos} lot photos"
            + ("" if candidates or not n_titles else "; counts differ, nothing imported"),
            attachments_ignored=attachments,
            date_header=str(message.get("Date", "")),
            received_header_time=received_raw,
            catalogs=[],
            components={},
        ),
    )
