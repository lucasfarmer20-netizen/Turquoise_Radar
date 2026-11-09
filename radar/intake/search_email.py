"""Observed owner-supplied search notifications, parsed without fetching resources.

Only the observed plaintext structures are supported. Format review does not
authenticate a sender or establish current availability, ownership or shipping.
"""

import re
from decimal import Decimal, InvalidOperation
from urllib.parse import parse_qsl, urlsplit, urlunsplit

from radar.intake.parsers import (
    MAX_LINKS,
    Candidate,
    MessageType,
    ParsedMessage,
    email_date,
    field,
    iso_aware,
    safe_link,
)
from radar.schemas import ListingInput

SHOP_FORMAT = "shopgoodwill-personal-shopper-v1"
HIBID_FORMAT = "hibid-saved-search-v1"
URL = re.compile(r'https://[^\s<>"\]]+')
NON_SEARCH = re.compile(
    r"^\s*(?:recommended(?:\s+(?:lots|items|auctions|for you))?|recommendations|"
    r"you may (?:also )?like|sponsored(?:\s+(?:lots|items|auctions))?|"
    r"featured(?:\s+(?:lots|items|auctions))?)"
    r"\s*[:!]?\s*$",
    re.I,
)
CTA = {"bid now", "view lot", "view item", "view details", "lot image", "image"}


def _target(url, source_id, source):
    """Canonical direct item links only; never retain query/tracking material."""
    if len(url) > 8192 or re.search(r"[\x00-\x20\x7f\\]", url):
        return None
    clean = safe_link(url, source)
    if not clean:
        return None
    path = urlsplit(clean).path
    if any(part in (".", "..") for part in path.split("/")):
        return None
    prefix = "item" if source_id == "shopgoodwill" else "lot"
    match = re.fullmatch(r"/" + prefix + r"/(\d{1,30})(?:/[^/]*)?/?", path, re.I)
    if not match:
        return None
    return match[1], urlunsplit(("https", urlsplit(clean).netloc, f"/{prefix}/{match[1]}", "", ""))


def _quote(value):
    """A complete observed monetary token; a bare dollar sign does not mean USD."""
    if not value:
        return None, "XXX"
    number = r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d{1,2})?"
    match = re.fullmatch(
        r"(?:(USD|CAD|AUD|EUR|GBP)\s*)?([$€£])?\s*(" + number + r")(?:\s+(USD|CAD|AUD|EUR|GBP))?",
        value,
        re.I,
    )
    if not match or not (match[1] or match[2] or match[4]):
        return None, "XXX"
    codes = [part.upper() for part in (match[1], match[4]) if part]
    currency = codes[0] if codes else {"€": "EUR", "£": "GBP"}.get(match[2], "XXX")
    if any(code != currency for code in codes) or (
        match[2] and match[2] != {"EUR": "€", "GBP": "£"}.get(currency, "$")
    ):
        return None, "XXX"
    try:
        amount = Decimal(match[3].replace(",", "")).quantize(Decimal(".01"))
        if amount > Decimal("9999999999.99"):
            return None, currency
        return str(amount), currency
    except InvalidOperation:
        return None, currency


def _shop_cards(text, source):
    # Even malformed item headings fence a block; their fields cannot leak into a neighbor.
    headings = list(re.finditer(r"^[ \t]*Item[ \t]*#\s*:([^\n]*)$", text, re.I | re.M))
    if len(headings) > MAX_LINKS:
        raise ValueError("Too many item references in one message")
    cards = []
    other_sections = list(re.finditer(NON_SEARCH.pattern, text, re.I | re.M))
    for index, heading in enumerate(headings):
        block = text[
            heading.start() : headings[index + 1].start()
            if index + 1 < len(headings)
            else len(text)
        ]
        identifier = heading[1].strip()
        url = field(block, ["URL"])
        target = _target(url, "shopgoodwill", source) if url else None
        if not re.fullmatch(r"\d{1,30}", identifier) or not target or target[0] != identifier:
            continue
        title = field(block, ["Title"])
        quote = field(block, ["Price"])
        price, currency = _quote(quote)
        search_section = not any(section.start() < heading.start() for section in other_sections)
        cards.append(
            dict(
                identifier=identifier,
                url=target[1],
                title=title,
                price=price,
                currency=currency,
                price_quote=quote,
                price_kind="OBSERVED_PRICE" if quote else "UNKNOWN",
                bids_raw=field(block, ["Bids"]),
                started_raw=field(block, ["Started"]),
                time_raw=None,
                role="SEARCH_SECTION" if search_section else "RECOMMENDED",
                query_region=search_section,
            )
        )
    return cards


