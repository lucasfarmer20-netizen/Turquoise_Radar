"""Review-state projection and source-separated attribution statistics."""

from collections import defaultdict
from decimal import Decimal

from sqlalchemy import select

from radar.db import (
    DiscoveredItem,
    DiscoveryObservation,
    Feedback,
    Listing,
    SearchExecution,
    SearchPattern,
)

STATES = ["NEW", "RESEARCH", "WATCH", "BUY CANDIDATE", "PURCHASED", "PASSED"]


def queue_state(row, analysis):
    if row.review_state in ("NEW", "WATCH", "PURCHASED", "PASSED"):
        return row.review_state
    return "BUY CANDIDATE" if analysis.tier == "BUY CANDIDATE" else "RESEARCH"


def pattern_effectiveness(session):
    patterns = {p.id: p for p in session.scalars(select(SearchPattern))}
    decisions = defaultdict(list)
    for feedback in session.scalars(select(Feedback).order_by(Feedback.id)):
        decisions[feedback.listing_id].append(feedback)
    groups = {}
    linked = defaultdict(list)
    for observation, execution, item in session.execute(
        select(DiscoveryObservation, SearchExecution, DiscoveredItem)
        .join(SearchExecution, SearchExecution.id == DiscoveryObservation.execution_id)
        .join(DiscoveredItem, DiscoveredItem.id == DiscoveryObservation.item_id)
        .where(DiscoveredItem.listing_id.is_not(None))
    ):
        linked[item.listing_id].append(
            (
                execution.pattern_id,
                execution.source,
                execution.transmitted_query,
                observation.correction_state,
                execution.mode != "production",
            )
        )
    for row in session.scalars(select(Listing)):
        evidence = list(linked[row.id])
        if not evidence:
            corrected = row.data.get("effective_query")
            state = (
                "CORRECTED"
                if corrected is not None and corrected != row.data.get("raw_search_phrase")
                else "UNKNOWN"
            )
            evidence = [
                (
                    row.data.get("search_pattern_id"),
                    row.data["source"],
                    row.data.get("raw_search_phrase"),
                    state,
                    row.data.get("synthetic", False),
                )
            ]
        counted = set()
        for pattern_id, source, query, state, non_live in evidence:
            p = patterns.get(pattern_id)
            if not p:
                continue
            actual = query == p.phrase
            kind = (
                "INFERRED"
                if not actual
                else "NON-LIVE QUERY"
                if non_live
                else "SOURCE CORRECTED"
                if state == "CORRECTED"
                else "SOURCE QUERY"
            )
            key = (p.id, source, kind)
            if key in counted:
                continue
            counted.add(key)
            g = groups.setdefault(
                key,
                dict(
                    pattern=p,
                    source=source,
                    kind=kind,
                    matches=0,
                    useful=0,
                    purchased=0,
                    false_positives=0,
                    realized_profit=Decimal(0),
                    profit_records=0,
                ),
            )
            g["matches"] += 1
            history = decisions[row.id]
            g["useful"] += int(any(f.action == "Interested" for f in history))
            g["purchased"] += int(any(f.action == "Purchased" for f in history))
            g["false_positives"] += int(any(f.action == "False positive" for f in history))
            latest_sold = next(
                (
                    f
                    for f in reversed(history)
                    if f.action == "Sold" and f.realized_profit is not None
                ),
                None,
            )
            if latest_sold:
                g["realized_profit"] += Decimal(latest_sold.realized_profit)
                g["profit_records"] += 1
    for g in groups.values():
        g["conversion"] = Decimal(g["purchased"]) / g["matches"] if g["matches"] else None
        # Inferred matches cannot contribute to a query's retrieval effectiveness.
        g["priority"] = g["pattern"].priority_weight + (
            min(15, g["useful"] * 2 + g["purchased"] * 3)
            + max(-5, min(5, int(g["realized_profit"] / 20)))
            if g["kind"] == "SOURCE QUERY"
            else 0
        )
    return sorted(groups.values(), key=lambda g: (-g["priority"], g["source"], g["kind"]))


def attributed_patterns(session):
    """Preserve legacy counters on disk, but never rank queries using inferred success."""
    from types import SimpleNamespace

    groups = pattern_effectiveness(session)
    result = []
    for p in session.scalars(select(SearchPattern)):
        actual = [g for g in groups if g["pattern"].id == p.id and g["kind"] == "SOURCE QUERY"]
        data = {col.name: getattr(p, col.name) for col in SearchPattern.__table__.columns}
        data.update(
            appearances=sum(g["matches"] for g in actual),
            useful_matches=sum(g["useful"] for g in actual),
            successful_purchases=sum(g["purchased"] for g in actual),
            realized_profit=str(sum((g["realized_profit"] for g in actual), Decimal(0))),
        )
        result.append(SimpleNamespace(**data))
    return result
