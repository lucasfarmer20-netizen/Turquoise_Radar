"""The owner's working queue and the auto-archive sweep.

Queue = NEW items that are screened relevant, read UNDERPRICED, score >= RADAR_QUEUE_MIN_SCORE
(default 80) and not ended. The sweep archives, with a reason, items that can no longer be
worth a look: screened irrelevant, read OVERPRICED, auctions ended, and Buy-It-Now listings
older than RADAR_STALE_DAYS (7) with no decision. Archived items keep their data and can be
restored with Undo on /radar.
"""

import logging
import os
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from radar.db import (
    AlertObservation,
    DiscoveredItem,
    DiscoveryObservation,
    TriageResult,
    VisionCase,
)

log = logging.getLogger("radar.queue")
ARCHIVED = "ARCHIVED"
ENDED = "ENDED"  # was Interested when the listing ended


def settings():
    return dict(
        min_score=int(os.environ.get("RADAR_QUEUE_MIN_SCORE", "80")),
        stale_days=int(os.environ.get("RADAR_STALE_DAYS", "7")),
        ended_grace_minutes=int(os.environ.get("RADAR_ENDED_GRACE_MINUTES", "30")),
        purge_days=int(os.environ.get("RADAR_PURGE_DAYS", "14")),
        offer_max_discount=float(os.environ.get("RADAR_OFFER_MAX_DISCOUNT", "0.30")),
        hero_min_score=int(os.environ.get("RADAR_HERO_MIN_SCORE", "60")),
    )


def hero_notes(n, data):
    """Why a listing counts as a statement / hero piece: the model's notes when it flagged
    one, plus hero materials the seller names. Empty list when it is not one."""
    from radar.triage import hero_signals

    notes = list((data or {}).get("statement_notes") or []) if (data or {}).get("statement") else []
    for term in hero_signals(n):
        if not any(term in note.casefold() for note in notes):
            notes.append(term)
    if (data or {}).get("statement") and not notes:
        notes = ["statement piece"]
    return notes


def is_contemporary(data):
    data = data or {}
    return data.get("era") == "CONTEMPORARY" or str(data.get("category") or "").startswith(
        "CONTEMPORARY"
    )


def in_hero(item, triage, now, cfg):
    """True when an item belongs on the Statement / hero tab: a relevant statement piece that
    is not priced past the walk-away (or takes offers that could get it there). The bar is the
    piece, not the bargain, so FAIR-priced pieces are shown and the score floor is lower."""
    if item.inbox_status != "NEW" or triage is None or triage.status != "OK":
        return False
    if not triage.relevant:
        return False
    n = item.normalized or {}
    if not hero_notes(n, triage.data):
        return False
    from radar.triage import landed_cost, live_verdict

    live = live_verdict(triage.data, n)
    if (live.get("model_score") or live.get("score") or 0) < cfg["hero_min_score"]:
        return False
    if (
        landed_cost(n) is not None
        and live.get("value_read") == "OVERPRICED"
        and offer_target(n, live, cfg) is None
    ):
        return False
    end = _end(n)
    return not (end and end < now)


def hero_candidates():
    """SQL condition narrowing to rows that could be hero pieces, so the tab does not load
    every listing: flagged by the model, or naming a hero material."""
    from sqlalchemy import String, cast, func, or_

    from radar.triage import HERO_TERMS

    text = func.lower(cast(DiscoveredItem.normalized, String))
    return or_(
        DiscoveredItem.id.in_(
            select(TriageResult.item_id).where(
                func.json_extract(TriageResult.data, "$.statement").in_((1, True))
            )
        ),
        *[text.like(f"%{term}%") for term in HERO_TERMS],
    )


def offer_target(n, live, cfg=None):
    """The offer to make on a fixed-price listing that accepts Best Offer and is priced above
    the walk-away: the walk-away price (before shipping), in whole dollars, when that is no
    more than offer_max_discount below asking. None when no offer applies. Whether a seller
    accepts is unknown; this only says what the piece is worth to the owner."""
    from decimal import ROUND_DOWN, Decimal, InvalidOperation

    opts = n.get("buying_options") or []
    if "BEST_OFFER" not in opts:
        return None
    asking = n.get("buy_it_now_price") or (n.get("item_price") if "AUCTION" not in opts else None)
    wa = (live or {}).get("walk_away_landed_usd")
    if asking in (None, "") or wa in (None, ""):
        return None
    try:
        asking = Decimal(str(asking))
        offer = Decimal(str(wa)) - Decimal(str(n.get("shipping_cost") or 0))
    except (InvalidOperation, TypeError):
        return None
    offer = offer.quantize(Decimal(1), rounding=ROUND_DOWN)
    discount = (cfg or settings())["offer_max_discount"]
    if offer <= 0 or offer >= asking or offer < asking * (1 - Decimal(str(discount))):
        return None
    return offer


def score_floor(min_score):
    """SQL condition: the model's own score (before a price-based cap) clears the bar. Rows
    that could be queued, either outright or as an offer candidate."""
    from sqlalchemy import func

    return (
        func.coalesce(func.json_extract(TriageResult.data, "$.model_score"), TriageResult.score)
        >= min_score
    )


def _end(n):
    try:
        v = n.get("item_end_time")
        if not v:
            return None
        d = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=UTC)
    except ValueError:
        return None


def _aware(d):
    return d if d is None or d.tzinfo else d.replace(tzinfo=UTC)


