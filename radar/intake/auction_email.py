"""Offline parsing of observed Invaluable/LiveAuctioneers email structures.

URLs here are supplied evidence, never fetched. A decoded locator is not proof of
the sender, availability, ownership, material, or the redirect service's behavior.
"""

import base64
import json
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from urllib.parse import parse_qsl, urlsplit, urlunsplit

from radar.intake.parsers import Candidate, MessageType, ParsedMessage, email_date, iso_aware
from radar.schemas import ListingInput

VOID = {
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "param",
    "source",
    "wbr",
}
SKIP = {"script", "style", "iframe", "object", "svg", "math", "form", "head", "template"}
CTA = {"bid now", "view final price", "see the current bid", "increase bid", "lot image", "image"}


def target_identity(url, platform):
    """Stable platform IDs only, excluding mutable slugs and auction-local numbers."""
    if not isinstance(url, str) or len(url) > 8192 or re.search(r"[\x00-\x1f\x7f\\]", url):
        return None
    try:
        u = urlsplit(url)
        if (
            u.scheme != "https"
            or u.hostname not in {platform + ".com", "www." + platform + ".com"}
            or u.username
            or u.password
            or u.port not in (None, 443)
            or re.search(r"[\s%]", u.path)
            or any(p in (".", "..") for p in u.path.split("/"))
        ):
            return None
        if platform == "invaluable":
            item = re.fullmatch(r"/auction-lot/[^/]+-\d+-c-([0-9a-fA-F]{10})/?", u.path)
            catalog = re.fullmatch(r"/catalog/([a-z0-9]{10})/?", u.path)
        else:
            item = re.fullmatch(r"/item/(\d{1,30})(?:_[^/]+)?/?", u.path)
            catalog = None  # No global catalog-ID structure established by these samples.
        match = item or catalog
        if match:
            return (
                "item" if item else "catalog",
                match[1].lower(),
                urlunsplit(("https", "www." + platform + ".com", u.path, "", "")),
            )
    except ValueError:
        pass
    return None


def decode_target(value):
    if not re.fullmatch(r"[A-Za-z0-9_-]+={0,2}", value) or len(value) > 12000:
        raise ValueError("Unsupported encoded locator")
    return base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True).decode(
        "utf-8"
    )


def unique_object(pairs):
    result = {}
    for k, v in pairs:
        if k in result:
            raise ValueError("Ambiguous locator")
        result[k] = v
    return result


def explicit_target(href, title, platform):
    """Recover only observed attribute/base64 layouts; opaque qs tokens stay opaque."""
    if not isinstance(href, str) or len(href) > 16000 or re.search(r"[\s\x00-\x1f\x7f\\]", href):
        return None
    if target_identity(href, platform):
        return href, "DIRECT_SOURCE_LINK"
    try:
        u = urlsplit(href)
        if (
            u.scheme != "https"
            or u.username
            or u.password
            or u.port not in (None, 443)
            or u.fragment
        ):
            return None
        pairs = parse_qsl(u.query, keep_blank_values=True)
        if platform == "invaluable" and u.hostname in {
            "click.e.invaluable.com",
            "click.bid.invaluable.com",
        }:
            if (
                u.path in ("", "/")
                and len(pairs) == 1
                and pairs[0][0] == "qs"
                and target_identity(title, platform)
            ):
                return title, "EXPLICIT_ANCHOR_TITLE_URL"
        if platform == "liveauctioneers" and u.hostname == "link.liveauctioneers.com":
            parts = u.path.split("/")
            if (
                len(parts) == 5
                and parts[1] == "click"
                and not u.query
                and re.fullmatch(r"[A-Za-z0-9_-]{1,200}", parts[2])
                and re.fullmatch(r"[A-Za-z0-9_-]{1,200}", parts[4])
            ):
                target = decode_target(parts[3])
                if target_identity(target, platform):
                    return target, "EXPLICIT_BASE64_PATH_TARGET"
        if platform == "liveauctioneers" and u.hostname == "link3.liveauctioneers.com":
            if (
                re.fullmatch(r"/track/click/\d{1,30}/www\.liveauctioneers\.com", u.path)
                and len(pairs) == 1
                and pairs[0][0] == "p"
            ):
                outer = json.loads(decode_target(pairs[0][1]), object_pairs_hook=unique_object)
                inner = json.loads(outer["p"], object_pairs_hook=unique_object)
                target = inner["url"]
                if target_identity(target, platform):
                    return target, "EXPLICIT_BASE64_JSON_TARGET"
    except (ValueError, TypeError, KeyError, UnicodeError, RecursionError):
        pass
    return None


