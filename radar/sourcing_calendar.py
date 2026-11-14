"""Sales that happen on a date instead of arriving as listings: state unclaimed-property
auctions, regional one-off sales. Read from fixtures/sourcing_calendar.json (or
RADAR_CALENDAR_FILE). The page lists what is coming; the scheduler posts one Slack reminder
per event on its remind_on date.

Entries are reminders to look, not verified schedules: each carries the wording of when the
sale usually runs and a link to the auctioneer.
"""

import json
import logging
import os
from datetime import UTC, date, datetime
from pathlib import Path

from radar.db import Setting

log = logging.getLogger("radar.calendar")
SENT_SETTING_ID = 95


def path():
    return Path(os.environ.get("RADAR_CALENDAR_FILE", "fixtures/sourcing_calendar.json"))


def _day(value):
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def events():
    try:
        raw = json.loads(path().read_text(encoding="utf-8")).get("events") or []
    except (OSError, ValueError):
        return []
    out = []
    for e in raw:
        if not isinstance(e, dict) or not e.get("id") or not e.get("name"):
            continue
        url = str(e.get("url") or "")
        out.append(
            dict(
                id=str(e["id"]),
                name=str(e["name"])[:160],
                where=str(e.get("where") or "")[:160],
                url=url if url.startswith("https://") else "",
                when=str(e.get("when") or "")[:120],
                note=str(e.get("note") or "")[:300],
                ends=_day(e.get("ends")),
                remind_on=_day(e.get("remind_on")),
            )
        )
    return out


def upcoming(today=None, horizon_days=120):
    """Events not yet over whose reminder date falls within the horizon, soonest first."""
    today = today or datetime.now(UTC).date()
    out = []
    for e in events():
        if e["ends"] and e["ends"] < today:
            continue
        anchor = e["remind_on"] or e["ends"]
        if anchor and (anchor - today).days > horizon_days:
            continue
        out.append({**e, "live": bool(e["remind_on"] and e["remind_on"] <= today)})
    return sorted(out, key=lambda e: e["remind_on"] or e["ends"] or today)


def due(sessions, today=None):
    """Events whose reminder date has arrived, that are not over and not yet announced."""
    today = today or datetime.now(UTC).date()
    with sessions() as s:
        row = s.get(Setting, SENT_SETTING_ID)
        sent = set((row.data or {}).get("sent", [])) if row else set()
    return [
        e
        for e in events()
        if e["id"] not in sent
        and e["remind_on"]
        and e["remind_on"] <= today
        and not (e["ends"] and e["ends"] < today)
    ]


def mark_sent(sessions, ids):
    with sessions.begin() as s:
        row = s.get(Setting, SENT_SETTING_ID)
        sent = sorted(set((row.data or {}).get("sent", []) if row else []) | set(ids))
        if row:
            row.data = {"sent": sent}
        else:
            s.add(Setting(id=SENT_SETTING_ID, data={"sent": sent}))


def message(due_events, slack=True):
    lines = ["*Sourcing calendar*" if slack else "Sourcing calendar"]
    for e in due_events:
        name = f"<{e['url']}|{e['name']}>" if slack and e["url"] else e["name"]
        lines.append(f"{name}\n    {e['when']} · {e['where']}\n    {e['note']}")
    return "\n\n".join(lines)


async def remind(sessions):
    """Send one reminder for everything due. Returns how many events were announced."""
    from radar.notify import configured, deliver, settings

    cfg = settings()
    pending = due(sessions)
    if not pending or not configured(cfg):
        return 0
    sent = await deliver(
        cfg,
        message(pending, slack=True),
        message(pending, slack=False),
        "Radar: sourcing calendar",
        click=f"{cfg['base_url']}/radar",
    )
    if sent:
        mark_sent(sessions, [e["id"] for e in pending])
        log.info("calendar: reminded about %d event(s)", len(pending))
        return len(pending)
    return 0
