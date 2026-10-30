"""Conservative local MIME/text parsing. No network, HTML rendering or attachment execution."""

import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from email import policy
from email.parser import BytesParser
from email.utils import parsedate_to_datetime
from enum import StrEnum
from html.parser import HTMLParser
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit

from pydantic import BaseModel, Field

from radar.intake.registry import SourceDefinition
from radar.schemas import ListingInput

MAX_MESSAGE_BYTES = 512_000
MAX_MESSAGES = 10
MAX_BATCH_BYTES = 3_000_000
MAX_LINKS = 100


class MessageType(StrEnum):
    SEARCH_MATCH = "SEARCH_MATCH"
    PROMOTIONAL_NEWSLETTER = "PROMOTIONAL_NEWSLETTER"
    AUCTION_CLOSING_REMINDER = "AUCTION_CLOSING_REMINDER"
    BID_CONFIRMED = "BID_CONFIRMED"
    OUTBID = "OUTBID"
    AUCTION_WON = "AUCTION_WON"
    PAYMENT_RECEIVED = "PAYMENT_RECEIVED"
    ORDER_SHIPPED = "ORDER_SHIPPED"
    UNKNOWN = "UNKNOWN"


class SafeEmailHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.text = []
        self.links = []
        self.frames = []
        self.anchor = None
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("img", "input", "meta", "link", "embed", "source", "wbr"):
            return
        if self.skip:
            if tag not in ("br", "hr"):
                self.skip += 1
            return
        if tag in (
            "script",
            "style",
            "iframe",
            "object",
            "svg",
            "math",
            "form",
            "head",
            "template",
        ):
            self.skip = 1
            return
        if tag in ("article", "li", "tr"):
            self.frames.append({"tag": tag, "text": []})
        if tag in ("br", "hr", "p", "div", "tr", "li", "article", "section"):
            self.handle_data("\n")
        if tag == "a":
            self.anchor = {
                "href": dict(attrs).get("href", ""),
                "text": [],
                "frame": self.frames[-1] if self.frames else None,
            }

    def handle_endtag(self, tag):
        if self.skip:
            if tag not in ("br", "hr", "img", "input", "meta", "link"):
                self.skip -= 1
            return
        if tag == "a" and self.anchor:
            self.links.append(self.anchor)
            self.anchor = None
        if tag in ("article", "li", "tr") and self.frames:
            if self.frames[-1]["tag"] == tag:
                self.frames.pop()
        if tag in ("p", "div", "tr", "li", "article", "section"):
            self.handle_data("\n")

    def handle_data(self, data):
        if self.skip:
            return
        self.text.append(data)
        for frame in self.frames:
            frame["text"].append(data)
        if self.anchor:
            self.anchor["text"].append(data)


def iso_aware(value):
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt if dt.tzinfo is not None else None
    except (ValueError, AttributeError):
        return None


def email_date(value):
    try:
        dt = parsedate_to_datetime(value)
        return dt if dt.tzinfo is not None else None
    except (TypeError, ValueError, OverflowError):
        return None


def field(text, labels, exact=False):
    values = []
    for label in labels:
        for match in re.finditer(
            r"^\s*" + re.escape(label) + r"\s*:[ \t]?(.*)$", text, re.I | re.M
        ):
            value = match.group(1) if exact else match.group(1).strip()
            if value and value not in values:
                values.append(value)
    return values[0] if len(values) == 1 else None


def amount(text, labels):
    value = field(text, labels)
    if value is None:
        return None
    match = re.fullmatch(
        r"(?:USD\s*\$?|\$)\s*(\d{1,10}(?:,\d{3})*(?:\.\d{1,2})?)(?:\s*USD)?", value, re.I
    )
    if not match:
        return None
    try:
        return str(Decimal(match.group(1).replace(",", "")).quantize(Decimal(".01")))
    except InvalidOperation:
        return None


def safe_link(url, source):
    try:
        u = urlsplit(url)
        host = (u.hostname or "").lower()
        if u.scheme != "https" or u.username or u.password or u.port not in (None, 443):
            return None
        if not any(host == d or host.endswith("." + d) for d in source.domains):
            return None
        # No redirect unwrapping, tracking requests or arbitrary relative URL resolution.
        if re.search(r"/(?:track|tracking|click|redirect)(?:/|$)", u.path, re.I):
            return None
        identifiers = [
            (k, v)
            for k, v in parse_qsl(u.query)
            if k.casefold() in ("id", "itemid", "lotid", "auctionid")
            and re.fullmatch(r"[A-Za-z0-9_|-]{1,100}", v)
        ]
        return urlunsplit(("https", host, u.path, urlencode(identifiers), ""))
    except ValueError:
        return None


