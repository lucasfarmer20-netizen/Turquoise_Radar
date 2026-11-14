"""Plain results page for scheduler finds: /radar.

Reads DiscoveredItem rows directly. No freshness withdrawal, no receiver gate, no
view-only child process. Owner decisions (Interested / Pass) are stored on the row's
inbox_status so they survive re-observation by later cycles.
"""

import collections
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from urllib.parse import quote_plus
from zoneinfo import ZoneInfo

from fastapi import Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select

from radar.db import DiscoveredItem, DiscoveryRun, Setting, TriageResult
from radar.economics import acquisition_terms, pickup_assumed, walk_away
from radar.queue import (
    hero_candidates,
    hero_notes,
    in_hero,
    in_queue,
    is_contemporary,
    offer_target,
    score_floor,
)
from radar.queue import settings as queue_settings
from radar.scheduler import cycle_running, read_state, request_scan, trigger_path
from radar.triage import condition_flags, live_verdict, spent_today, thumb_url
from radar.triage import settings as triage_settings

ALASKA = ZoneInfo("America/Anchorage")
DECISIONS = {"interested": "INTERESTED", "pass": "IRRELEVANT", "reset": "NEW"}
PASS_REASONS = {
    "style": "Not my style",
    "price": "Overpriced",
    "authenticity": "Doubt it's real",
    "category": "Not Native / Southwest",
    "condition": "Damaged / condition",
    "other": "Other",
}
STATUS_FILTERS = {
    "new": ["NEW"],
    "interested": ["INTERESTED"],
    "ended": ["ENDED"],
    "passed": ["IRRELEVANT"],
    "archived": ["ARCHIVED"],
    "all": None,
}


def _dec(value):
    try:
        return Decimal(str(value)) if value not in (None, "") else None
    except InvalidOperation:
        return None


