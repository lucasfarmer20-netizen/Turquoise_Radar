"""Manually recorded comparable evidence, separate from acquired inventory."""

import csv
import io
import json
from collections import defaultdict
from decimal import Decimal

from sqlalchemy import select

from radar.db import ComparableSale, Feedback, Listing
from radar.schemas import ComparableInput


def add_comparables(session, content, format_="json"):
    if format_ not in ("json", "csv"):
        raise ValueError("Comparable format must be json or csv")
    rows = json.loads(content) if format_ == "json" else list(csv.DictReader(io.StringIO(content)))
    if not isinstance(rows, list) or len(rows) > 1000:
        raise ValueError("Use a list with at most 1000 comparable sales")
    inputs = [
        ComparableInput.model_validate({k: v for k, v in row.items() if v != ""}) for row in rows
    ]
    for entry in inputs:
        if entry.listing_id is not None:
            listing = session.get(Listing, entry.listing_id)
            purchased = session.scalar(
                select(Feedback).where(
                    Feedback.listing_id == entry.listing_id, Feedback.action == "Purchased"
                )
            )
            if not listing or not purchased:
                raise ValueError("Linked listing must have a recorded Purchased decision")
        session.add(ComparableSale(data=entry.model_dump(mode="json"), listing_id=entry.listing_id))
    session.flush()
    return len(inputs)


def summaries(rows):
    groups = defaultdict(list)
    for row in rows:
        groups[(row.data["category"], row.data.get("synthetic", False))].append(row)
    return [
        dict(
            category=category,
            synthetic=synthetic,
            count=len(group),
            low=min(Decimal(r.data["sale_price"]) for r in group),
            high=max(Decimal(r.data["sale_price"]) for r in group),
            uncertainty="Small, manually selected sample; condition, size and fees may differ",
            evidence=[r.data["evidence"] for r in group],
        )
        for (category, synthetic), group in sorted(groups.items())
    ]
