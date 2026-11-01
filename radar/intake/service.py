"""Private intake, stable source identities and explicit owner-paid-cost reconciliation."""

import base64
import json
from datetime import UTC
from decimal import Decimal
from hashlib import sha256
from types import SimpleNamespace

from pydantic import BaseModel
from sqlalchemy import select

from radar.anomalies import PatternInput, add_pattern
from radar.db import (
    AlertImport,
    AlertInvoice,
    AlertMessage,
    AlertObservation,
    DiscoveredItem,
    Feedback,
    IntakeSource,
    InvoiceEvidence,
    Listing,
    ListingEdit,
    SearchPattern,
)
from radar.intake.parsers import MAX_BATCH_BYTES, MAX_MESSAGES, MessageType, parse_message
from radar.intake.registry import SourceDefinition
from radar.schemas import ListingInput, Money, Shipping, utcnow
from radar.sources import dedup_key, parse_import

COSTS = [
    "item_price",
    "buyer_premium",
    "buyer_service_fee",
    "sales_tax",
    "shipping_cost",
    "handling_fee",
]


def digest(value):
    return sha256(value.encode()).hexdigest()


def source_definition(session, source_id):
    source = session.get(IntakeSource, source_id)
    if not source:
        raise ValueError("Unknown intake source")
    return source, SourceDefinition.model_validate(source.data)


def pattern_for(session, phrase, verified=False):
    if phrase is None:
        return None
    p = session.scalar(select(SearchPattern).where(SearchPattern.phrase == phrase))
    if not p and verified:
        p = add_pattern(
            session,
            PatternInput(
                phrase=phrase,
                original_phrase=phrase,
                canonical_concept="Owner-confirmed saved search",
                anomaly_category="manual",
                priority_weight=80,
            ),
        )
    return p


def observation_occurrences(observation):
    """Read new per-card evidence or the unchanged legacy single observation."""
    cards = observation.data.get("card_occurrences")
    if not cards:
        return [observation]
    return [
        SimpleNamespace(
            id=observation.id,
            message_id=observation.message_id,
            item_id=observation.item_id,
            **card,
        )
        for card in cards
    ]


def add_candidate(session, message, candidate, verified=False):
    item = candidate.item
    verified = bool(verified and candidate.query_supported)
    p = pattern_for(session, candidate.query, verified)
    item = item.model_copy(
        update={"search_pattern_id": p.id if p else None, "query_attribution_verified": verified}
    )
    key = dedup_key(item)
    found = session.scalar(select(DiscoveredItem).where(DiscoveredItem.identity == key))
    is_new = found is None
    if found is None:
        found = DiscoveredItem(
            identity=key,
            source=item.source,
            ebay_item_id=item.source_listing_id or key,
            normalized=item.model_dump(mode="json"),
            original_data={
                "alert_message_id": message.id,
                "source_id": message.source_id,
                "evidence": candidate.evidence,
            },
        )
        session.add(found)
        session.flush()
    else:
        found.last_seen = utcnow()
        # Refresh observed source fields, never the linked owner's financial record.
        updates = {
            k: v
            for k, v in item.model_dump(mode="json").items()
            if v is not None
            and k
            in (
                "item_price",
                "shipping_cost",
                "handling_fee",
                "buyer_premium",
                "buyer_service_fee",
                "sales_tax",
                "seller_id",
                "sale_auction_id",
                "item_end_time",
                "shipping_evidence",
            )
        }
        if not item.raw_title.startswith("TITLE UNAVAILABLE"):
            updates["raw_title"] = item.raw_title
        updates["cost_status"] = {
            **found.normalized.get("cost_status", {}),
            **item.model_dump(mode="json")["cost_status"],
        }
        if candidate.evidence.get("platform") in ("invaluable", "liveauctioneers"):
            if item.item_price is not None:
                updates["currency"] = item.currency
            if candidate.evidence.get("availability") == "ENDED":
                updates["item_price"] = None
                updates["cost_status"]["item_price"] = "UNKNOWN"
        found.normalized = {**found.normalized, **updates}
    existing = session.scalar(
        select(AlertObservation).where(
            AlertObservation.message_id == message.id,
            AlertObservation.item_id == found.id,
        )
    )
    if not existing:
        session.add(
            AlertObservation(
                message_id=message.id,
                item_id=found.id,
                data=candidate.evidence,
                query_text=candidate.query,
                attribution_verified=verified,
            )
        )
    elif candidate.evidence.get("occurrence_id"):
        cards = [
            dict(query_text=o.query_text, attribution_verified=o.attribution_verified, data=o.data)
            for o in observation_occurrences(existing)
        ]
        if not any(
            c["data"].get("occurrence_id") == candidate.evidence["occurrence_id"] for c in cards
        ):
            cards.append(
                dict(
                    query_text=candidate.query,
                    attribution_verified=verified,
                    data=candidate.evidence,
                )
            )
            # The original first occurrence and columns stay intact. No migration.
            existing.data = {**existing.data, "card_occurrences": cards}
    return found, is_new