def _hibid_query(subject, text, source):
    heading = re.search(r"New lots for your saved (.+?) search\b", subject, re.I)
    display = heading[1] if heading else None
    values = []
    for match in URL.finditer(text):
        url = match[0].rstrip(").,;")
        if not safe_link(url, source):
            continue
        parts = urlsplit(url)
        if (
            parts.hostname not in {"hibid.com", "www.hibid.com"}
            or parts.path.rstrip("/") != "/lots"
        ):
            continue
        terms = [
            value for key, value in parse_qsl(parts.query, keep_blank_values=True) if key == "q"
        ]
        if len(terms) != 1:
            return None, display, None
        values.append(terms[0])
    unique = set(values)
    if len(unique) != 1:
        return None, display, None
    query = next(iter(unique))

    def normalize_display(value):
        return " ".join(value.split()).casefold()

    section_seen = False
    item_seen = False
    heading_ends = []
    offset = 0
    lines = text.splitlines(keepends=True)
    for index, line in enumerate(lines):
        if re.match(r"^\s*Your saved search\b", line, re.I):
            section_seen = True
        if section_seen and any(
            _target(match[0].rstrip(").,;"), "hibid", source) for match in URL.finditer(line)
        ):
            item_seen = True
        separator = lines[index - 1].strip() if index else ""
        if (
            section_seen
            and not item_seen
            and display
            and re.fullmatch(r"\*{3,100}", separator)
            and index + 1 < len(lines)
            and lines[index + 1].strip() == separator
            and normalize_display(line.strip()) == normalize_display(display)
        ):
            heading_ends.append(offset + len(line))
        offset += len(line)
    if (
        not query.strip()
        or len(query) > 200
        or re.search(r"[\x00-\x1f\x7f]", query)
        or not display
        or normalize_display(display) != normalize_display(query)
        or len(heading_ends) != 1
    ):
        return None, display, None
    # The observed direct q link is in the footer; the header navigation is
    # opaque. It corroborates the phrase, while the body heading scopes cards.
    return query, display, heading_ends[0]


def _hibid_cards(text, source, search_start):
    cards = {}
    role = "SEARCH_SECTION"
    offset = 0
    previous = ""
    current = None
    for line in text.splitlines(keepends=True):
        if NON_SEARCH.fullmatch(line.strip()):
            role = "RECOMMENDED"
            current = None
        matches = list(URL.finditer(line))
        if matches:
            # An unresolved or ambiguous linked card is still a structural boundary.
            current = None
            targets = [_target(match[0].rstrip(").,;"), "hibid", source) for match in matches]
            if len(targets) == 1 and targets[0]:
                identifier, url = targets[0]
                card_role = (
                    role if search_start is not None and offset >= search_start else "UNVERIFIED"
                )
                key = identifier, card_role
                title = previous.strip()
                if title.endswith("(") and line.strip().endswith(")"):
                    # Plaintext MIME wraps link labels across two lines:
                    # title  (\nhttps://official-source/lot/id ).
                    title = title[:-1].rstrip()
                if not title or title.casefold() in CTA or URL.search(title):
                    title = None
                row = cards.setdefault(
                    key,
                    dict(
                        identifier=identifier,
                        url=url,
                        title=None,
                        price=None,
                        currency="XXX",
                        price_quote=None,
                        price_kind="UNKNOWN",
                        bids_raw=None,
                        started_raw=None,
                        time_raw=None,
                        role=card_role,
                        query_region=card_role == "SEARCH_SECTION",
                    ),
                )
                if title and not row["title"]:
                    row["title"] = title
                current = row
                if len(cards) > MAX_LINKS:
                    raise ValueError("Too many item references in one message")
        elif current and re.fullmatch(r"\s*\d{1,2}/\d{1,2}/\d{4}(?:\s+[^\n]{1,80})?\s*", line):
            current["time_raw"] = line.strip()
        elif current and re.fullmatch(r"\s*Ends \d{1,2}/\d{1,2}(?:/\d{4})?\s*", line, re.I):
            current["time_raw"] = line.strip()
        if line.strip():
            previous = line
        offset += len(line)
    return list(cards.values())


