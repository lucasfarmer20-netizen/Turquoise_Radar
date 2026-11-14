"""eBay category sweep: every new listing filed under Ethnic, Regional & Tribal Jewelry
(category 262025) with the "Ethnic & Regional Style" aspect set to the owner's styles,
no keywords. Complements the keyword lane, which catches mis-filed listings.

Env:
    RADAR_CATEGORY_SWEEP="Native American,Southwestern,Mexican"   (empty disables)
    RADAR_CATEGORY_SWEEP_LIMIT=200                                  (per call, max 200)
Two calls per style per cycle: Buy It Now newest-first, auctions ending-soonest.
"""

import logging
import os
from datetime import UTC, datetime

from sqlalchemy import select

from radar.db import DiscoveredItem, Setting
from radar.discovery.config import Connection, DiscoveryError
from radar.discovery.ebay import EbayAdapter, normalize
from radar.discovery.service import _identity

log = logging.getLogger("radar.category_sweep")
CATEGORY_ID = "262025"
ASPECT = "Ethnic & Regional Style"
STATE_SETTING_ID = 94
LANES = (("FIXED_PRICE", "newlyListed"), ("AUCTION", "endingSoonest"))


def settings():
    raw = os.environ.get("RADAR_CATEGORY_SWEEP", "Native American,Southwestern,Mexican")
    return dict(
        styles=[x.strip() for x in raw.split(",") if x.strip()],
        limit=min(200, int(os.environ.get("RADAR_CATEGORY_SWEEP_LIMIT", "200"))),
    )


def params(style, buying, sort, limit):
    return dict(
        category_ids=CATEGORY_ID,
        aspect_filter=f"categoryId:{CATEGORY_ID},{ASPECT}:{{{style}}}",
        filter=f"buyingOptions:{{{buying}}},itemLocationCountry:US",
        sort=sort,
        limit=limit,
        fieldgroups="EXTENDED",
    )


async def fetch(adapter, style, buying, sort, limit):
    token = await adapter.access_token()
    data = await adapter._request(
        "GET",
        "/buy/browse/v1/item_summary/search",
        params=params(style, buying, sort, limit),
        headers={
            "Authorization": "Bearer " + token,
            "X-EBAY-C-MARKETPLACE-ID": "EBAY_US",
            "X-EBAY-C-ENDUSERCTX": "contextualLocation=country%3DUS%2Czip%3D99669",
        },
    )
    return adapter.public_data(data).get("itemSummaries") or []


def store(sessions, raws, mode, label):
    new = refreshed = invalid = 0
    with sessions.begin() as s:
        for raw in raws:
            try:
                item = normalize(raw, mode, label, label)
            except DiscoveryError:
                invalid += 1
                continue
            identity = _identity("eBay", item.source_listing_id)
            found = s.scalar(select(DiscoveredItem).where(DiscoveredItem.identity == identity))
            if found is None:
                s.add(
                    DiscoveredItem(
                        identity=identity,
                        source="eBay",
                        ebay_item_id=item.source_listing_id,
                        normalized=item.model_dump(mode="json"),
                        original_data=raw,
                    )
                )
                new += 1
            else:
                found.last_seen = datetime.now(UTC)
                merged = dict(found.normalized or {})
                fresh = item.model_dump(mode="json")
                for k in (
                    "item_price",
                    "buy_it_now_price",
                    "bid_count",
                    "item_end_time",
                    "shipping_cost",
                ):
                    if fresh.get(k) is not None:
                        merged[k] = fresh[k]
                found.normalized = merged
                refreshed += 1
    return new, refreshed, invalid


# The fast lane: newest fixed-price listings in every style, plus the auctions closing soonest
# in the first style, so their current bids are fresh when the last-hour alert is decided.
# Newly listed auctions are deliberately left out: an opening bid says nothing.
FAST_LANES = (("FIXED_PRICE", "newlyListed"), ("AUCTION", "endingSoonest"))


async def run(sessions, cfg=None, fast=False):
    """Sweep every configured style. Returns stats dict or None when disabled. With fast=True
    only the newest listings are fetched (see FAST_LANES) and routine logging is skipped."""
    cfg = cfg or settings()
    if not cfg["styles"]:
        return None
    mode = os.environ.get("EBAY_MODE", "fixture")
    if mode != "production":
        log.info("category sweep skipped: EBAY_MODE=%s", mode)
        return None
    connection = Connection.from_env()
    try:
        adapter = EbayAdapter(connection, production_run_approved=True)
    except DiscoveryError as e:
        log.warning("category sweep unavailable: %s", e)
        return None
    stats = dict(calls=0, listings=0, new=0, refreshed=0, invalid=0, errors=0, by_style={})
    try:
        for index, style in enumerate(cfg["styles"]):
            st = dict(listings=0, new=0)
            lanes = LANES
            if fast:
                lanes = FAST_LANES if index == 0 else FAST_LANES[:1]
            for buying, sort in lanes:
                try:
                    raws = await fetch(
                        adapter,
                        style,
                        buying,
                        sort,
                        min(cfg["limit"], 50) if fast else cfg["limit"],
                    )
                except DiscoveryError as e:
                    stats["errors"] += 1
                    log.warning("sweep %s/%s: %s", style, buying, e)
                    continue
                stats["calls"] += 1
                n, r, i = store(sessions, raws, mode, f"category:{style}")
                st["listings"] += len(raws)
                st["new"] += n
                stats["listings"] += len(raws)
                stats["new"] += n
                stats["refreshed"] += r
                stats["invalid"] += i
            stats["by_style"][style] = st
    finally:
        await adapter.close()
    if fast:
        if stats["new"] or stats["errors"]:
            log.info(
                "fast lane: %d calls, %d new, %d errors; %s",
                stats["calls"],
                stats["new"],
                stats["errors"],
                {k: v["new"] for k, v in stats["by_style"].items() if v["new"]},
            )
        return stats
    with sessions.begin() as s:
        row = s.get(Setting, STATE_SETTING_ID)
        data = {"last_run_at": datetime.now(UTC).isoformat(), "stats": stats}
        if row:
            row.data = data
        else:
            s.add(Setting(id=STATE_SETTING_ID, data=data))
    log.info(
        "category sweep: %d calls, %d listings, %d new, %d refreshed, %d invalid, %d errors; %s",
        stats["calls"],
        stats["listings"],
        stats["new"],
        stats["refreshed"],
        stats["invalid"],
        stats["errors"],
        {k: v["new"] for k, v in stats["by_style"].items()},
    )
    return stats
