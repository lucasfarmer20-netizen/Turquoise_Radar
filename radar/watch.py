"""Auction watch: refresh Interested eBay auctions each cycle so cards and alerts carry the
current bid, bid count and end time rather than the values from when the item was found."""

import logging
import os
from datetime import UTC, datetime

from sqlalchemy import select

from radar.db import DiscoveredItem
from radar.discovery.config import Connection, DiscoveryError
from radar.discovery.ebay import EbayAdapter

log = logging.getLogger("radar.watch")


def _end(n):
    try:
        v = n.get("item_end_time")
        d = datetime.fromisoformat(str(v).replace("Z", "+00:00")) if v else None
        return d if d is None or d.tzinfo else d.replace(tzinfo=UTC)
    except ValueError:
        return None


def watched(session, now):
    rows = session.scalars(
        select(DiscoveredItem).where(DiscoveredItem.inbox_status == "INTERESTED")
    ).all()
    out = []
    for i in rows:
        n = i.normalized or {}
        if (i.source or "").lower() != "ebay" or "AUCTION" not in (n.get("buying_options") or []):
            continue
        end = _end(n)
        if end and end < now:
            continue
        out.append(i)
    return out


def apply_item(n, raw):
    """Merge a Browse getItem payload into the normalized record. Returns the new dict."""
    out = dict(n)
    bid = (raw.get("currentBidPrice") or {}).get("value")
    if bid is not None:
        out["item_price"] = str(bid)
    if raw.get("bidCount") is not None:
        out["bid_count"] = int(raw["bidCount"])
    if raw.get("itemEndDate"):
        out["item_end_time"] = raw["itemEndDate"]
    if "FIXED_PRICE" in (raw.get("buyingOptions") or []) and (raw.get("price") or {}).get("value"):
        out["buy_it_now_price"] = str(raw["price"]["value"])
    ship_to = raw.get("shipToLocations") or {}
    regions = [
        r.get("regionId") or r.get("regionName") for r in ship_to.get("regionIncluded") or []
    ]
    excluded = [
        r.get("regionId") or r.get("regionName") for r in ship_to.get("regionExcluded") or []
    ]
    if regions and not any(x in ("AK", "Alaska", "US_AK") for x in excluded):
        if any(x in ("US", "WORLDWIDE", "AMERICAS", "NORTH_AMERICA") for x in regions):
            out["shipping_status"] = "CONFIRMED_TO_ALASKA"
    if any(x in ("AK", "Alaska", "US_AK") for x in excluded):
        out["shipping_status"] = "EXCLUDED_ALASKA"
    out["watched_at"] = datetime.now(UTC).isoformat()
    return out


async def refresh(sessions, now=None):
    """Refresh every watched auction. Returns number refreshed."""
    now = now or datetime.now(UTC)
    with sessions() as s:
        items = [(i.id, i.ebay_item_id) for i in watched(s, now)]
    if not items:
        return 0
    if os.environ.get("EBAY_MODE", "fixture") != "production":
        log.info("auction watch: %d items but EBAY_MODE is not production; skipped", len(items))
        return 0
    connection = Connection.from_env()
    try:
        adapter = EbayAdapter(connection, production_run_approved=True)
    except DiscoveryError as e:
        log.warning("auction watch unavailable: %s", e)
        return 0
    done = 0
    try:
        for item_id, ebay_id in items:
            try:
                raw = await adapter.get_item(ebay_id)
            except DiscoveryError as e:
                log.warning("watch %s: %s", ebay_id, e)
                continue
            with sessions.begin() as s:
                row = s.get(DiscoveredItem, item_id)
                if row:
                    row.normalized = apply_item(row.normalized or {}, raw)
                    done += 1
    finally:
        await adapter.close()
    log.info("auction watch: refreshed %d of %d", done, len(items))
    return done
