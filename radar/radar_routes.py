"""Plain results page for scheduler finds: /radar.

Reads DiscoveredItem rows directly. No freshness withdrawal, no receiver gate, no
view-only child process. Owner decisions (Interested / Pass) are stored on the row's
inbox_status so they survive re-observation by later cycles.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from fastapi import Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select

from radar.db import DiscoveredItem, DiscoveryRun, Setting, TriageResult
from radar.triage import thumb_url

ALASKA = ZoneInfo("America/Anchorage")
DECISIONS = {"interested": "INTERESTED", "pass": "IRRELEVANT", "reset": "NEW"}
STATUS_FILTERS = {
    "new": ["NEW"],
    "interested": ["INTERESTED"],
    "passed": ["IRRELEVANT"],
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


def card(row, now, triage=None):
    n = row.normalized or {}
    td = (triage.data if triage and triage.status == "OK" else None) or {}
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
        first_seen_relative=_relative(row.first_seen, now) if row.first_seen else None,
        last_seen=_aware(row.last_seen),
        synthetic=bool(n.get("synthetic")),
        mode=n.get("discovery_mode"),
        triaged=triage is not None and triage.status == "OK",
        relevant=triage.relevant if triage and triage.status == "OK" else None,
        score=triage.score if triage and triage.status == "OK" else None,
        category=(td.get("category") or "").replace("_", " ").title() or None,
        piece_type=td.get("piece_type"),
        signals=td.get("quality_signals") or [],
        concerns=td.get("concerns") or [],
        reason=td.get("reason"),
        value_read=(td.get("value_read") or "").title() or None,
        resale=td.get("resale_estimate_usd"),
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
        show: str = "relevant",
        min_score: int = 0,
    ):
        status = status if status in STATUS_FILTERS else "new"
        kind = kind if kind in ("all", "auction", "bin") else "all"
        sort = sort if sort in ("score", "newest", "ending", "price", "price_desc") else "score"
        show = show if show in ("relevant", "all", "irrelevant", "untriaged") else "relevant"
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
            rows = list(s.scalars(query))
            triage = {
                tr.item_id: tr
                for tr in s.scalars(
                    select(TriageResult).where(TriageResult.item_id.in_([r.id for r in rows]))
                )
            }
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
            if sort == "score":
                cards.sort(
                    key=lambda c: (
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
            elif sort == "price_desc":
                cards.sort(key=lambda c: (c["price"] is None, -(c["price"] or 0)))
            else:
                cards.sort(key=lambda c: c["first_seen"] or now, reverse=True)
            total_matching = len(cards)
            cards = cards[(page - 1) * per_page : page * per_page]

            phrases = sorted(
                {
                    (r.normalized or {}).get("raw_search_phrase")
                    for r in s.scalars(select(DiscoveredItem))
                }
                - {None}
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
            triaged_total = (
                s.scalar(select(func.count(TriageResult.id)).where(TriageResult.status == "OK"))
                or 0
            )
            relevant_total = (
                s.scalar(select(func.count(TriageResult.id)).where(TriageResult.relevant.is_(True)))
                or 0
            )
            ledger = s.get(Setting, 91)
            calls_today = (
                ledger.data.get("calls", 0)
                if ledger and ledger.data.get("day") == now.strftime("%Y-%m-%d")
                else 0
            )
        return render(
            request,
            "radar.html",
            cards=cards,
            total_matching=total_matching,
            page=page,
            pages=max(1, -(-total_matching // per_page)),
            status=status,
            kind=kind,
            sort=sort,
            phrase=phrase,
            hours=hours,
            show=show,
            min_score=min_score,
            triaged_total=triaged_total,
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

    @app.post("/radar/{item_id}/decision")
    def radar_decision(
        request: Request, item_id: int, decision: str = Form(...), back: str = Form("/radar")
    ):
        if decision not in DECISIONS:
            raise HTTPException(400, "Unknown decision")
        with app.state.sessions.begin() as s:
            row = s.get(DiscoveredItem, item_id)
            if row is None:
                raise HTTPException(404, "Item not found")
            row.inbox_status = DECISIONS[decision]
        if not back.startswith("/radar"):
            back = "/radar"
        return RedirectResponse(back, status_code=303)