def parse_search_email(message, source_id, source, texts, attachments, received_override):
    """Return only a recognized source structure; leave other formats on existing paths."""
    if source_id not in {"shopgoodwill", "hibid"}:
        return None
    text = "\n".join(texts).replace("\r\n", "\n").replace("\r", "\n")
    if len(text) > 100000:
        raise ValueError("Decoded message text exceeds limit")
    subject = str(message.get("Subject", ""))[:1000]
    if re.search(r"^\s*This is a promotional message\.", text, re.I | re.M):
        return None
    if source_id == "shopgoodwill":
        if not re.search(r"Your Personal Shopper", subject, re.I):
            return None
        structure_valid = "personal shopper" in text.casefold()
        format_id = SHOP_FORMAT
        query = field(text, ["Search Keyword(s)"], exact=True) if structure_valid else None
        display = query
        cards = _shop_cards(text, source)
        query_method = "EXPLICIT_SEARCH_KEYWORDS_LABEL"
    else:
        if not re.search(r"New lots for your saved .+? search\b", subject, re.I):
            return None
        structure_valid = bool(re.search(r"Your saved search", text, re.I))
        format_id = HIBID_FORMAT
        query, display, start = (
            _hibid_query(subject, text, source) if structure_valid else (None, None, None)
        )
        cards = _hibid_cards(text, source, start)
        query_method = "EXPLICIT_SOURCE_Q_WITH_SUBJECT_AND_BODY_AGREEMENT"
    if query and (not query.strip() or len(query) > 200 or re.search(r"[\x00-\x1f\x7f]", query)):
        query = None
    reviewed = bool(
        structure_valid
        and source.sample_verified
        and source.sample_kind == "OWNER_PROVIDED"
        and source.reviewed_email_formats.get(format_id)
    )
    sent = email_date(message.get("Date"))
    received_raw = str(message.get("Received", "")).rsplit(";", 1)[-1]
    received = iso_aware(received_override) if received_override else email_date(received_raw)
    if received_override and not received:
        raise ValueError("Received time must include an explicit timezone")
    synthetic = str(message.get("X-Turquoise-Synthetic", "")).lower() == "true"
    source_name = source.name + (" FIXTURE" if synthetic else "")
    candidates = []
    for index, card in enumerate(cards):
        exact = query if card["query_region"] else None
        title = card["title"] or f"TITLE UNAVAILABLE · {source.name} item {card['identifier']}"
        item = ListingInput(
            source=source_name,
            source_listing_id=card["identifier"],
            listing_url=card["url"],
            raw_title=title[:500],
            raw_description=title[:500],
            item_price=card["price"],
            currency=card["currency"],
            raw_search_phrase=exact,
            query_attribution_verified=False,
            buyer_profile=source.fee_profile,
            shipping_status="UNKNOWN",
            synthetic=synthetic,
            cost_status={"item_price": "ESTIMATED"} if card["price"] is not None else {},
        )
        candidates.append(
            Candidate(
                item=item,
                query=exact,
                query_supported=exact is not None and reviewed,
                evidence=dict(
                    occurrence_id=f"search-card-{index + 1}",
                    platform=source_id,
                    source_name=source_name,
                    format_id=format_id,
                    format_reviewed=reviewed,
                    identity_method="DIRECT_SOURCE_LINK",
                    url=card["url"],
                    title=title,
                    quoted_price=card["price"],
                    price_quote=card["price_quote"],
                    price_kind=card["price_kind"],
                    bid_count_quote=card["bids_raw"],
                    currency=card["currency"],
                    currency_note="A bare $ does not establish USD"
                    if card["currency"] == "XXX"
                    else None,
                    observed_at=sent.isoformat() if sent else None,
                    listing_started_raw=card["started_raw"],
                    auction_time_raw=card["time_raw"],
                    auction_time_scope="UNVERIFIED_LOCAL_OR_RELATIVE; NOT A LOT DEADLINE",
                    availability="UNKNOWN",
                    message_role=card["role"],
                    query_relationship_supported=exact is not None,
                    query_evidence=query_method if exact is not None else None,
                    displayed_query=display,
                    photographs="NOT RETRIEVED",
                    order_id=None,
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
            platform=source_id,
            format_id=format_id,
            format_reviewed=reviewed,
            status="REVIEWED_FORMAT" if reviewed else "NEEDS_REVIEW",
            format_note="Review applies only to this observed search-notification structure",
            query_explicit=query,
            attachments_ignored=attachments,
            date_header=str(message.get("Date", "")),
            received_header_time=received_raw,
            personal_shopper_expiry_raw=field(text, ["Personal Shopper Ends"])
            if source_id == "shopgoodwill"
            else None,
            catalogs=[],
            components={},
        ),
    )