def link_identity(url, source_id, source):
    clean = safe_link(url, source)
    if not clean:
        return None
    path = urlsplit(clean).path
    if source_id == "shopgoodwill":
        m = re.match(r"^/item/(\d+)(?:/|$)", path, re.I)
        if m:
            return ("item", m.group(1), clean)
    if source_id == "hibid":
        m = re.match(r"^/(lot|catalog)/(\d+)(?:/|$)", path, re.I)
        if m:
            return ("catalog" if m.group(1).lower() == "catalog" else "item", m.group(2), clean)
    if (
        source.sample_verified
        and source.item_path_prefix
        and path.startswith(source.item_path_prefix)
    ):
        identifier = unquote(path[len(source.item_path_prefix) :].split("/")[0])
        if re.fullmatch(r"[A-Za-z0-9_-]{1,100}", identifier):
            return ("item", identifier, clean)
    return ("unresolved", None, clean)


def classify(subject, text):
    heading = subject + "\n" + "\n".join(text.splitlines()[:3])
    rules = [
        (
            MessageType.PAYMENT_RECEIVED,
            r"payment received|payment receipt|payment confirmation|order paid",
        ),
        (MessageType.ORDER_SHIPPED, r"order shipped|shipment confirmation"),
        (MessageType.OUTBID, r"outbid"),
        (MessageType.AUCTION_WON, r"auction won|you(?: have)? won|winning bidder notice"),
        (MessageType.BID_CONFIRMED, r"bid confirmed|bid confirmation|bid placed"),
        (
            MessageType.AUCTION_CLOSING_REMINDER,
            r"closing soon|auction closing|closing reminder|ending today",
        ),
        (
            MessageType.PROMOTIONAL_NEWSLETTER,
            r"newsletter|featured auctions|auction preview|promotional",
        ),
        (MessageType.SEARCH_MATCH, r"personal shopper|saved.search|search match|search results"),
    ]
    for kind, pattern in rules:
        if re.search(pattern, heading, re.I):
            return kind
    if field(text, ["Saved search", "Search phrase"], exact=True):
        return MessageType.SEARCH_MATCH
    return MessageType.UNKNOWN


class Candidate(BaseModel):
    item: ListingInput
    evidence: dict
    query: str | None = None
    query_supported: bool = False


class ParsedMessage(BaseModel):
    subject: str
    text: str
    message_type: MessageType
    sent_at: datetime | None = None
    received_at: datetime | None = None
    synthetic: bool = False
    data: dict = Field(default_factory=dict)
    candidates: list[Candidate] = Field(default_factory=list)


