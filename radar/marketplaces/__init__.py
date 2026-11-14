"""Additional marketplace adapters (Etsy, Bonanza, ...) that run beside the eBay lane.

Each adapter exposes `configured() -> bool` and `async search(query) -> list[ListingInput]`.
`run_marketplaces()` runs every configured adapter over the seed queries and stores results
through the same upsert path the email intake uses, so triage, /radar and notifications
see them as ordinary DiscoveredItem rows.
"""

import asyncio
import json
import logging
import os
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select

from radar.db import DiscoveredItem, Setting
from radar.sources import dedup_key

log = logging.getLogger("radar.marketplaces")
STATE_SETTING_ID = 93

REFRESH_FIELDS = (
    "item_price",
    "shipping_cost",
    "shipping_status",
    "buying_options",
    "buy_it_now_price",
    "bid_count",
    "item_end_time",
    "image_urls",
    "condition",
    "stated_weight",
    "weight_unit",
)


def seed_queries():
    """Flat list of owner search terms from the scheduler's seed file."""
    root = Path(__file__).resolve().parent.parent.parent
    path = Path(os.environ.get("RADAR_QUERIES", root / "fixtures" / "seed_queries.json"))
    spec = json.loads(Path(path).read_text(encoding="utf-8"))
    seen, out = set(), []
    for group in spec.get("groups", []):
        for q in group.get("queries", []):
            k = q.casefold().strip()
            if k and k not in seen:
                seen.add(k)
                out.append(q)
    return out


def queries_for(mod, queries):
    """Search terms for one marketplace: <SOURCE>_QUERIES from .env (comma-separated), else the
    adapter's own DEFAULT_QUERIES, else every seed term. Retail-heavy sites get a short list so
    screening money isn't spent on thousands of full-price listings."""
    override = os.environ.get(f"{mod.SOURCE.upper()}_QUERIES", "").strip()
    if override.casefold() == "all":
        return queries
    if override:
        return [q.strip() for q in override.split(",") if q.strip()]
    return list(getattr(mod, "DEFAULT_QUERIES", None) or queries)


def store(sessions, items):
    """Upsert ListingInput rows into discovered_items. Returns (new, refreshed)."""
    new = refreshed = 0
    with sessions.begin() as s:
        for item in items:
            key = dedup_key(item)
            found = s.scalar(select(DiscoveredItem).where(DiscoveredItem.identity == key))
            data = item.model_dump(mode="json")
            if found is None:
                s.add(
                    DiscoveredItem(
                        identity=key,
                        source=item.source,
                        ebay_item_id=item.source_listing_id or key,
                        normalized=data,
                        original_data={"adapter": item.source, "fetched_at": _now()},
                    )
                )
                new += 1
            else:
                found.last_seen = datetime.now(UTC)
                merged = dict(found.normalized or {})
                for k in REFRESH_FIELDS:
                    if data.get(k) is not None:
                        merged[k] = data[k]
                found.normalized = merged
                refreshed += 1
    return new, refreshed


def _now():
    return datetime.now(UTC).isoformat()


def adapters():
    from radar.marketplaces import bonanza, etsy

    return [m for m in (etsy, bonanza) if m.configured()]


async def run_marketplaces(sessions, queries=None, fast=False):
    """Run every configured adapter over the seed queries. Returns {source: stats}. With
    fast=True only adapters that return newest-first (FAST_LANE = True) run, for the
    between-cycles check, and routine logging and state are skipped."""
    active = [m for m in adapters() if not fast or getattr(m, "FAST_LANE", False)]
    if not active:
        return {}
    queries = queries or seed_queries()
    report = {}
    for mod in active:
        name = mod.SOURCE
        stats = dict(queries=0, listings=0, new=0, refreshed=0, errors=0, calls=0)
        for q in queries_for(mod, queries):
            try:
                items, calls = await mod.search(q)
                stats["calls"] += calls
            except mod.NotReady as e:
                log.info("%s not ready: %s", name, e)
                stats["errors"] += 1
                break
            except Exception as e:
                stats["errors"] += 1
                log.warning("%s search %r failed: %s", name, q, str(e)[:200])
                continue
            stats["queries"] += 1
            stats["listings"] += len(items)
            n, r = store(sessions, items)
            stats["new"] += n
            stats["refreshed"] += r
            await asyncio.sleep(mod.PAUSE_SECONDS)
        report[name] = stats
        if fast:
            if stats["new"] or stats["errors"]:
                log.info(
                    "fast lane %s: %d new, %d errors, %d calls",
                    name,
                    stats["new"],
                    stats["errors"],
                    stats["calls"],
                )
            continue
        log.info(
            "%s: %d queries, %d listings, %d new, %d refreshed, %d errors, %d calls",
            name,
            stats["queries"],
            stats["listings"],
            stats["new"],
            stats["refreshed"],
            stats["errors"],
            stats["calls"],
        )
    if fast:
        return report
    with sessions.begin() as s:
        row = s.get(Setting, STATE_SETTING_ID)
        data = {"last_run_at": _now(), "report": report}
        if row:
            row.data = data
        else:
            s.add(Setting(id=STATE_SETTING_ID, data=data))
    return report