def import_alerts(session, source_id, messages, received_override=None):
    if not 1 <= len(messages) <= MAX_MESSAGES or sum(map(len, messages)) > MAX_BATCH_BYTES:
        raise ValueError("Import 1–10 messages, maximum 3MB combined")
    source, definition = source_definition(session, source_id)
    # Parse all before mutations. No original body is ever included in an exception.
    parsed = [parse_message(raw, source_id, definition, received_override) for raw in messages]
    stats = dict(
        messages=len(messages),
        new_messages=0,
        duplicate_messages=0,
        new_candidates=0,
        duplicate_candidates=0,
        observations=0,
        needs_review=0,
        message_ids=[],
    )
    for raw, p in zip(messages, parsed, strict=True):
        key = digest(source_id + "|" + sha256(raw).hexdigest())
        message = session.scalar(select(AlertMessage).where(AlertMessage.digest == key))
        if message:
            message.import_count += 1
            message.last_imported_at = utcnow()
            stats["duplicate_messages"] += 1
            stats["message_ids"].append(message.id)
            continue
        message = AlertMessage(
            source_id=source_id,
            digest=key,
            raw_b64=base64.b64encode(raw).decode(),
            safe_text=p.text,
            subject=p.subject,
            message_type=p.message_type,
            data=p.data,
            received_at=p.received_at.astimezone(UTC) if p.received_at else None,
            sent_at=p.sent_at.astimezone(UTC) if p.sent_at else None,
            synthetic=p.synthetic,
        )
        session.add(message)
        session.flush()
        stats["new_messages"] += 1
        stats["message_ids"].append(message.id)
        stats["needs_review"] += int(p.data["status"] == "NEEDS_REVIEW")
        for c in p.candidates:
            _, new = add_candidate(
                session,
                message,
                c,
                definition.sample_verified and definition.sample_kind == "OWNER_PROVIDED",
            )
            stats["new_candidates"] += int(new)
            stats["duplicate_candidates"] += int(not new)
            stats["observations"] += 1
        session.flush()
        order = p.data.get("order_id")
        if order:
            update_invoice(session, message, order)
    source.last_successful_import = utcnow()
    batch = AlertImport(source_id=source_id, stats=stats)
    session.add(batch)
    session.flush()
    return batch


