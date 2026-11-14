"""eBay marketplaces outside the US, through the same Browse API.

A lot of Native American jewelry was exported to Germany, the UK and Japan in the 1970s-90s
and is listed there under local-language titles that US buyers never search. One Browse call
per (marketplace, phrase), newest first, restricted to sellers who deliver to the US.

    RADAR_FOREIGN_MARKETS=EBAY_DE,EBAY_GB        (empty turns the lane off)
    RADAR_FOREIGN_QUERIES_EBAY_DE=Indianerschmuck,Navajo Silber     (comma separated)
    RADAR_FX_EUR=1.08  RADAR_FX_GBP=1.27         USD per unit; overrides the fetched rate
    RADAR_IMPORT_RATE=0.15                       allowance for duty and import fees

Prices are converted to USD so the rest of the radar (walk-away, queue) works unchanged. The
rate comes from the European Central Bank's published daily reference feed unless set in .env;
with no rate available for a currency, its listings are skipped rather than guessed.
Each stored listing keeps the original amount, the marketplace and the import allowance.

Cautions the code cannot settle: coral and conch shell may need wildlife import paperwork;
"Indianerschmuck" also covers European-made pieces; tribal attribution needs its own evidence.
"""

import logging
import os
import re
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

import httpx
from sqlalchemy import select

from radar.db import DiscoveredItem
from radar.discovery.config import Connection, DiscoveryError
from radar.discovery.ebay import EbayAdapter, normalize
from radar.discovery.service import _identity

log = logging.getLogger("radar.foreign")
ECB_FEED = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml"
DEFAULT_QUERIES = {
    "EBAY_DE": ["Indianerschmuck Silber", "Navajo Silber Türkis", "Zuni Schmuck", "Türkis Armreif"],
    "EBAY_GB": ["Navajo silver turquoise", "Zuni silver", "old pawn silver", "squash blossom"],
}
REFRESH = ("item_price", "buy_it_now_price", "bid_count", "item_end_time", "shipping_cost")


def settings():
    markets = [
        m.strip().upper()
        for m in os.environ.get("RADAR_FOREIGN_MARKETS", "EBAY_DE,EBAY_GB").split(",")
        if m.strip()
    ]
    queries = {}
    for m in markets:
        raw = os.environ.get(f"RADAR_FOREIGN_QUERIES_{m}", "")
        queries[m] = [q.strip() for q in raw.split(",") if q.strip()] or DEFAULT_QUERIES.get(m, [])
    return dict(
        markets=[m for m in markets if queries[m]],
        queries=queries,
        import_rate=os.environ.get("RADAR_IMPORT_RATE", "0.15"),
        limit=min(50, int(os.environ.get("RADAR_FOREIGN_LIMIT", "50"))),
    )


def parse_ecb(xml_text):
    """{currency: USD per unit} from the ECB daily feed (rates are quoted per EUR)."""
    per_eur = {
        c: Decimal(r)
        for c, r in re.findall(
            r"currency=['\"]([A-Z]{3})['\"]\s+rate=['\"]([0-9.]+)['\"]", xml_text
        )
    }
    usd = per_eur.get("USD")
    if not usd or usd <= 0:
        return {}
    out = {"EUR": usd}
    for cur, rate in per_eur.items():
        if cur != "USD" and rate > 0:
            out[cur] = usd / rate
    return out


async def rates(client=None):
    """USD per unit of each currency: .env overrides first, then the ECB feed."""
    fetched = {}
    try:
        if client is None:
            async with httpx.AsyncClient(timeout=20) as c:
                r = await c.get(ECB_FEED)
        else:
            r = await client.get(ECB_FEED)
        r.raise_for_status()
        fetched = parse_ecb(r.text)
    except Exception as e:  # noqa: BLE001
        log.warning("exchange-rate feed unavailable: %s", str(e)[:120])
    for key, value in os.environ.items():
        if key.startswith("RADAR_FX_") and value.strip():
            try:
                fetched[key[9:].upper()] = Decimal(value.strip())
            except InvalidOperation:
                log.warning("ignoring %s: not a number", key)
    return fetched


def to_usd(amount, rate):
    if amount in (None, ""):
        return None
    return str((Decimal(str(amount)) * rate).quantize(Decimal("0.01")))