@dataclass(eq=False)
class Node:
    tag: str
    attrs: dict = field(default_factory=dict)
    parent: "Node | None" = None
    parts: list = field(default_factory=list)
    identities: set = field(default_factory=set)
    region: str = "PRIMARY"
    query_heading: str | None = None

    def text(self):
        return " ".join(
            " ".join(p.text() if isinstance(p, Node) else p for p in self.parts).split()
        )

    def images(self):
        return ([self] if self.tag == "img" else []) + [
            image for p in self.parts if isinstance(p, Node) for image in p.images()
        ]

    def has_linked_image(self):
        # Spacing/icon images inside a bid button are not another lot card.
        linked = (
            self.tag == "a"
            and bool(self.images())
            and self.text().lower() not in CTA
            and not re.match(r"BID \$", self.text(), re.I)
        )
        return linked or any(p.has_linked_image() for p in self.parts if isinstance(p, Node))


class EvidenceHTML(HTMLParser):
    """Inert structure only; retain card ancestry to avoid sharing prices across lots."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node("root")
        self.stack = [self.root]
        self.anchors = []
        self.region = "PRIMARY"
        self.query_heading = None
        self.nodes = 0

    def handle_starttag(self, tag, attrs):
        self.nodes += 1
        if self.nodes > 15000 or len(self.stack) > 100:
            raise ValueError("Email HTML structure exceeds safe limit")
        node = Node(
            tag, dict(attrs), self.stack[-1], region=self.region, query_heading=self.query_heading
        )
        self.stack[-1].parts.append(node)
        skipped = any(n.tag in SKIP for n in self.stack) or tag in SKIP
        if tag == "a" and not skipped:
            self.anchors.append(node)
        if tag == "img" and not skipped:
            self.section(node.attrs.get("alt") or "")
        if tag not in VOID:
            self.stack.append(node)

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                break

    def section(self, value):
        line = " ".join(value.split())
        if re.fullmatch(
            r"(?:Sponsored(?: lots| items)?|Recommended(?: for you)?|You may also like|"
            r"Check out these other items you may love)",
            line,
            re.I,
        ):
            self.region, self.query_heading = "RECOMMENDED", None
        elif m := re.fullmatch(r"New for (.+)", line):
            self.region, self.query_heading = "SEARCH_SECTION", m[1]

    def handle_data(self, value):
        if not any(n.tag in SKIP for n in self.stack):
            self.stack[-1].parts.append(value)
            self.section(value)


def card_scope(node, marker):
    """Bound a linked card by its layout and every neighboring linked block.

    Unresolved anchors participate in the fence. Their structural markers are
    never platform identities. Repeated image-bearing columns/tables stay separate
    even when both happen to link to the same item and query.
    """
    scope = node
    while scope.parent and scope.parent.identities == {marker}:
        if scope.tag in {"td", "div", "table", "article", "li"} and scope.has_linked_image():
            peers = [
                child
                for child in scope.parent.parts
                if isinstance(child, Node)
                and child.tag == scope.tag
                and child.identities
                and child.has_linked_image()
            ]
            if len(peers) > 1:
                break
        scope = scope.parent
    return scope


def observed_format(platform, subject, text):
    if platform == "invaluable":
        if re.search(r"you (?:were|have been) just outbid|you were just outbid", subject, re.I):
            return "invaluable-outbid-v1", MessageType.OUTBID
        if "Collector's Spotlight" in text:
            return "invaluable-spotlight-v1", MessageType.PROMOTIONAL_NEWSLETTER
        if (
            re.search(r"Your next .+ finds have arrived", subject, re.I)
            and "matching your saved searches" in text.lower()
            and "New for " in text
        ):
            return "invaluable-saved-search-v1", MessageType.SEARCH_MATCH
    else:
        if re.search(r"you[’']ve been outbid", subject, re.I) and "highest bid is now" in text:
            return "liveauctioneers-outbid-v1", MessageType.OUTBID
        if "Bidding has ended for " in subject and "This item was sold" in text:
            return "liveauctioneers-ended-recommendations-v1", MessageType.AUCTION_ENDED
    return None, MessageType.UNKNOWN


def price_evidence(text):
    quotes = []
    labels = [
        ("CURRENT_BID", r"Current Bid:|The highest bid is now"),
        ("STARTING_BID", r"Starting [Bb]id:"),
        ("ESTIMATE", r"(?:Presale [Ee]stimate|Estimate|Est\.):"),
        ("SOLD_RESULT", r"(?:Hammer [Pp]rice|Sold [Ff]or):"),
    ]
    for kind, label in labels:
        # Capture the complete token, including unsupported punctuation/letters.
        # A successful prefix match must never manufacture a smaller valid amount.
        atom = r"(?:(?:USD|CAD|AUD|EUR|GBP)\s*)?[$€£]?\s*\d[^\s;–-]*"
        pattern = r"(?:" + label + r")\s*(" + atom + r"(?:\s*[-–]\s*" + atom + r")?)"
        for m in re.finditer(pattern, text):
            quote = m[1].strip()
            currency = next(
                (c for c in ("USD", "CAD", "AUD", "EUR", "GBP") if quote.startswith(c)), None
            )
            currency = currency or ("EUR" if "€" in quote else "GBP" if "£" in quote else "XXX")
            number = r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d{1,2})?"
            amount = r"(?:(USD|CAD|AUD|EUR|GBP)\s*)?([$€£])?\s*(" + number + r")"
            match = re.fullmatch(amount + r"(?:\s*[-–]\s*" + amount + r")?\.?", quote)
            values = []
            if match:
                codes = [code for code in (match[1], match[4]) if code]
                symbols = [symbol for symbol in (match[2], match[5]) if symbol]
                coherent = all(code == currency for code in codes) and all(
                    symbol == {"EUR": "€", "GBP": "£"}.get(currency, "$") for symbol in symbols
                )
                try:
                    numbers = [n for n in (match[3], match[6]) if n]
                    if coherent and all(
                        len(n.replace(",", "").replace(".", "")) <= 14 for n in numbers
                    ):
                        values = [
                            str(Decimal(n.replace(",", "")).quantize(Decimal(".01")))
                            for n in numbers
                        ]
                except InvalidOperation:
                    pass
            entry = dict(
                kind=kind,
                quote=quote,
                currency=currency,
                amounts=values,
                parse_status="PARSED" if values else "UNKNOWN",
            )
            if entry not in quotes:
                quotes.append(entry)
    return quotes


def parse_auction_email(
    message, platform, source, html_parts, texts, attachments, received_override
):
    subject = str(message.get("Subject", ""))[:1000]
    dom = EvidenceHTML()
    dom.feed("\n".join(html_parts))
    plain = "\n".join(texts)
    text = dom.root.text() if html_parts else plain
    if len(text) > 100000 or len(plain) > 100000:
        raise ValueError("Decoded message text exceeds limit")
    format_id, kind = observed_format(platform, subject, text)
    reviewed = bool(source.sample_verified and format_id in source.reviewed_email_formats)
    sent = email_date(message.get("Date"))
    received_raw = (message.get("Received") or "").rsplit(";", 1)[-1]
    received = iso_aware(received_override) if received_override else email_date(received_raw)
    if received_override and not received:
        raise ValueError("Received time must include an explicit timezone")
    synthetic = str(message.get("X-Turquoise-Synthetic", "")).lower() == "true"
    source_name = source.name + (" FIXTURE" if synthetic else "")
    links, catalogs, unresolved, recovered = [], {}, 0, 0
    for anchor_number, node in enumerate(dom.anchors):
        resolved = explicit_target(
            node.attrs.get("href", ""), node.attrs.get("title", ""), platform
        )
        if not resolved:
            unresolved += 1
            parent = node
            while parent:
                parent.identities.add(("unresolved-block", anchor_number))
                parent = parent.parent
            continue
        target, method = resolved
        identity = target_identity(target, platform)
        if identity[0] == "catalog":
            catalogs[identity[1]] = {"auction_id": identity[1], "url": identity[2]}
            continue
        recovered += method != "DIRECT_SOURCE_LINK"
        links.append((identity, target, method, node))
        marker = (identity[1], node.region, node.query_heading)
        parent = node
        while parent:
            parent.identities.add(marker)
            parent = parent.parent
    # The observed Invaluable outbid multipart alternative supplies direct URLs
    # even when its HTML links are opaque. Never decode those opaque qs tokens.
    plain_targets = {}
    if platform == "invaluable":
        for url in re.findall(r'https://[^\s<>"\]]+', plain):
            identity = target_identity(url, platform)
            if identity and identity[0] == "catalog":
                catalogs[identity[1]] = {"auction_id": identity[1], "url": identity[2]}
            elif identity:
                plain_targets.setdefault(identity[1], (identity, url))
    for id_, (identity, target) in plain_targets.items():
        if id_ not in {link[0][1] for link in links}:
            links.append((identity, target, "DIRECT_TEXT_ALTERNATIVE", None))
    grouped = {}
    for identity, target, method, node in links:
        scope = card_scope(node, (identity[1], node.region, node.query_heading)) if node else None
        grouped.setdefault((identity[1], scope), []).append((identity, target, method, node))
    if len(grouped) > 100:
        raise ValueError("Too many lot references in one message")
    candidates = []
    for occurrence, ((id_, scope), entries) in enumerate(grouped.items(), 1):
        identity, target, method, node = entries[0]
        context = scope.text() if scope else plain if len(plain_targets) == 1 else ""
        region = node.region if node else "PRIMARY"
        title_options = []
        for _, _, _, anchor in entries:
            if anchor:
                for value in [img.attrs.get("alt", "") for img in anchor.images()] + [
                    anchor.text()
                ]:
                    value = " ".join((value or "").split())
                    if value and value.lower() not in CTA and not re.match(r"BID \$", value, re.I):
                        if value not in title_options:
                            title_options.append(value)
        title = title_options[0] if title_options else None
        if kind == MessageType.OUTBID and region == "PRIMARY" and len(grouped) == 1:
            match = re.search(r"(?:outbid on) (.+)", subject, re.I)
            title = match[1] if match else title
        title = title or f"TITLE UNAVAILABLE · {source.name} item {id_}"
        availability = (
            "ENDED" if kind == MessageType.AUCTION_ENDED and region == "PRIMARY" else "UNKNOWN"
        )
        quotes = price_evidence(context)
        current = [q for q in quotes if q["kind"] == "CURRENT_BID"]
        price = (
            current[0]["amounts"][0]
            if len(current) == 1 and len(current[0]["amounts"]) == 1 and availability != "ENDED"
            else None
        )
        currency = current[0]["currency"] if price is not None else "XXX"
        query = None
        if kind == MessageType.SEARCH_MATCH and region == "SEARCH_SECTION":
            values = [
                v
                for k, v in parse_qsl(urlsplit(target).query, keep_blank_values=True)
                if k == "utm_term"
            ]
            if (
                len(values) == 1
                and values[0]
                and node.query_heading.casefold() == values[0].casefold()
            ):
                query = values[0]  # Preserve exact URL evidence, not title-cased heading text.
        auctioneer = None
        if platform == "invaluable" and kind == MessageType.SEARCH_MATCH:
            # Observed card footer follows the title and precedes the price CTA.
            match = re.search(
                re.escape(title) + r"\s+(.+?)\s+(?:See the current bid|Current Bid:|"
                r"Starting Bid:|Estimate:|Presale Estimate:)",
                context,
            )
            auctioneer = match[1] if match else None
        if platform == "invaluable" and kind == MessageType.OUTBID:
            match = re.search(r"Question about this auction\? Contact\s+(.+?)\.(?:\s|$)", context)
            auctioneer = match[1] if match else None
        sale = next(iter(catalogs)) if len(catalogs) == 1 else None
        time_match = re.search(
            r"(?:January|February|March|April|May|June|July|August|September|October|"
            r"November|December)\s+\d{1,2}(?:\s+\d{4})?"
            r"(?:\s+\d{1,2}:\d{2}\s*[AP]M\s*[A-Z]{2,4})?|\b\d+ Days Away\b|\bToday\b",
            context,
        )
        time_raw = time_match[0] if time_match else None
        item = ListingInput(
            source=source_name,
            source_listing_id=id_,
            listing_url=identity[2],
            raw_title=title[:500],
            raw_description=context[:30000],
            item_price=price,
            currency=currency,
            seller_id=auctioneer,
            sale_auction_id=sale,
            item_end_time=None,
            synthetic=synthetic,
            raw_search_phrase=query,
            query_attribution_verified=False,
            shipping_status="UNKNOWN",
            buyer_profile=source.fee_profile,
            cost_status={"item_price": "ESTIMATED"} if price is not None else {},
        )
        evidence = dict(
            occurrence_id=f"card-{occurrence}",
            source_name=source_name,
            platform=platform,
            url=identity[2],
            identity_method=method,
            format_id=format_id,
            format_reviewed=reviewed,
            title=title,
            title_variants=title_options,
            message_role=region,
            availability=availability,
            price_quotes=quotes,
            quoted_price=price,
            price_kind="CURRENT_BID" if price is not None else "UNKNOWN",
            currency=currency,
            currency_note="A bare $ does not establish USD" if currency == "XXX" else None,
            observed_at=sent.isoformat() if sent else None,
            auction_time_raw=time_raw,
            auction_time_scope="AUCTION_OR_RELATIVE; NOT A LOT DEADLINE",
            query_relationship_supported=query is not None,
            query_evidence="EXPLICIT_URL_UTM_TERM" if query else None,
            remote_image_references=len(scope.images()) if scope else 0,
            photographs="REMOTE_REFERENCES_ONLY; NOT RETRIEVED",
            buyer_premium_rate=None,
            platform_fee_inclusion="UNKNOWN",
            order_id=None,
        )
        candidates.append(
            Candidate(
                item=item,
                evidence=evidence,
                query=query,
                query_supported=query is not None and reviewed,
            )
        )
    return ParsedMessage(
        subject=subject,
        text=text,
        sent_at=sent,
        received_at=received,
        synthetic=synthetic,
        message_type=kind,
        candidates=candidates,
        data=dict(
            source_name=source_name,
            platform=platform,
            format_id=format_id,
            format_reviewed=reviewed,
            status="REVIEWED_FORMAT" if reviewed else "NEEDS_REVIEW",
            format_note=(
                "Review applies only to this email structure; other templates remain unverified"
            ),
            catalogs=list(catalogs.values()),
            auction_id=next(iter(catalogs)) if len(catalogs) == 1 else None,
            unresolved_links=unresolved,
            wrapped_targets_recovered_offline=recovered,
            attachments_ignored=attachments,
            date_header=str(message.get("Date", "")),
            received_header_time=received_raw,
            query_explicit=None,
            components={},
        ),
    )