def update_invoice(session, message, order):
    identity = digest(message.source_id + "|" + str(message.synthetic) + "|" + order)
    invoice = session.scalar(select(AlertInvoice).where(AlertInvoice.identity == identity))
    if not invoice:
        invoice = AlertInvoice(
            source_id=message.source_id, identity=identity, order_id=order, data={}
        )
        session.add(invoice)
        session.flush()
    session.add(InvoiceEvidence(invoice_id=invoice.id, message_id=message.id))
    session.flush()
    messages = list(
        session.scalars(
            select(AlertMessage)
            .join(InvoiceEvidence, InvoiceEvidence.message_id == AlertMessage.id)
            .where(InvoiceEvidence.invoice_id == invoice.id)
            .order_by(AlertMessage.id)
        )
    )
    item_ids = sorted(
        set(
            session.scalars(
                select(AlertObservation.item_id).where(
                    AlertObservation.message_id.in_([m.id for m in messages])
                )
            )
        )
    )
    paid = [m for m in messages if m.message_type == MessageType.PAYMENT_RECEIVED]
    components = {}
    conflicts = []
    keys = [
        "item_subtotal",
        "sales_tax",
        "shipping_cost",
        "handling_fee",
        "shipping_handling",
        "buyer_premium",
        "buyer_service_fee",
        "order_total",
    ]
    for key in keys:
        values = {m.data.get("components", {}).get(key) for m in paid} - {None}
        if len(values) > 1:
            conflicts.append(key)
        components[key] = next(iter(values)) if len(values) == 1 else None
    final_items = {}
    paid_ids = [m.id for m in paid]
    if paid_ids:
        for observation in session.scalars(
            select(AlertObservation).where(AlertObservation.message_id.in_(paid_ids))
        ):
            value = observation.data.get("final_item_subtotal")
            if value is not None:
                existing = final_items.get(str(observation.item_id))
                if existing is not None and existing != value:
                    conflicts.append("conflicting final item subtotal")
                final_items[str(observation.item_id)] = value
    derived_subtotal = False
    if item_ids and all(str(i) in final_items for i in item_ids):
        line_sum = sum((Decimal(final_items[str(i)]) for i in item_ids), Decimal(0))
        if components.get("item_subtotal") is None:
            components["item_subtotal"] = str(line_sum)
            derived_subtotal = True
        elif Decimal(components["item_subtotal"]) != line_sum:
            conflicts.append("final line items differ from stated order subtotal")
    reconciliation = reconcile(components, conflicts)
    if paid and {m.data.get("currency", "USD") for m in paid} != {"USD"}:
        reconciliation = {**reconciliation, "status": "UNSUPPORTED_CURRENCY"}
    invoice.data = {
        **invoice.data,
        "synthetic": message.synthetic,
        "item_ids": item_ids,
        "message_ids": [m.id for m in messages],
        "paid_message_ids": [m.id for m in paid],
        "components": components,
        "final_item_subtotals": final_items,
        "derived_subtotal": derived_subtotal,
        **reconciliation,
    }
    return invoice


def reconcile(c, conflicts=()):
    required = ["item_subtotal", "sales_tax", "order_total"]
    missing = [key for key in required if c.get(key) is None]
    shipping = None
    if c.get("shipping_handling") is not None:
        shipping = Decimal(c["shipping_handling"])
        if c.get("shipping_cost") is not None and c.get("handling_fee") is not None:
            if Decimal(c["shipping_cost"]) + Decimal(c["handling_fee"]) != shipping:
                conflicts = [*conflicts, "shipping/handling representations"]
    elif c.get("shipping_cost") is not None and c.get("handling_fee") is not None:
        shipping = Decimal(c["shipping_cost"]) + Decimal(c["handling_fee"])
    else:
        missing.append("shipping and handling breakdown or explicit combined amount")
    if conflicts:
        return dict(
            status="CONFLICT",
            missing=missing,
            conflicts=list(conflicts),
            sum_components=None,
            difference=None,
        )
    if missing:
        return dict(
            status="INCOMPLETE", missing=missing, conflicts=[], sum_components=None, difference=None
        )
    total = Decimal(c["item_subtotal"]) + Decimal(c["sales_tax"]) + shipping
    total += sum(
        (Decimal(c[k]) for k in ("buyer_premium", "buyer_service_fee") if c.get(k) is not None),
        Decimal(0),
    )
    difference = Decimal(c["order_total"]) - total
    return dict(
        status="RECONCILED" if difference == 0 else "MISMATCH",
        missing=[],
        conflicts=[],
        sum_components=str(total),
        difference=str(difference),
    )