def in_queue(item, triage, now, cfg):
    """True when an item belongs in the owner's queue."""
    if item.inbox_status != "NEW" or triage is None or triage.status != "OK":
        return False
    from radar.triage import live_verdict

    n = item.normalized or {}
    live = live_verdict(triage.data, n)
    if not triage.relevant:
        return False
    from radar.triage import landed_cost

    # Auction-house alerts often carry no price at all. Those lots are judged on the piece
    # alone; the walk-away on the card is the owner's max bid.
    priced = landed_cost(n) is not None
    if priced and live.get("value_read") != "UNDERPRICED":
        # Priced above the walk-away, but the seller takes offers and the walk-away is a
        # realistic one: judge the piece on the model's score before the price cap.
        if offer_target(n, live, cfg) is None:
            return False
        if (live.get("model_score") or live.get("score") or 0) < cfg["min_score"]:
            return False
    elif (live.get("score") or 0) < cfg["min_score"]:
        return False
    end = _end(item.normalized or {})
    return not (end and end < now)


def archive_reason(item, triage, now, cfg):
    """Why an item should leave the active set, or None."""
    n = item.normalized or {}
    end = _end(n)
    if end and end < now - timedelta(minutes=cfg["ended_grace_minutes"]):
        return "ended"
    if triage is not None and triage.status == "OK":
        if triage.relevant is False:
            return "irrelevant"
        from radar.triage import live_verdict

        live = live_verdict(triage.data, n)
        if live.get("value_read") == "OVERPRICED" and offer_target(n, live, cfg) is None:
            return "overpriced"
    is_auction = "AUCTION" in (n.get("buying_options") or [])
    first = _aware(item.first_seen)
    if not is_auction and first and first < now - timedelta(days=cfg["stale_days"]):
        return "stale"
    return None


def sweep(sessions, cfg=None, now=None):
    """Archive NEW items that no longer deserve attention. Returns {reason: count}."""
    cfg = cfg or settings()
    now = now or datetime.now(UTC)
    counts = {}
    with sessions.begin() as s:
        rows = s.execute(
            select(DiscoveredItem, TriageResult)
            .outerjoin(TriageResult, TriageResult.item_id == DiscoveredItem.id)
            .where(DiscoveredItem.inbox_status == "NEW")
        ).all()
        for item, triage in rows:
            reason = archive_reason(item, triage, now, cfg)
            if reason:
                item.inbox_status = ARCHIVED
                item.normalized = {
                    **(item.normalized or {}),
                    "archive_reason": reason,
                    "archived_at": now.isoformat(),
                }
                counts[reason] = counts.get(reason, 0) + 1
        # Interested listings that have ended leave the Interested list but are kept, under
        # their own status, so a win can still be recorded against them.
        grace = timedelta(minutes=cfg["ended_grace_minutes"])
        for item in s.scalars(
            select(DiscoveredItem).where(DiscoveredItem.inbox_status == "INTERESTED")
        ):
            end = _end(item.normalized or {})
            if end and end < now - grace:
                item.inbox_status = ENDED
                item.normalized = {**(item.normalized or {}), "ended_at": now.isoformat()}
                counts["interested ended"] = counts.get("interested ended", 0) + 1
    if counts:
        log.info("archived %s", counts)
    return counts


def purge(sessions, cfg=None, now=None):
    """Delete listings that are over. Never touches INTERESTED items or anything with a
    vision case. Removes: items whose listing ended more than purge_days ago (any status),
    and ARCHIVED/IRRELEVANT items archived or last seen more than purge_days ago.
    Returns {reason: count}. Set RADAR_PURGE_DAYS=0 to disable."""
    cfg = cfg or settings()
    if cfg["purge_days"] <= 0:
        return {}
    now = now or datetime.now(UTC)
    cutoff = now - timedelta(days=cfg["purge_days"])
    counts = {}
    with sessions.begin() as s:
        keep = {cid for cid in s.scalars(select(VisionCase.candidate_id)) if cid is not None}
        victims = []
        for item in s.scalars(
            select(DiscoveredItem).where(DiscoveredItem.inbox_status != "INTERESTED")
        ):
            if item.id in keep:
                continue
            n = item.normalized or {}
            if n.get("bought_for") not in (None, ""):
                continue  # purchase history is kept
            end = _end(n)
            reason = None
            if end and end < cutoff:
                reason = "ended"
            elif item.inbox_status in (ARCHIVED, "IRRELEVANT"):
                archived_at = _end({"item_end_time": n.get("archived_at")})
                stamp = archived_at or _aware(item.last_seen) or _aware(item.first_seen)
                if stamp and stamp < cutoff:
                    reason = "archived"
            if reason:
                victims.append((item, reason))
        for item, reason in victims:
            for model in (TriageResult, DiscoveryObservation, AlertObservation):
                for row in s.scalars(select(model).where(model.item_id == item.id)):
                    s.delete(row)
            s.delete(item)
            counts[reason] = counts.get(reason, 0) + 1
    if counts:
        log.info("purged %s", counts)
    return counts


def main(argv=None):
    import argparse
    import logging as _logging

    from radar.db import database
    from radar.scheduler import load_env

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["sweep", "purge", "status"])
    parser.add_argument("--days", type=int, help="override RADAR_PURGE_DAYS for this run")
    parser.add_argument("--env")
    args = parser.parse_args(argv)
    _logging.basicConfig(
        level=_logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S"
    )
    load_env(args.env)
    cfg = settings()
    if args.days is not None:
        cfg["purge_days"] = args.days
    engine, sessions = database()
    try:
        if args.command == "sweep":
            print(sweep(sessions, cfg) or "nothing to archive")
        elif args.command == "purge":
            print(purge(sessions, cfg) or "nothing to purge")
        else:
            with sessions() as s:
                from sqlalchemy import func

                rows = s.execute(
                    select(DiscoveredItem.inbox_status, func.count()).group_by(
                        DiscoveredItem.inbox_status
                    )
                ).all()
                print(dict(rows), f"purge after {cfg['purge_days']} days")
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
