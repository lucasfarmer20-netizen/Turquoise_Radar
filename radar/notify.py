"""Push a digest of new high-value finds (and Interested auctions ending soon) after each cycle.

Channels, enabled by presence of their setting in .env:
    RADAR_SLACK_WEBHOOK=https://hooks.slack.com/services/...   Slack incoming webhook
    RADAR_NTFY_TOPIC=some-unguessable-topic                     ntfy.sh (or RADAR_NTFY_SERVER)
Tuning:
    RADAR_NOTIFY_MIN_SCORE=75        new finds must score at least this
    RADAR_NOTIFY_ENDING_HOURS=2      Interested auctions ending within this many hours
    RADAR_PUBLIC_URL=http://radar:8000   base for links back to /radar (Tailscale name)

Each item is notified once; the flag lives in TriageResult.data["notified_at"] (finds) and
DiscoveredItem.normalized["ending_notified_at"] (ending-soon). A quiet cycle sends nothing.

    python -m radar.notify test     # send a test message to configured channels
    python -m radar.notify run      # send whatever is pending now
"""

import argparse
import asyncio
import logging
import os
import sys
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import select

from radar.db import DiscoveredItem, TriageResult, database
from radar.queue import in_queue, offer_target, score_floor
from radar.queue import settings as queue_settings

log = logging.getLogger("radar.notify")


def settings():
    return dict(
        slack=os.environ.get("RADAR_SLACK_WEBHOOK", "").strip(),
        ntfy_topic=os.environ.get("RADAR_NTFY_TOPIC", "").strip(),
        ntfy_server=os.environ.get("RADAR_NTFY_SERVER", "https://ntfy.sh").rstrip("/"),
        min_score=int(
            os.environ.get("RADAR_NOTIFY_MIN_SCORE", os.environ.get("RADAR_QUEUE_MIN_SCORE", "80"))
        ),
        max_items=int(os.environ.get("RADAR_NOTIFY_MAX_ITEMS", "10")),
        ending_hours=float(os.environ.get("RADAR_NOTIFY_ENDING_HOURS", "2")),
        auction_hours=float(os.environ.get("RADAR_NOTIFY_AUCTION_HOURS", "1")),
        base_url=os.environ.get("RADAR_PUBLIC_URL", "http://radar:8000").rstrip("/"),
    )


def configured(cfg):
    return bool(cfg["slack"] or cfg["ntfy_topic"])


# ---------------------------------------------------------------- selection


def _parse(ts):
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def pending_finds(session, cfg):
    rows = session.execute(
        select(TriageResult, DiscoveredItem)
        .join(DiscoveredItem, DiscoveredItem.id == TriageResult.item_id)
        .where(TriageResult.status == "OK")
        .where(TriageResult.relevant.is_(True))
        .where(score_floor(cfg["min_score"]))
        .where(DiscoveredItem.inbox_status == "NEW")
    ).all()
    qcfg = {**queue_settings(), "min_score": cfg["min_score"]}
    now = datetime.now(UTC)
    out = []
    for t, i in rows:
        if t.data.get("notified_at") or not in_queue(i, t, now, qcfg):
            continue
        n = i.normalized or {}
        if "AUCTION" in (n.get("buying_options") or []) and not n.get("buy_it_now_price"):
            # A plain auction is only worth an alert in its last hour, at the bid it has
            # then. Until that point it waits (un-notified) and is re-judged each check.
            end = _parse(n.get("item_end_time"))
            if end is None or end > now + timedelta(hours=cfg.get("auction_hours", 1)):
                continue
        out.append((t, i))
    return sorted(out, key=lambda ti: (-int(_buy_now(ti[1])), -(ti[0].score or 0)))


def pending_ending(session, cfg, now):
    horizon = now + timedelta(hours=cfg["ending_hours"])
    rows = session.scalars(
        select(DiscoveredItem).where(DiscoveredItem.inbox_status == "INTERESTED")
    ).all()
    out = []
    for i in rows:
        n = i.normalized or {}
        if "AUCTION" not in (n.get("buying_options") or []) or n.get("ending_notified_at"):
            continue
        end = _parse(n.get("item_end_time"))
        if end and now < end <= horizon:
            out.append((i, end))
    return sorted(out, key=lambda x: x[1])


# ---------------------------------------------------------------- formatting


def _buy_now(i):
    n = i.normalized or {}
    opts = n.get("buying_options") or []
    return bool(n.get("buy_it_now_price")) or ("FIXED_PRICE" in opts and "AUCTION" not in opts)


def _usd(v):
    try:
        return f"${float(v):,.2f}"
    except (TypeError, ValueError):
        return "?"


def _find_line(t, i, cfg, slack):
    n = i.normalized or {}
    d = t.data
    title = (n.get("raw_title") or "(untitled)")[:80]
    landed = d.get("landed_usd")
    price = n.get("buy_it_now_price") or n.get("item_price")
    kind = (
        "BIN"
        if n.get("buy_it_now_price") or "AUCTION" not in (n.get("buying_options") or [])
        else "bid"
    )
    resale = d.get("resale_estimate_usd")
    resale_s = f"resale ~${resale[0]}–{resale[1]}" if resale else "resale ?"
    card = f"{cfg['base_url']}/radar?show=all&status=all#item-{i.id}"
    ebay = n.get("listing_url") or ""
    from radar.triage import condition_flags, live_verdict

    offer = offer_target(n, live_verdict(d, n))
    tag = f"OFFER ≤ ${offer} · " if offer else ("BUY NOW · " if _buy_now(i) else "")
    flags = condition_flags(n) or d.get("condition_flags") or []
    if flags:
        tag += f"CONDITION ({flags[0]}) · "
    if slack:
        head = f"*{t.score}* · {tag}<{ebay}|{title}>"
        tail = f"<{card}|card>"
    else:
        head = f"[{t.score}] {tag}{title}"
        tail = ebay
    return (
        f"{head}\n"
        f"    {_usd(price)} {kind} → {_usd(landed)} landed · {resale_s} · "
        f"{(d.get('category') or '').replace('_', ' ').title()}\n"
        f"    {d.get('reason', '')}\n"
        f"    {tail}"
    )


