"""Etsy Open API v3 adapter: keyword search of active listings + batch image lookup.

Env: ETSY_API_KEY (keystring). Optional: ETSY_SHOP_LOCATION (default "US"),
ETSY_PAGE_SIZE (default 50, max 100), ETSY_MIN_PRICE.
Rate limits are generous (10k/day); two calls per query (search + images).
"""

import os
from datetime import UTC, datetime
from decimal import Decimal

import httpx

from radar.schemas import ListingInput, Shipping

SOURCE = "Etsy"
# Etsy is retail-priced and huge: all 31 seed terms brought ~230 new listings an hour (about
# $2 a day to screen) for few finds. ETSY_QUERIES in .env replaces this list; "all" restores
# every seed term.
DEFAULT_QUERIES = [
    "old pawn",
    "navajo cuff",
    "zuni inlay",
    "squash blossom necklace",
    "concho belt",
    "fred harvey era",
    "southwestern sterling lot",
    "estate sterling jewelry lot",
]
BASE = "https://openapi.etsy.com/v3/application"
PAUSE_SECONDS = 0.3
FAST_LANE = True  # searches return newest listings first
WEIGHT_TO_G = {
    "g": Decimal(1),
    "kg": Decimal(1000),
    "oz": Decimal("28.3495"),
    "lb": Decimal("453.592"),
}


class NotReady(Exception):
    """Credentials present but Etsy refuses them (app not yet approved, bad key)."""


def configured():
    return bool(os.environ.get("ETSY_API_KEY", "").strip())


def api_key_header():
    """Etsy v3 wants "keystring:shared_secret" in x-api-key. ETSY_API_KEY may already hold the
    pair; otherwise ETSY_SHARED_SECRET is appended. A bare keystring is sent as-is (and refused)."""
    key = os.environ["ETSY_API_KEY"].strip()
    secret = os.environ.get("ETSY_SHARED_SECRET", "").strip()
    return f"{key}:{secret}" if secret and ":" not in key else key


def _headers():
    return {"x-api-key": api_key_header(), "Accept": "application/json"}


def _money(m):
    if not m or m.get("amount") is None:
        return None
    try:
        return Decimal(m["amount"]) / Decimal(m.get("divisor") or 100)
    except Exception:
        return None


def _weight_g(value, unit):
    if value in (None, "", 0):
        return None
    try:
        return (Decimal(str(value)) * WEIGHT_TO_G[(unit or "g").lower()]).quantize(Decimal("0.01"))
    except Exception:
        return None


def to_listing(raw, images, query):
    price = _money(raw.get("price"))
    created = raw.get("original_creation_timestamp") or raw.get("creation_timestamp")
    return ListingInput(
        source=SOURCE,
        source_listing_id=str(raw["listing_id"]),
        listing_url=raw.get("url"),
        seller_id=str(raw.get("shop_id")) if raw.get("shop_id") else None,
        raw_title=(raw.get("title") or "(untitled)")[:500],
        raw_description=(raw.get("description") or "")[:4000],
        item_price=price,
        currency=(raw.get("price") or {}).get("currency_code") or "USD",
        shipping_status=Shipping.UNKNOWN,
        image_urls=images[:20],
        stated_weight=_weight_g(raw.get("item_weight"), raw.get("item_weight_unit")),
        weight_unit="g",
        listing_time=datetime.fromtimestamp(created, UTC) if created else None,
        raw_search_phrase=query,
        original_search_phrase=query,
        effective_query=query,
        buying_options=["FIXED_PRICE"],
        discovery_mode="production",
        style_description=", ".join((raw.get("tags") or [])[:10]) or None,
    )


async def search(query):
    """Return (listings, api_calls) for one keyword query, newest first."""
    params = {
        "keywords": query,
        "sort_on": "created",
        "sort_order": "desc",
        "limit": int(os.environ.get("ETSY_PAGE_SIZE", "50")),
    }
    # Etsy matches this as a place name: "US" finds nothing, "United States" works.
    loc = os.environ.get("ETSY_SHOP_LOCATION", "United States").strip()
    loc = {"US": "United States", "USA": "United States"}.get(loc.upper(), loc)
    if loc:
        params["shop_location"] = loc
    if os.environ.get("ETSY_MIN_PRICE"):
        params["min_price"] = os.environ["ETSY_MIN_PRICE"]
    calls = 0
    async with httpx.AsyncClient(timeout=30, headers=_headers()) as client:
        r = await client.get(f"{BASE}/listings/active", params=params)
        calls += 1
        if r.status_code in (401, 403):
            raise NotReady(f"HTTP {r.status_code}: {r.text[:200]}")
        r.raise_for_status()
        results = r.json().get("results") or []
        if not results:
            return [], calls
        ids = [str(x["listing_id"]) for x in results if x.get("state", "active") == "active"]
        images = {}
        for i in range(0, len(ids), 100):
            chunk = ids[i : i + 100]
            r2 = await client.get(
                f"{BASE}/listings/batch",
                params={"listing_ids": ",".join(chunk), "includes": "Images"},
            )
            calls += 1
            if r2.status_code == 200:
                for row in r2.json().get("results") or []:
                    urls = [
                        im.get("url_570xN") or im.get("url_fullxfull")
                        for im in (row.get("images") or [])
                        if im.get("url_570xN") or im.get("url_fullxfull")
                    ]
                    images[str(row["listing_id"])] = urls
    out = []
    for raw in results:
        if str(raw.get("state", "active")) != "active":
            continue
        try:
            out.append(to_listing(raw, images.get(str(raw["listing_id"]), []), query))
        except Exception:
            continue
    return out, calls
