"""Append-only owner research and quote economics, separate from source/decision history."""

from copy import deepcopy
from decimal import ROUND_CEILING, Decimal

from radar.db import Listing, ListingEdit
from radar.schemas import ListingInput, ResearchObservation, Shipping, ValueStatus

QUOTE_FIELDS = {
    "item_price": "item_quote",
    "buyer_premium": "buyer_premium_quote",
    "sales_tax": "sales_tax_quote",
    "shipping_cost": "shipping_quote",
    "handling_fee": "handling_quote",
    "buyer_service_fee": "other_fees_quote",
}
CANONICAL_FIELDS = set(QUOTE_FIELDS) | {
    "currency",
    "cost_status",
    "shipping_status",
    "shipping_evidence",
    "carrier",
    "item_end_time",
    "stated_weight",
    "weight_unit",
    "weight_kind",
    "weight_evidence",
    "weight_verified",
    "material_evidence",
    "piece_count",
    "material_terms",
    "condition",
}
PRIORITY_WEIGHTS = {"HIGH": 30, "NORMAL": 0, "LOW": -20, "HISTORICAL": -100}
CENT = Decimal("0.01")


def _canonical(item, observation, updates):
    if not isinstance(updates, dict) or set(updates) - CANONICAL_FIELDS:
        raise ValueError("Only explicitly supported canonical research fields may change")
    values = {**item.model_dump(mode="json"), **updates}
    if isinstance(updates.get("cost_status"), dict):
        values["cost_status"] = {**item.cost_status, **updates["cost_status"]}
    candidate = ListingInput.model_validate(values)
    for key, evidence_field in QUOTE_FIELDS.items():
        if key not in updates or getattr(candidate, key) is None:
            continue
        if (
            observation.availability != "ACTIVE_AT_OBSERVATION"
            or observation.item_quote is None
            or candidate.currency != observation.currency
            or getattr(candidate, key) != getattr(observation, evidence_field)
            or candidate.cost_status.get(key) != ValueStatus.ESTIMATED
        ):
            raise ValueError("Canonical amounts require matching dated, estimated source quotes")
    if "currency" in updates and candidate.currency != item.currency:
        if observation.item_quote is None or candidate.currency != observation.currency:
            raise ValueError("Historical prices cannot relabel current acquisition currency")
        if (
            item.buyer_profile
            or item.invoice_fee_allocation is not None
            or item.invoice_allocation_confirmed
            or item.invoice_allocation_evidence
        ):
            raise ValueError(
                "Review and clear currency-dependent profile/allocation assumptions first"
            )
        # Every retained amount needs explicit evidence in the new quoted denomination.
        if any(getattr(candidate, key) is not None and key not in updates for key in QUOTE_FIELDS):
            raise ValueError("Currency change requires reviewing every retained acquisition amount")
    if "cost_status" in updates:
        statuses = updates["cost_status"]
        if (
            set(statuses) - set(QUOTE_FIELDS)
            or any(value not in ("UNKNOWN", "ESTIMATED") for value in statuses.values())
            or any(
                getattr(candidate, key) is None and value != "UNKNOWN"
                for key, value in statuses.items()
            )
        ):
            raise ValueError("Owner-report cost evidence remains estimated or unknown")
        for key, status in statuses.items():
            if (
                status == "ESTIMATED"
                and item.cost_status.get(key) != ValueStatus.ESTIMATED
                and (
                    observation.availability != "ACTIVE_AT_OBSERVATION"
                    or observation.item_quote is None
                    or candidate.currency != observation.currency
                    or getattr(candidate, key) != getattr(observation, QUOTE_FIELDS[key])
                )
            ):
                raise ValueError("Newly estimated amounts require a matching dated source quote")
    if "shipping_status" in updates:
        if candidate.shipping_status == Shipping.CONFIRMED_TO_ALASKA and not (
            observation.shipping_state == "FINAL_CONFIRMED"
            and observation.final_fulfillment_confirmed
            and candidate.shipping_evidence
        ):
            raise ValueError("A destination quote is not final Alaska fulfillment confirmation")
        if (
            candidate.shipping_status == Shipping.CONFIRMED_UNAVAILABLE
            and observation.shipping_state != "UNAVAILABLE"
        ):
            raise ValueError("Generic restrictions do not prove Alaska shipping unavailable")
    if "item_end_time" in updates and candidate.item_end_time != observation.reported_end_at:
        raise ValueError("Canonical deadline must match the explicitly reported source deadline")
    if "carrier" in updates and candidate.carrier != observation.shipping_method:
        raise ValueError("Canonical carrier must match the reported shipping method")
    if updates.get("weight_verified") or updates.get("material_evidence") not in (
        None,
        "unknown",
        "seller_claim",
    ):
        raise ValueError("Owner-reported marks or seller tests cannot verify material or weight")
    weight_fields = {
        "stated_weight",
        "weight_unit",
        "weight_kind",
        "weight_evidence",
        "weight_verified",
    }
    if weight_fields.intersection(updates) and (
        candidate.weight_kind != "gross"
        or candidate.weight_unit != "g"
        or candidate.weight_verified
        or candidate.stated_weight is not None
        and candidate.stated_weight != observation.gross_weight_g
    ):
        raise ValueError("Reported weight edits must remain matching unverified gross grams")
    return candidate