def promote_candidate(session, item_id):
    found = session.get(DiscoveredItem, item_id)
    if not found:
        raise ValueError("Candidate not found")
    if found.listing_id:
        return session.get(Listing, found.listing_id)
    item = ListingInput.model_validate(found.normalized)
    key = dedup_key(item)
    row = session.scalar(select(Listing).where(Listing.dedup_key == key))
    if not row:
        row = Listing(
            dedup_key=key,
            data=item.model_dump(mode="json"),
            original_data=found.original_data,
            review_state="RESEARCH",
        )
        session.add(row)
        session.flush()
    found.listing_id = row.id
    return row


def confirm_query(session, observation_id, occurrence_id=None):
    row = session.get(AlertObservation, observation_id)
    cards = observation_occurrences(row) if row else []
    o = (
        next((c for c in cards if c.data.get("occurrence_id") == occurrence_id), None)
        if occurrence_id
        else (cards[0] if cards else None)
    )
    if not o or not o.query_text:
        raise ValueError("No explicit saved-search phrase is available to confirm")
    message = session.get(AlertMessage, o.message_id)
    if message.message_type != MessageType.SEARCH_MATCH:
        raise ValueError("Promotional/lifecycle messages cannot receive saved-search attribution")
    if o.data.get("message_role") == "RECOMMENDED":
        raise ValueError("Recommendations cannot receive saved-search attribution")
    p = pattern_for(session, o.query_text, True)
    o.attribution_verified = True
    o.data = {**o.data, "attribution_confirmed_at": utcnow().isoformat()}
    if row.data.get("card_occurrences"):
        row.data = {
            **row.data,
            "card_occurrences": [
                dict(
                    query_text=c.query_text,
                    attribution_verified=c.attribution_verified,
                    data=c.data,
                )
                for c in cards
            ],
        }
        if o is cards[0]:
            row.attribution_verified = True
    item = session.get(DiscoveredItem, o.item_id)
    if item.normalized.get("raw_search_phrase") == o.query_text:
        item.normalized = {
            **item.normalized,
            "search_pattern_id": p.id,
            "query_attribution_verified": True,
        }
        if item.listing_id:
            listing = session.get(Listing, item.listing_id)
            if listing.data.get("raw_search_phrase") == o.query_text:
                listing.data = {
                    **listing.data,
                    "search_pattern_id": p.id,
                    "query_attribution_verified": True,
                }


class PaidCosts(BaseModel):
    item_price: Money
    buyer_premium: Money
    buyer_service_fee: Money
    sales_tax: Money
    shipping_cost: Money
    handling_fee: Money
    shipping_status: Shipping = Shipping.UNKNOWN


def confirm_paid_order(session, invoice_id, costs: PaidCosts):
    invoice = session.get(AlertInvoice, invoice_id)
    if not invoice or invoice.data.get("status") != "RECONCILED":
        raise ValueError("A reconciled paid receipt is required")
    if invoice.confirmed_at:
        return session.get(Listing, invoice.listing_id)
    if len(invoice.data["item_ids"]) != 1:
        raise ValueError(
            "Multi-item/unidentified invoices need manual allocation; no equal split is made"
        )
    quoted = invoice.data["components"]
    total = sum((getattr(costs, k) for k in COSTS), Decimal(0))
    if total != Decimal(quoted["order_total"]):
        raise ValueError("Entered costs do not reconcile to the stated paid order total")
    if costs.item_price != Decimal(quoted["item_subtotal"]) or costs.sales_tax != Decimal(
        quoted["sales_tax"]
    ):
        raise ValueError("Entered item subtotal/tax must match the receipt")
    if quoted.get("shipping_handling") is not None:
        if costs.shipping_cost + costs.handling_fee != Decimal(quoted["shipping_handling"]):
            raise ValueError("Shipping/handling allocation must match the combined receipt amount")
    for key in ("shipping_cost", "handling_fee", "buyer_premium", "buyer_service_fee"):
        if quoted.get(key) is not None and getattr(costs, key) != Decimal(quoted[key]):
            raise ValueError("Entered component differs from the receipt")
    row = promote_candidate(session, invoice.data["item_ids"][0])
    before = dict(row.data)
    updated = {
        **before,
        **costs.model_dump(mode="json"),
        "cost_status": {**before.get("cost_status", {}), **{k: "CONFIRMED" for k in COSTS}},
    }
    row.data = ListingInput.model_validate(updated).model_dump(mode="json")
    session.add(ListingEdit(listing_id=row.id, before_data=before, after_data=row.data))
    if not session.scalar(
        select(Feedback).where(Feedback.listing_id == row.id, Feedback.action == "Purchased")
    ):
        session.add(
            Feedback(
                listing_id=row.id,
                action="Purchased",
                note=f"Owner confirmed paid order evidence #{invoice.id}",
            )
        )
    row.review_state = "PURCHASED"
    invoice.listing_id = row.id
    invoice.confirmed_at = utcnow()
    return row