def convert(item, market, fx, import_rate):
    """Stored record (dict) for one foreign listing with money in USD, or None when the
    currency has no rate."""
    currency = item.currency
    rate = Decimal(1) if currency == "USD" else fx.get(currency)
    if rate is None:
        return None
    data = item.model_dump(mode="json")
    original = f"{data.get('buy_it_now_price') or data.get('item_price')} {currency}"
    for key in ("item_price", "buy_it_now_price", "shipping_cost"):
        data[key] = to_usd(data.get(key), rate)
    data["currency"] = "USD"
    data["marketplace"] = market
    data["original_price"] = original
    data["fx_rate"] = str(rate.quantize(Decimal("0.0001")))
    data["import_rate"] = str(import_rate)
    return data


def store(sessions, records):
    new = refreshed = 0
    with sessions.begin() as s:
        for data in records:
            identity = _identity("eBay", data["source_listing_id"])
            found = s.scalar(select(DiscoveredItem).where(DiscoveredItem.identity == identity))
            if found is None:
                s.add(
                    DiscoveredItem(
                        identity=identity,
                        source="eBay",
                        ebay_item_id=data["source_listing_id"],
                        normalized=data,
                        original_data={"marketplace": data["marketplace"]},
                    )
                )
                new += 1
            else:
                found.last_seen = datetime.now(UTC)
                merged = dict(found.normalized or {})
                for k in (*REFRESH, "original_price", "fx_rate"):
                    if data.get(k) is not None:
                        merged[k] = data[k]
                found.normalized = merged
                refreshed += 1
    return new, refreshed


async def fetch(adapter, market, query, limit):
    token = await adapter.access_token()
    data = await adapter._request(
        "GET",
        "/buy/browse/v1/item_summary/search",
        params=dict(
            q=query,
            limit=limit,
            sort="newlyListed",
            filter="buyingOptions:{FIXED_PRICE|AUCTION},deliveryCountry:US",
            fieldgroups="EXTENDED",
        ),
        headers={
            "Authorization": "Bearer " + token,
            "X-EBAY-C-MARKETPLACE-ID": market,
            "X-EBAY-C-ENDUSERCTX": "contextualLocation=country%3DUS%2Czip%3D99669",
        },
    )
    return adapter.public_data(data).get("itemSummaries") or []


async def run(sessions, cfg=None):
    """One pass over every configured marketplace. Returns stats, or None when disabled."""
    cfg = cfg or settings()
    if not cfg["markets"]:
        return None
    mode = os.environ.get("EBAY_MODE", "fixture")
    if mode != "production":
        return None
    try:
        adapter = EbayAdapter(Connection.from_env(), production_run_approved=True)
    except DiscoveryError as e:
        log.warning("foreign marketplaces unavailable: %s", e)
        return None
    fx = await rates()
    stats = dict(calls=0, listings=0, new=0, refreshed=0, skipped=0, errors=0, by_market={})
    try:
        for market in cfg["markets"]:
            found = 0
            for query in cfg["queries"][market]:
                try:
                    raws = await fetch(adapter, market, query, cfg["limit"])
                except DiscoveryError as e:
                    stats["errors"] += 1
                    log.warning("foreign %s %r: %s", market, query, e)
                    continue
                stats["calls"] += 1
                records = []
                for raw in raws:
                    try:
                        item = normalize(raw, mode, query, query)
                    except DiscoveryError:
                        stats["skipped"] += 1
                        continue
                    record = convert(item, market, fx, cfg["import_rate"])
                    if record is None:
                        stats["skipped"] += 1
                        continue
                    records.append(record)
                n, refreshed = store(sessions, records)
                stats["listings"] += len(records)
                stats["new"] += n
                stats["refreshed"] += refreshed
                found += n
            stats["by_market"][market] = found
    finally:
        await adapter.close()
    log.info(
        "foreign marketplaces: %d calls, %d listings, %d new, %d refreshed, %d skipped, "
        "%d errors; %s",
        stats["calls"],
        stats["listings"],
        stats["new"],
        stats["refreshed"],
        stats["skipped"],
        stats["errors"],
        stats["by_market"],
    )
    return stats