def append_research_observation(session, listing_id, observation, *, canonical_updates=None):
    """Validate completely, then append one observation/audit in the caller's transaction.

    Exact observation-ID repeats are no-ops. Changed evidence under the same ID or
    changed canonical instructions conflict; use a new observation for new evidence.
    Source identities, raw mail, original data, Feedback and review state are untouched.
    """
    observation = ResearchObservation.model_validate(observation)
    updates = canonical_updates or {}
    with session.no_autoflush:
        row = session.get(Listing, listing_id)
        if row is None:
            raise ValueError("Research listing not found")
        item = ListingInput.model_validate(row.data)
        candidate = _canonical(item, observation, updates)
        existing = next(
            (
                entry
                for entry in item.research_observations
                if entry.observation_id == observation.observation_id
            ),
            None,
        )
        if existing:
            if (
                existing.model_dump(mode="json") != observation.model_dump(mode="json")
                or candidate != item
            ):
                raise ValueError(
                    "Observation ID already has different evidence or canonical changes"
                )
            return False
        after = ListingInput.model_validate(
            {
                **candidate.model_dump(mode="json"),
                "research_observations": [*item.research_observations, observation],
            }
        ).model_dump(mode="json")
        before = deepcopy(row.data)
        row.data = after
        session.add(ListingEdit(listing_id=row.id, before_data=before, after_data=deepcopy(after)))
    return True


def _subtotal(components, base):
    if components.get(base) is None:
        return None
    return sum((value for value in components.values() if value is not None), Decimal(0)).quantize(
        CENT, rounding=ROUND_CEILING
    )


def research_projection(item, analysis=None):
    """Pure report projection: known subtotals are not complete delivered costs."""
    if not item.research_observations:
        return None
    latest = max(
        enumerate(item.research_observations), key=lambda pair: (pair[1].recorded_at, pair[0])
    )[1]
    ended = latest.availability == "ENDED"
    quoted = {key: None if ended else getattr(latest, field) for key, field in QUOTE_FIELDS.items()}
    known = _subtotal(quoted, "item_price")
    next_known = None
    if not ended and latest.next_minimum_bid is not None:
        next_known = _subtotal(quoted | {"item_price": latest.next_minimum_bid}, "item_price")
    shipping_handling = (
        quoted["shipping_cost"] + quoted["handling_fee"]
        if quoted["shipping_cost"] is not None and quoted["handling_fee"] is not None
        else None
    )
    historical = {"realized_hammer": latest.realized_hammer} | {
        key: getattr(latest, field) if ended else None
        for key, field in QUOTE_FIELDS.items()
        if key != "item_price"
    }
    currency_consistent = bool(
        latest.realized_currency
        and latest.realized_currency != "XXX"
        and latest.realized_currency == latest.currency
    )
    if (
        ended
        and historical["realized_hammer"] is not None
        and currency_consistent
        and historical["buyer_premium"] is None
        and latest.buyer_premium_rate is not None
    ):
        historical["buyer_premium"] = (latest.realized_hammer * latest.buyer_premium_rate).quantize(
            CENT, rounding=ROUND_CEILING
        )
    # A posted tax rate has no charged amount/basis; never select a BuyerProfile default.
    historical_total = _subtotal(historical, "realized_hammer") if currency_consistent else None
    item_ratio = (
        shipping_handling / quoted["item_price"]
        if shipping_handling is not None and quoted["item_price"]
        else None
    )
    subtotal_ratio = shipping_handling / known if shipping_handling is not None and known else None
    exempt = bool(
        analysis
        and item.currency == latest.currency == "USD"
        and all(
            getattr(item, key) == value and item.cost_status.get(key) != ValueStatus.UNKNOWN
            for key, value in quoted.items()
            if value is not None
        )
        and (analysis.tier == "BUY CANDIDATE" or analysis.melt.eligible)
    )
    penalty = (
        10 if subtotal_ratio is not None and subtotal_ratio >= Decimal("0.50") and not exempt else 0
    )
    priority = "HISTORICAL" if ended else latest.priority
    return dict(
        latest=latest,
        quoted_components=quoted,
        known_subtotal=known,
        next_bid_known_subtotal=next_known,
        shipping_handling=shipping_handling,
        shipping_item_ratio=item_ratio,
        shipping_subtotal_ratio=subtotal_ratio,
        historical_components=historical,
        historical_subtotal=historical_total,
        missing_costs=[key for key, value in quoted.items() if value is None],
        historical_missing_costs=[key for key, value in historical.items() if value is None],
        priority=priority,
        priority_weight=PRIORITY_WEIGHTS[priority],
        burden_penalty=penalty,
        burden_exempt=exempt,
        availability="ENDED" if ended else latest.availability,
    )