def _parse_time(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _aware(value):
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _relative(when, now):
    when = _aware(when)
    if when is None:
        return None
    delta = when - now
    seconds = int(delta.total_seconds())
    past = seconds < 0
    seconds = abs(seconds)
    if seconds < 3600:
        text = f"{max(1, seconds // 60)}m"
    elif seconds < 86400:
        text = f"{seconds // 3600}h {(seconds % 3600) // 60:02d}m"
    else:
        text = f"{seconds // 86400}d {(seconds % 86400) // 3600}h"
    return f"{text} ago" if past else f"in {text}"


def _listed_days(n, now):
    """Whole days since the marketplace says the listing was created, or None."""
    listed = _parse_time(n.get("listing_time"))
    if listed is None:
        return None
    if listed.tzinfo is None:
        listed = listed.replace(tzinfo=UTC)
    return max(0, (now - listed).days)


def _buyable_now(n, td, price, bin_price, ship):
    """True when a fixed price exists and landing it stays under the walk-away landed cost."""
    fixed = (
        bin_price
        if bin_price is not None
        else (price if "AUCTION" not in (n.get("buying_options") or []) else None)
    )
    wa = td.get("walk_away_landed_usd")
    if fixed is None or wa is None:
        return False
    try:
        return fixed + (ship or Decimal(0)) <= Decimal(wa)
    except (InvalidOperation, TypeError):
        return False


def comp_links(query):
    q = quote_plus(query)
    return [
        ("eBay sold", f"https://www.ebay.com/sch/i.html?_nkw={q}&LH_Sold=1&LH_Complete=1"),
        (
            "LiveAuctioneers results",
            f"https://www.liveauctioneers.com/auction-results/?keyword={q}",
        ),
        (
            "Invaluable",
            f"https://www.invaluable.com/search?keyword={q}&sort=price-desc&priceResult=1",
        ),
        ("WorthPoint", f"https://www.worthpoint.com/worthopedia?query={q}"),
    ]


def card(row, now, triage=None):
    n = row.normalized or {}
    td = live_verdict(triage.data, n) if triage and triage.status == "OK" else {}
    price = _dec(n.get("item_price"))
    ship = _dec(n.get("shipping_cost"))
    ends = _parse_time(n.get("item_end_time"))
    options = n.get("buying_options") or []
    is_auction = "AUCTION" in options
    has_bin = "FIXED_PRICE" in options
    bin_price = _dec(n.get("buy_it_now_price"))
    images = n.get("image_urls") or []
    return dict(
        id=row.id,
        source=row.source,
        status=row.inbox_status,
        title=n.get("raw_title") or "(untitled)",
        url=n.get("listing_url"),
        image=thumb_url(images[0]) if images else None,
        price=price,
        ship=ship,
        landed=(price + ship) if price is not None and ship is not None else None,
        ship_status=n.get("shipping_status") or "UNKNOWN",
        kind=(
            "Auction + BIN"
            if is_auction and has_bin
            else "Auction"
            if is_auction
            else "Buy It Now"
            if has_bin
            else "Listing"
        ),
        is_auction=is_auction,
        bin_price=bin_price,
        bid_count=n.get("bid_count"),
        ends=ends,
        ends_local=ends.astimezone(ALASKA).strftime("%a %b %d %I:%M %p AKT") if ends else None,
        ends_relative=_relative(ends, now),
        ended=bool(ends and ends < now),
        condition=n.get("condition"),
        seller=n.get("seller_id"),
        phrase=n.get("raw_search_phrase") or n.get("original_search_phrase"),
        first_seen=_aware(row.first_seen),
        listed_days=_listed_days(n, now),
        seen=bool(n.get("seen_at")),
        hero=hero_notes(n, td) if td.get("relevant") else [],
        contemporary=is_contemporary(td),
        era=(td.get("era") or "").title() if td.get("era") not in (None, "UNKNOWN") else None,
        first_seen_relative=_relative(row.first_seen, now) if row.first_seen else None,
        last_seen=_aware(row.last_seen),
        synthetic=bool(n.get("synthetic")),
        mode=n.get("discovery_mode"),
        triaged=triage is not None and triage.status == "OK",
        relevant=triage.relevant if triage and triage.status == "OK" else None,
        score=td.get("score") if triage and triage.status == "OK" else None,
        category=(td.get("category") or "").replace("_", " ").title() or None,
        piece_type=td.get("piece_type"),
        signals=td.get("quality_signals") or [],
        concerns=td.get("concerns") or [],
        reason=td.get("reason"),
        value_read=(td.get("value_read") or "").title() or None,
        archive_reason=n.get("archive_reason"),
        pass_reason=PASS_REASONS.get(n.get("pass_reason") or "", n.get("pass_reason")),
        pass_note=n.get("pass_note"),
        condition_flags=condition_flags(n) or td.get("condition_flags") or [],
        walk=walk_away(
            ((n.get("owner_resale_usd") or td.get("resale_estimate_usd") or [None])[0]),
            *acquisition_terms(n),
        ),
        pickup_assumed=pickup_assumed(n),
        owner_resale=n.get("owner_resale_usd"),
        buyable_now=_buyable_now(n, td, price, bin_price, ship),
        offer=offer_target(n, td),
        takes_offers="BEST_OFFER" in (n.get("buying_options") or []),
        comp_query=td.get("comp_query") or (n.get("raw_title") or "")[:80],
        comps=comp_links(td.get("comp_query") or (n.get("raw_title") or "")[:80]),
        hallmark_read=td.get("hallmark_read"),
        deep=bool(td.get("deep")),
        bought_for=n.get("bought_for"),
        sold_for=n.get("sold_for"),
        lens_url=("https://lens.google.com/uploadbyurl?url=" + quote_plus(thumb_url(images[0])))
        if images
        else None,
        watched_at=n.get("watched_at"),
        resale=n.get("owner_resale_usd") or td.get("resale_estimate_usd"),
        resale_source=td.get("resale_source", "model"),
    )


def radar_routes(app, render):
    @app.get("/radar")
    def radar_page(
        request: Request,
        status: str = "new",
        kind: str = "all",
        sort: str = "score",
        phrase: str = "",
        hours: int = 0,
        page: int = 1,
        show: str = "queue",
        min_score: int = 0,
        source: str = "",
        seen: str = "hide",
    ):
        seen = "show" if seen == "show" else "hide"
        status = status if status in STATUS_FILTERS else "new"
        kind = kind if kind in ("all", "auction", "bin") else "all"
        sort = (
            sort
            if sort in ("score", "newest", "ending", "price", "price_desc", "listed")
            else "score"
        )
        show = (
            show
            if show in ("queue", "hero", "relevant", "all", "irrelevant", "untriaged")
            else "queue"
        )
        if show in ("queue", "hero") and status != "new":
            show = "all"  # the queue only holds NEW items
        qcfg = queue_settings()
        page = max(1, page)
        per_page = 48
        now = datetime.now(UTC)
        with app.state.sessions() as s:
            query = select(DiscoveredItem)
            wanted = STATUS_FILTERS[status]
            if wanted:
                query = query.where(DiscoveredItem.inbox_status.in_(wanted))
            if hours > 0:
                since = (now - timedelta(hours=hours)).replace(tzinfo=None)
                query = query.where(DiscoveredItem.first_seen >= since)
            if show == "queue":
                # The default view: let SQL drop everything that cannot be in the queue so a
                # page view loads dozens of rows, not every NEW listing.
                query = query.where(
                    DiscoveredItem.id.in_(
                        select(TriageResult.item_id)
                        .where(TriageResult.relevant.is_(True))
                        .where(score_floor(qcfg["min_score"]))
                    )
                )
            if show == "hero":
                query = query.where(hero_candidates()).where(
                    DiscoveredItem.id.in_(
                        select(TriageResult.item_id)
                        .where(TriageResult.relevant.is_(True))
                        .where(score_floor(qcfg["hero_min_score"]))
                    )
                )
            rows = list(s.scalars(query))
            triage = {
                tr.item_id: tr
                for tr in s.scalars(
                    select(TriageResult).where(TriageResult.item_id.in_([r.id for r in rows]))
                )
            }
            if show == "queue":
                rows = [r for r in rows if in_queue(r, triage.get(r.id), now, qcfg)]
            elif show == "hero":
                rows = [r for r in rows if in_hero(r, triage.get(r.id), now, qcfg)]
            cards = [card(r, now, triage.get(r.id)) for r in rows]
            if show == "relevant":
                cards = [c for c in cards if c["relevant"] is True or not c["triaged"]]
            elif show == "irrelevant":
                cards = [c for c in cards if c["relevant"] is False]
            elif show == "untriaged":
                cards = [c for c in cards if not c["triaged"]]
            if min_score > 0:
                cards = [c for c in cards if (c["score"] or 0) >= min_score]
            if kind == "auction":
                cards = [c for c in cards if c["is_auction"]]
            elif kind == "bin":
                cards = [c for c in cards if not c["is_auction"]]
            if phrase:
                cards = [c for c in cards if c["phrase"] == phrase]
            if source:
                cards = [c for c in cards if c["source"] == source]
            if sort == "score":
                soon = now + timedelta(hours=6)
                cards.sort(
                    key=lambda c: (
                        # the hero tab leads with contemporary work
                        1 if show == "hero" and c["contemporary"] else 0,
                        2
                        if c["buyable_now"] and not c["ended"]
                        else (1 if c["offer"] and not c["ended"] else 0),
                        # among offer candidates, the longest-listed come first: a seller
                        # who has waited is likelier to take less
                        (c["listed_days"] or 0) if c["offer"] and not c["buyable_now"] else 0,
                        1
                        if (c["is_auction"] and c["ends"] and not c["ended"] and c["ends"] <= soon)
                        else 0,
                        c["score"] if c["score"] is not None else -1,
                        c["first_seen"] or now,
                    ),
                    reverse=True,
                )
            elif sort == "ending":
                far = datetime.max.replace(tzinfo=UTC)
                cards.sort(key=lambda c: (c["ended"], c["ends"] or far))
            elif sort == "price":
                cards.sort(key=lambda c: (c["price"] is None, c["price"] or 0))
            elif sort == "listed":
                cards.sort(key=lambda c: (c["listed_days"] is None, -(c["listed_days"] or 0)))
            elif sort == "price_desc":
                cards.sort(key=lambda c: (c["price"] is None, -(c["price"] or 0)))
            else:
                cards.sort(key=lambda c: c["first_seen"] or now, reverse=True)
            # In the two queues, listings already marked seen drop out of the way by default.
            seen_hidden = 0
            mark_ids = ""
            if show in ("queue", "hero"):
                seen_hidden = sum(1 for c in cards if c["seen"])
                if seen == "hide":
                    cards = [c for c in cards if not c["seen"]]
                mark_ids = ",".join(str(c["id"]) for c in cards if not c["seen"])
            total_matching = len(cards)
            cards = cards[(page - 1) * per_page : page * per_page]
            for idx, c in enumerate(cards):
                c["next_id"] = cards[idx + 1]["id"] if idx + 1 < len(cards) else 0

            sources = sorted(set(s.scalars(select(DiscoveredItem.source).distinct())) - {None})
            # Distinct values straight from SQL; loading every row just to list them is what
            # made each page view cost as much memory as the whole table.
            phrases = sorted(
                {
                    p
                    for p in s.scalars(
                        select(
                            func.json_extract(DiscoveredItem.normalized, "$.raw_search_phrase")
                        ).distinct()
                    )
                    if isinstance(p, str) and p
                }
            )
            counts = dict(
                s.execute(
                    select(DiscoveredItem.inbox_status, func.count()).group_by(
                        DiscoveredItem.inbox_status
                    )
                ).all()
            )
            day_ago = now - timedelta(hours=24)
            new_24h = (
                s.scalar(
                    select(func.count(DiscoveredItem.id)).where(
                        DiscoveredItem.first_seen >= day_ago.replace(tzinfo=None)
                    )
                )
                or 0
            )
            last_run = s.scalar(select(DiscoveryRun).order_by(DiscoveryRun.id.desc()))
            queue_total = sum(
                1
                for it, tr in s.execute(
                    select(DiscoveredItem, TriageResult)
                    .join(TriageResult, TriageResult.item_id == DiscoveredItem.id)
                    .where(DiscoveredItem.inbox_status == "NEW")
                    .where(TriageResult.relevant.is_(True))
                    .where(score_floor(qcfg["min_score"]))
                )
                if in_queue(it, tr, now, qcfg) and not (it.normalized or {}).get("seen_at")
            )
            hero_total = sum(
                1
                for it, tr in s.execute(
                    select(DiscoveredItem, TriageResult)
                    .join(TriageResult, TriageResult.item_id == DiscoveredItem.id)
                    .where(DiscoveredItem.inbox_status == "NEW")
                    .where(TriageResult.relevant.is_(True))
                    .where(score_floor(qcfg["hero_min_score"]))
                    .where(hero_candidates())
                )
                if in_hero(it, tr, now, qcfg) and not (it.normalized or {}).get("seen_at")
            )
            triaged_total = (
                s.scalar(select(func.count(TriageResult.id)).where(TriageResult.status == "OK"))
                or 0
            )
            waiting = (
                s.scalar(
                    select(func.count(DiscoveredItem.id))
                    .where(DiscoveredItem.id.not_in(select(TriageResult.item_id)))
                    .where(DiscoveredItem.inbox_status != "IRRELEVANT")
                )
                or 0
            )
            budget = triage_settings()["daily_budget"]
            spent = spent_today(s)
            relevant_total = (
                s.scalar(select(func.count(TriageResult.id)).where(TriageResult.relevant.is_(True)))
                or 0
            )
            week = (now - timedelta(days=7)).replace(tzinfo=None)
            passes = collections.Counter()
            for pr, passed_at in s.execute(
                select(
                    func.json_extract(DiscoveredItem.normalized, "$.pass_reason"),
                    func.json_extract(DiscoveredItem.normalized, "$.passed_at"),
                ).where(DiscoveredItem.inbox_status == "IRRELEVANT")
            ):
                pa = _parse_time(passed_at)
                if pr and pa and pa.replace(tzinfo=None) >= week:
                    passes[PASS_REASONS.get(pr, pr)] += 1
            ledger = s.get(Setting, 91)
            calls_today = (
                ledger.data.get("calls", 0)
                if ledger and ledger.data.get("day") == now.strftime("%Y-%m-%d")
                else 0
            )
        sched = read_state()
        sched_next = _parse_time(sched.get("next_at"))
        return render(
            request,
            "radar.html",
            sched=sched,
            sched_running=cycle_running(sched, now),
            sched_requested=trigger_path().exists(),
            sched_next_relative=_relative(sched_next, now) if sched_next else None,
            sched_alive=bool(sched)
            and (now - (_parse_time(sched.get("updated_at")) or now))
            < timedelta(hours=max(2.0, 2 * float(sched.get("interval_hours") or 1))),
            cards=cards,
            total_matching=total_matching,
            seen=seen,
            seen_hidden=seen_hidden,
            mark_ids=mark_ids,
            page=page,
            pages=max(1, -(-total_matching // per_page)),
            status=status,
            kind=kind,
            sort=sort,
            phrase=phrase,
            hours=hours,
            show=show,
            min_score=min_score,
            source=source,
            sources=sources,
            triaged_total=triaged_total,
            screen_waiting=waiting,
            screen_paused=waiting > 0 and spent >= budget,
            screen_budget=f"{budget:.2f}",
            screen_spent=f"{spent:.2f}",
            queue_total=queue_total,
            hero_total=hero_total,
            hero_min_score=qcfg["hero_min_score"],
            queue_min_score=qcfg["min_score"],
            pass_reasons=PASS_REASONS,
            pass_stats=passes.most_common(),
            relevant_total=relevant_total,
            phrases=phrases,
            counts=counts,
            total=sum(counts.values()),
            new_24h=new_24h,
            last_run=last_run,
            last_run_relative=_relative(last_run.started_at, now)
            if last_run and last_run.started_at
            else None,
            calls_today=calls_today,
        )

    @app.post("/radar/scan")
    def radar_scan(back: str = Form("/radar")):
        request_scan()
        if not back.startswith("/radar"):
            back = "/radar"
        return RedirectResponse(back, status_code=303)

    @app.post("/radar/{item_id}/outcome")
    def radar_outcome(
        item_id: int,
        bought_for: str = Form(""),
        sold_for: str = Form(""),
        resale_low: str = Form(""),
        resale_high: str = Form(""),
        back: str = Form("/radar"),
    ):
        with app.state.sessions.begin() as s:
            row = s.get(DiscoveredItem, item_id)
            if row is None:
                raise HTTPException(404, "Item not found")
            n = dict(row.normalized or {})
            lo, hi = (
                _dec(x.strip().lstrip("$").replace(",", "") or None)
                for x in (resale_low, resale_high)
            )
            if lo is not None or hi is not None:
                if lo is None or hi is None or lo <= 0 or hi < lo:
                    raise HTTPException(400, "Resale needs a low and a high, low <= high")
                n["owner_resale_usd"] = [int(lo), int(hi)]
                n["owner_resale_at"] = datetime.now(UTC).isoformat()
            elif resale_low == "clear":
                n.pop("owner_resale_usd", None)
            for key, raw in (("bought_for", bought_for), ("sold_for", sold_for)):
                raw = raw.strip().lstrip("$").replace(",", "")
                if raw == "":
                    continue
                value = _dec(raw)
                if value is None or value < 0:
                    raise HTTPException(400, f"Bad {key}")
                n[key] = str(value)
                n[key + "_at"] = datetime.now(UTC).isoformat()
            row.normalized = n
        if not back.startswith("/radar"):
            back = "/radar"
        return RedirectResponse(f"{back}#item-{item_id}", status_code=303)

    @app.post("/radar/mark-seen")
    def radar_mark_seen(
        request: Request,
        ids: str = Form(""),
        back: str = Form("/radar"),
        undo: str = Form(""),
    ):
        """Mark listings as seen (or, with undo, unseen). Seen is not a decision: the listing
        keeps its status, is still archived on the normal rules, and can still be acted on."""
        wanted = [int(x) for x in ids.split(",") if x.strip().isdigit()][:2000]
        stamp = datetime.now(UTC).isoformat()
        with app.state.sessions.begin() as s:
            query = select(DiscoveredItem)
            if wanted:
                query = query.where(DiscoveredItem.id.in_(wanted))
            elif undo:
                query = query.where(
                    func.json_extract(DiscoveredItem.normalized, "$.seen_at").is_not(None)
                )
            else:
                query = None
            for row in s.scalars(query) if query is not None else ():
                n = dict(row.normalized or {})
                if undo:
                    n.pop("seen_at", None)
                else:
                    n.setdefault("seen_at", stamp)
                row.normalized = n
        if not back.startswith("/radar"):
            back = "/radar"
        return RedirectResponse(back, status_code=303)

    @app.post("/radar/bulk-pass")
    def radar_bulk_pass(
        request: Request,
        ids: list[int] = Form(default_factory=list),  # noqa: B008
        back: str = Form("/radar"),
        reason: str = Form("other"),
        note: str = Form(""),
    ):
        """Pass on several listings at once with one reason and optional note."""
        note = " ".join(note.split())[:300]
        stamp = datetime.now(UTC).isoformat()
        with app.state.sessions.begin() as s:
            rows = s.scalars(select(DiscoveredItem).where(DiscoveredItem.id.in_(ids[:500])))
            for row in rows:
                if row.inbox_status == "INTERESTED":
                    continue  # never bulk-pass something already marked Interested
                row.inbox_status = DECISIONS["pass"]
                n = dict(row.normalized or {})
                n["pass_reason"] = reason if reason in PASS_REASONS else "other"
                n["passed_at"] = stamp
                if note:
                    n["pass_note"] = note
                else:
                    n.pop("pass_note", None)
                row.normalized = n
        if not back.startswith("/radar"):
            back = "/radar"
        return RedirectResponse(back, status_code=303)

    @app.post("/radar/{item_id}/decision")
    def radar_decision(
        request: Request,
        item_id: int,
        decision: str = Form(...),
        back: str = Form("/radar"),
        reason: str = Form(""),
        note: str = Form(""),
        next_id: int = Form(0),
    ):
        if decision not in DECISIONS:
            raise HTTPException(400, "Unknown decision")
        with app.state.sessions.begin() as s:
            row = s.get(DiscoveredItem, item_id)
            if row is None:
                raise HTTPException(404, "Item not found")
            row.inbox_status = DECISIONS[decision]
            n = dict(row.normalized or {})
            if decision == "pass":
                n["pass_reason"] = reason if reason in PASS_REASONS else "other"
                n["passed_at"] = datetime.now(UTC).isoformat()
                note = " ".join(note.split())[:300]
                if note:
                    n["pass_note"] = note
                else:
                    n.pop("pass_note", None)
            elif decision == "reset":
                n.pop("pass_reason", None)
                n.pop("pass_note", None)
                n.pop("archive_reason", None)
            elif decision == "interested":
                n["interested_at"] = datetime.now(UTC).isoformat()
            row.normalized = n
        if not back.startswith("/radar"):
            back = "/radar"
        if next_id and "#" not in back:
            back = f"{back}#item-{next_id}"
        return RedirectResponse(back, status_code=303)