def parse_message(raw: bytes, source_id: str, source: SourceDefinition, received_override=None):
    if not raw or len(raw) > MAX_MESSAGE_BYTES:
        raise ValueError("Each message must contain 1–512000 bytes")
    try:
        message = BytesParser(policy=policy.default).parsebytes(raw)
    except (RecursionError, ValueError):
        raise ValueError("Unsupported MIME structure") from None
    subject = str(message.get("Subject", ""))[:1000]
    texts = []
    html_parts = []
    attachments = 0

    def inline_parts(part, depth=0):
        nonlocal attachments
        if depth > 20:
            raise ValueError("MIME nesting exceeds safe limit")
        if part.get_content_disposition() == "attachment" or part.get_filename():
            attachments += 1
            return
        if part.is_multipart():
            for child in part.iter_parts():
                yield from inline_parts(child, depth + 1)
        else:
            yield part

    parts = list(inline_parts(message))
    if len(parts) > 100:
        raise ValueError("Too many MIME parts")
    for part in parts:
        if part.get_content_type() not in ("text/plain", "text/html"):
            continue
        try:
            content = part.get_content()
        except (LookupError, UnicodeError, ValueError):
            content = (part.get_payload(decode=True) or b"").decode("utf-8", "replace")
        if isinstance(content, str):
            (html_parts if part.get_content_type() == "text/html" else texts).append(content)
    anchors = []
    if html_parts:
        extractor = SafeEmailHTML()
        extractor.feed("\n".join(html_parts))
        text = "".join(extractor.text)
        for anchor in extractor.links[:MAX_LINKS]:
            frame = anchor["frame"]
            anchors.append(
                (
                    anchor["href"],
                    "".join(anchor["text"]).strip(),
                    "".join(frame["text"]) if frame else text,
                )
            )
    else:
        text = "\n".join(texts) if texts else raw.decode("utf-8", "replace")
        for block in re.split(r"\n\s*[-=]{3,}\s*\n", text):
            for url in re.findall(r'https?://[^\s<>"\]]+', block)[:MAX_LINKS]:
                anchors.append((url.rstrip(".,);"), "", block))
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if len(text) > 100000:
        raise ValueError("Decoded message text exceeds limit")
    kind = classify(subject, text)
    sent = email_date(message.get("Date"))
    received_raw = (message.get("Received") or "").rsplit(";", 1)[-1]
    received = iso_aware(received_override) if received_override else email_date(received_raw)
    if received_override and not received:
        raise ValueError("Received time must include an explicit timezone")
    source_name = source.name
    synthetic = str(message.get("X-Turquoise-Synthetic", "")).lower() == "true"
    if synthetic:
        source_name += " FIXTURE"
    currency = (field(text, ["Currency"]) or "USD").upper()
    if not re.fullmatch(r"[A-Z]{3}", currency):
        currency = "XXX"
    query = field(text, ["Saved search", "Search phrase"], exact=True)
    order = field(text, ["Order ID", "Order Number", "Order #"])
    auction = field(text, ["Auction ID", "Estate ID"])
    seller = field(text, ["Auction House", "Seller ID", "Seller"])
    closing_raw = field(text, ["Auction closes", "Closing time", "Auction closing time"])
    closing = iso_aware(closing_raw)
    identities = []
    catalogs = []
    unsafe = 0
    for url, title, context in anchors[:MAX_LINKS]:
        identity = link_identity(url, source_id, source)
        if not identity:
            unsafe += 1
            continue
        link_kind, id_, clean = identity
        if link_kind == "catalog":
            catalogs.append({"auction_id": id_, "url": clean})
            continue
        labels = source.field_labels if source.sample_verified else {}
        explicit_id = field(
            context, labels.get("item_id", ["Item ID", "Source listing ID", "Lot ID"])
        )
        if link_kind == "unresolved":
            if not explicit_id:
                continue
            id_ = explicit_id
        identities.append((id_, clean, title, context))
    # Explicit item ID without a link is useful for lifecycle correlation, never a catalog lot.
    if not identities and source_id == "shopgoodwill":
        id_ = field(text, ["Item ID"])
        if id_ and re.fullmatch(r"\d{1,30}", id_):
            identities = [(id_, None, "", text)]
    if not auction and len({c["auction_id"] for c in catalogs}) == 1:
        auction = catalogs[0]["auction_id"]
    unique = {}
    for id_, url, title, context in identities:
        context = context.replace("\r\n", "\n").replace("\r", "\n")
        if id_ in unique:
            continue
        block_ids = {i for i, _, _, ctx in identities if ctx == context}
        scoped = context if len(block_ids) == 1 else ""
        labels = source.field_labels if source.sample_verified else {}
        title = field(scoped, labels.get("title", ["Title", "Item title", "Lot title"])) or title
        title = title or f"TITLE UNAVAILABLE · {source.name} item {id_}"
        price = amount(
            scoped, labels.get("price", ["Current price", "Current bid", "Price", "Bid amount"])
        )
        final = amount(scoped, ["Final item subtotal", "Final item price"])
        if kind == MessageType.PAYMENT_RECEIVED and final is not None:
            price = final
        shipping = amount(scoped, ["Shipping", "Shipping cost"])
        handling = amount(scoped, ["Handling", "Handling fee"])
        premium = amount(scoped, ["Buyer premium", "Buyer's premium"])
        service_fee = amount(scoped, ["Buyer service fee", "Service fee", "Marketplace fee"])
        sales_tax = amount(scoped, ["Sales tax", "Tax"])
        combined_shipping = amount(
            scoped, ["Shipping/Handling", "Shipping & Handling", "Shipping and handling"]
        )
        if kind == MessageType.PAYMENT_RECEIVED and len({i[0] for i in identities}) > 1:
            # A card may share its text block with order totals. Keep all non-item
            # paid charges at order level until an owner supplies an allocation.
            shipping = handling = premium = service_fee = sales_tax = combined_shipping = None
        premium_raw = field(scoped, ["Buyer premium", "Buyer's premium"])
        rate = re.fullmatch(r"(\d+(?:\.\d+)?)\s*%", premium_raw or "")
        local_query = (
            field(scoped, labels.get("query", ["Saved search", "Search phrase"]), exact=True)
            or query
        )
        supported = kind == MessageType.SEARCH_MATCH and local_query is not None
        end_raw = field(scoped, labels.get("closing", ["Lot closes", "Closing time"]))
        end = iso_aware(end_raw)
        item = ListingInput(
            source=source_name,
            currency=currency,
            source_listing_id=id_,
            listing_url=url,
            raw_title=title[:500],
            raw_description=scoped[:30000],
            item_price=price,
            shipping_cost=shipping,
            handling_fee=handling,
            buyer_premium=premium,
            buyer_service_fee=service_fee,
            sales_tax=sales_tax,
            buyer_profile=source.fee_profile,
            seller_id=field(scoped, labels.get("seller", ["Auction House", "Seller ID", "Seller"]))
            or seller,
            sale_auction_id=field(scoped, labels.get("auction", ["Auction ID", "Estate ID"]))
            or auction,
            listing_time=iso_aware(field(scoped, ["Listing created", "Listed at"])),
            item_end_time=end,
            raw_search_phrase=local_query if supported else None,
            query_attribution_verified=False,
            synthetic=synthetic,
            shipping_status="UNKNOWN",
            shipping_evidence=field(scoped, ["Shipping indicator"])
            or (
                "Shipping Available (99669 unconfirmed)" if "Shipping Available" in scoped else None
            ),
            cost_status={
                k: "ESTIMATED"
                for k, v in dict(
                    item_price=price,
                    shipping_cost=shipping,
                    handling_fee=handling,
                    buyer_premium=premium,
                    buyer_service_fee=service_fee,
                    sales_tax=sales_tax,
                ).items()
                if v is not None
            },
        )
        unique[id_] = Candidate(
            item=item,
            query=local_query if supported else None,
            query_supported=supported,
            evidence={
                "url": url,
                "title": title,
                "quoted_price": price,
                "quoted_service_fee": service_fee,
                "quoted_sales_tax": sales_tax,
                "quoted_shipping_handling": combined_shipping,
                "final_item_subtotal": final,
                "buyer_premium_rate": str(Decimal(rate.group(1)) / 100)
                if rate and Decimal(rate.group(1)) <= 100
                else None,
                "closing_time_raw": end_raw,
                "closing_timezone_raw": timezone_text(end_raw),
                "shipping_indicator": item.shipping_evidence,
                "format_status": (
                    "OWNER_VERIFIED_SAMPLE"
                    if source.sample_kind == "OWNER_PROVIDED"
                    else "SYNTHETIC_SAMPLE_RULES"
                )
                if source.sample_verified
                else "PROVISIONAL",
                "source_name": source_name,
                "order_id": order,
            },
        )
    components = {}
    if kind == MessageType.PAYMENT_RECEIVED:
        labels = {
            "item_subtotal": ["Items subtotal", "Order item subtotal"]
            + (["Item subtotal"] if len(unique) == 1 else []),
            "sales_tax": ["Sales tax", "Tax"],
            "shipping_cost": ["Shipping", "Shipping cost"],
            "handling_fee": ["Handling", "Handling fee"],
            "shipping_handling": [
                "Shipping/Handling",
                "Shipping & Handling",
                "Shipping and handling",
            ],
            "buyer_premium": ["Buyer premium", "Buyer's premium"],
            "buyer_service_fee": ["Buyer service fee", "Service fee"],
            "order_total": ["Order total", "Total paid"],
        }
        components = {key: amount(text, names) for key, names in labels.items()}
    data = {
        "source_name": source_name,
        "currency": currency,
        "order_id": order,
        "auction_id": auction,
        "seller_id": seller,
        "catalogs": catalogs,
        "closing_time_raw": closing_raw,
        "closing_at": closing.isoformat() if closing else None,
        "closing_timezone_raw": timezone_text(closing_raw),
        "date_header": str(message.get("Date", "")),
        "received_header_time": received_raw or None,
        "received_override": received_override,
        "attachments_ignored": attachments,
        "unsafe_links_ignored": unsafe,
        "components": components,
        "status": "PROVISIONAL" if unique else "NEEDS_REVIEW",
        "format_note": "Real search digest formats unverified; extracted values need owner review",
        "ship_to_postal_code": field(text, ["Ship to postal code"]),
        "query_explicit": query if kind == MessageType.SEARCH_MATCH else None,
    }
    return ParsedMessage(
        subject=subject,
        text=text,
        message_type=kind,
        sent_at=sent,
        received_at=received,
        synthetic=synthetic,
        data=data,
        candidates=list(unique.values()),
    )


def timezone_text(value):
    if not value:
        return None
    match = re.search(
        r"(Z|[+-]\d{2}:\d{2}|\b(?:UTC|GMT|AKST|AKDT|PST|PDT|MST|MDT|CST|CDT|EST|EDT)\b)\s*$", value
    )
    return match.group(1) if match else None
