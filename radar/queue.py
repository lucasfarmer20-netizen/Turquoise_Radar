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

from radar.db import DiscoveredItem, TriageResult

log = logging.getLogger("radar.queue")
ARCHIVED = "ARCHIVED"


def settings():
    return dict(
        min_score=int(os.environ.get("RADAR_QUEUE_MIN_SCORE", "80")),
        stale_days=int(os.environ.get("RADAR_STALE_DAYS", "7")),
        ended_grace_minutes=int(os.environ.get("RADAR_ENDED_GRACE_MINUTES", "30")),
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
    if not triage.relevant or (triage.score or 0) < cfg["min_score"]:
        return False
    if triage.data.get("value_read") != "UNDERPRICED":
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
        if triage.data.get("value_read") == "OVERPRICED":
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
    if counts:
        log.info("archived %s", counts)
    return counts
