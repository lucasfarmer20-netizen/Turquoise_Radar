"""Explicit owner decisions using the existing feedback history and milestones."""

from decimal import Decimal

from sqlalchemy import select

from radar.db import Feedback, SearchPattern


def record_feedback(session, listing, data):
    prior = list(
        session.scalars(
            select(Feedback).where(Feedback.listing_id == listing.id).order_by(Feedback.id)
        )
    )
    pattern = session.get(SearchPattern, listing.data.get("search_pattern_id") or "")
    if (
        pattern
        and listing.data.get("raw_search_phrase") == pattern.phrase
        and listing.data.get("query_attribution_verified") is not False
    ):
        if data.action == "Interested" and not any(f.action == "Interested" for f in prior):
            pattern.useful_matches += 1
        if data.action == "Purchased" and not any(f.action == "Purchased" for f in prior):
            pattern.successful_purchases += 1
        if data.action == "Sold" and data.realized_profit is not None:
            previous = next(
                (
                    Decimal(f.realized_profit)
                    for f in reversed(prior)
                    if f.action == "Sold" and f.realized_profit is not None
                ),
                Decimal(0),
            )
            pattern.realized_profit = str(
                Decimal(pattern.realized_profit) + data.realized_profit - previous
            )
    if data.action in ("Purchased", "Sold"):
        listing.review_state = "PURCHASED"
    elif data.action in ("Passed", "False positive"):
        listing.review_state = "PASSED"
    elif data.action == "Interested":
        listing.review_state = "WATCH"
    feedback = Feedback(
        listing_id=listing.id,
        action=data.action,
        note=data.note,
        realized_profit=str(data.realized_profit) if data.realized_profit is not None else None,
    )
    session.add(feedback)
    return feedback