def _ending_line(i, end, now, cfg, slack):
    n = i.normalized or {}
    mins = int((end - now).total_seconds() // 60)
    title = (n.get("raw_title") or "(untitled)")[:80]
    bid = _usd(n.get("item_price"))
    ebay = n.get("listing_url") or ""
    when = f"ends in {mins // 60}h {mins % 60:02d}m" if mins >= 60 else f"ends in {mins}m"
    return f"{'<' + ebay + '|' + title + '>' if slack else title}\n    {when} · current {bid}"


def build_message(finds, ending, now, cfg, slack):
    parts = []
    if finds:
        n = len(finds)
        plural = "s" if n != 1 else ""
        ge = "\u2265" if slack else ">="
        head = f"{n} new find{plural} (score {ge} {cfg['min_score']}, underpriced)"
        parts.append(f"*{head}*" if slack else head)
        cap = cfg.get("max_items", 10)
        parts += [_find_line(t, i, cfg, slack) for t, i in finds[:cap]]
        if len(finds) > cap:
            parts.append(f"…and {len(finds) - cap} more in the queue at {cfg['base_url']}/radar")
    if ending:
        parts.append(
            "*Interested auctions ending soon*" if slack else "Interested auctions ending soon"
        )
        parts += [_ending_line(i, e, now, cfg, slack) for i, e in ending]
    return "\n\n".join(parts)


# ---------------------------------------------------------------- delivery


async def send_slack(client, webhook, text):
    r = await client.post(
        webhook, json={"text": text, "unfurl_links": False, "unfurl_media": False}
    )
    r.raise_for_status()


async def send_ntfy(client, server, topic, title, text, priority="default", click=None):
    headers = {"Title": title, "Priority": priority, "Markdown": "no"}
    if click:
        headers["Click"] = click
    r = await client.post(f"{server}/{topic}", content=text.encode("utf-8"), headers=headers)
    r.raise_for_status()


async def deliver(cfg, slack_text, plain_text, title, click=None, priority="default"):
    sent = []
    async with httpx.AsyncClient(timeout=20) as client:
        if cfg["slack"]:
            try:
                await send_slack(client, cfg["slack"], slack_text)
                sent.append("slack")
            except Exception as e:
                log.error("slack delivery failed: %s", e)
        if cfg["ntfy_topic"]:
            try:
                await send_ntfy(
                    client,
                    cfg["ntfy_server"],
                    cfg["ntfy_topic"],
                    title,
                    plain_text,
                    priority,
                    click,
                )
                sent.append("ntfy")
            except Exception as e:
                log.error("ntfy delivery failed: %s", e)
    return sent


async def notify_pending(sessions, cfg=None):
    """Send one digest for everything pending; mark items notified only if a channel accepted it."""
    cfg = cfg or settings()
    if not configured(cfg):
        return 0
    now = datetime.now(UTC)
    with sessions() as s:
        finds = pending_finds(s, cfg)
        ending = pending_ending(s, cfg, now)
        if not finds and not ending:
            return 0
        slack_text = build_message(finds, ending, now, cfg, slack=True)
        plain_text = build_message(finds, ending, now, cfg, slack=False)
        find_ids = [t.id for t, _ in finds]
        ending_ids = [i.id for i, _ in ending]
    title = f"Radar: {len(finds)} find{'s' if len(finds) != 1 else ''}" + (
        f", {len(ending)} ending soon" if ending else ""
    )
    sent = await deliver(
        cfg,
        slack_text,
        plain_text,
        title,
        click=f"{cfg['base_url']}/radar",
        priority="high" if ending else "default",
    )
    if not sent:
        return 0
    stamp = now.isoformat()
    with sessions.begin() as s:
        for t in s.scalars(select(TriageResult).where(TriageResult.id.in_(find_ids))):
            t.data = {**t.data, "notified_at": stamp}
        for i in s.scalars(select(DiscoveredItem).where(DiscoveredItem.id.in_(ending_ids))):
            i.normalized = {**(i.normalized or {}), "ending_notified_at": stamp}
    log.info("notified %d finds, %d ending via %s", len(find_ids), len(ending_ids), "+".join(sent))
    return len(find_ids) + len(ending_ids)


def main(argv=None):
    from radar.scheduler import load_env

    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("command", choices=["test", "run"])
    parser.add_argument("--env")
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S"
    )
    load_env(args.env)
    cfg = settings()
    if not configured(cfg):
        print("no channel configured: set RADAR_SLACK_WEBHOOK and/or RADAR_NTFY_TOPIC in .env")
        return 2
    if args.command == "test":
        text = (
            "Turquoise Radar notifications are working. "
            f"Links will point at {cfg['base_url']}/radar"
        )
        sent = asyncio.run(
            deliver(cfg, text, text, "Radar: test", click=f"{cfg['base_url']}/radar")
        )
        print("sent via", "+".join(sent) if sent else "nothing (see errors above)")
        return 0 if sent else 1
    engine, sessions = database()
    try:
        n = asyncio.run(notify_pending(sessions, cfg))
        print(f"notified {n} items")
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(main())