def import_structured(session, source_id, content, format_, verified_query=False):
    from radar.intake.parsers import Candidate

    _, definition = source_definition(session, source_id)
    rows = parse_import(content, format_)
    stats = dict(
        messages=len(rows),
        new_messages=0,
        duplicate_messages=0,
        new_candidates=0,
        duplicate_candidates=0,
        observations=0,
        needs_review=0,
        message_ids=[],
    )
    if len(rows) > 100:
        raise ValueError("Structured inbox imports are limited to 100 rows")
    for item, raw in rows:
        if not item.source_listing_id and not item.listing_url:
            raise ValueError(
                "Inbox identities require a source item ID or direct URL; no content merging"
            )
        if not item.source_listing_id and item.listing_url:
            from urllib.parse import urlsplit

            url = urlsplit(str(item.listing_url))
            if url.query or url.path in ("", "/"):
                raise ValueError(
                    "Supply a stable item ID for query-addressed or ambiguous listing URLs"
                )
        if item.source.casefold() != definition.name.casefold():
            raise ValueError("Structured source must match the selected registry source")
        if item.listing_url:
            from radar.intake.parsers import safe_link

            clean_url = safe_link(str(item.listing_url), definition)
            if not clean_url:
                raise ValueError("Listing URL must be a safe direct source URL")
            item = ListingInput.model_validate(
                {**item.model_dump(mode="json"), "listing_url": clean_url}
            )
        item = item.model_copy(
            update={
                "source": definition.name + (" FIXTURE" if item.synthetic else ""),
                "query_attribution_verified": bool(verified_query and item.raw_search_phrase),
            }
        )
        key = digest(source_id + "|structured|" + json.dumps(raw, sort_keys=True))
        existing = session.scalar(select(AlertMessage).where(AlertMessage.digest == key))
        if existing:
            existing.import_count += 1
            existing.last_imported_at = utcnow()
            stats["duplicate_messages"] += 1
            continue
        text = json.dumps(raw, indent=2)
        message = AlertMessage(
            source_id=source_id,
            digest=key,
            raw_b64=base64.b64encode(text.encode()).decode(),
            safe_text=text,
            subject="Owner structured import",
            message_type=MessageType.SEARCH_MATCH
            if item.raw_search_phrase
            else MessageType.UNKNOWN,
            data={
                "channel": format_,
                "status": "OWNER_STRUCTURED",
                "catalogs": [],
                "source_name": item.source,
            },
            synthetic=item.synthetic,
        )
        session.add(message)
        session.flush()
        _, new = add_candidate(
            session,
            message,
            Candidate(
                item=item,
                evidence={"channel": format_},
                query=item.raw_search_phrase,
                query_supported=bool(item.raw_search_phrase),
            ),
            verified_query,
        )
        stats["new_messages"] += 1
        stats["new_candidates"] += int(new)
        stats["duplicate_candidates"] += int(not new)
        stats["observations"] += 1
        stats["message_ids"].append(message.id)
    session.get(IntakeSource, source_id).last_successful_import = utcnow()
    batch = AlertImport(source_id=source_id, stats=stats)
    session.add(batch)
    session.flush()
    return batch
