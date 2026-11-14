"""Bonanza (Bonapitit) adapter: findItemsByKeywords, newest first.

Env: BONANZA_DEV_NAME, BONANZA_CERT_NAME. Optional BONANZA_PAGE_SIZE (default 50, max 100).
Non-secure standard_request; no user token needed for search.
"""

import os
from decimal import Decimal, InvalidOperation

import httpx

from radar.schemas import ListingInput, Shipping

SOURCE = "Bonanza"
URL = "https://api.bonanza.com/api_requests/standard_request"
# Bonanza is fixed-price and mostly retail: the full 31-term list screened ~1,700 listings for
# no finds. These are the terms where an under-priced piece is plausible. BONANZA_QUERIES in
# .env replaces the list ("all" restores every seed term).
DEFAULT_QUERIES = [
    "old pawn",
    "navajo cuff",
    "zuni inlay",
    "squash blossom necklace",
    "southwestern sterling lot",
    "estate sterling jewelry lot",
]
PAUSE_SECONDS = 0.5


class NotReady(Exception):
    """Credentials present but Bonanza refuses them."""


def configured():
    return bool(
        os.environ.get("BONANZA_DEV_NAME", "").strip()
        and os.environ.get("BONANZA_CERT_NAME", "").strip()
    )


def _headers():
    return {
        "Content-Type": "application/json; charset=utf-8",
        "X-BONANZLE-API-DEV-NAME": os.environ["BONANZA_DEV_NAME"].strip(),
        "X-BONANZLE-API-CERT-NAME": os.environ["BONANZA_CERT_NAME"].strip(),
    }


def _dec(v):
    if v is None or v == "":
        return None
    if isinstance(v, dict):  # {"value": "12.34", "currencyId": "USD"}
        v = v.get("value")
    try:
        return Decimal(str(v))
    except (InvalidOperation, ValueError):
        return None


def _currency(v):
    return (v or {}).get("currencyId", "USD") if isinstance(v, dict) else "USD"


def _first(x):
    return x[0] if isinstance(x, list) and x else x


def to_listing(raw, query):
    selling = raw.get("sellingStatus") or {}
    shipping = raw.get("shippingInfo") or {}
    info = raw.get("listingInfo") or {}
    price = _dec(
        selling.get("convertedCurrentPrice")
        or selling.get("currentPrice")
        or info.get("convertedBuyItNowPrice")
        or info.get("buyItNowPrice")
        or info.get("price")
    )
    ship_cost = _dec(shipping.get("shippingServiceCost"))
    if ship_cost is None and (shipping.get("shippingType") or "").lower() == "free":
        ship_cost = Decimal(0)
    url = raw.get("viewItemURL")
    img = raw.get("galleryURL") or raw.get("pictureURLLarge")
    return ListingInput(
        source=SOURCE,
        source_listing_id=str(raw.get("itemId")) if raw.get("itemId") else None,
        listing_url=url,
        raw_title=(raw.get("title") or "(untitled)")[:500],
        raw_description=(raw.get("subtitle") or "")[:4000],
        item_price=price,
        currency=_currency(selling.get("convertedCurrentPrice") or selling.get("currentPrice")),
        shipping_cost=ship_cost,
        shipping_status=Shipping.UNKNOWN,
        image_urls=[img] if img else [],
        condition=(raw.get("condition") or {}).get("conditionDisplayName")
        if isinstance(raw.get("condition"), dict)
        else raw.get("condition"),
        raw_search_phrase=query,
        original_search_phrase=query,
        effective_query=query,
        buying_options=["FIXED_PRICE"],
        discovery_mode="production",
    )


def items_of(data):
    """The item list from a findItemsByKeywords response. Bonanza's reference documents
    searchResult.item; some responses carry item directly under the response."""
    response = data.get("findItemsByKeywordsResponse") or {}
    if not isinstance(response, dict):
        return []
    items = (response.get("searchResult") or {}).get("item") or response.get("item") or []
    if isinstance(items, dict):
        items = [items]
    return [i for i in items if isinstance(i, dict)]


async def search(query):
    body = {
        "findItemsByKeywordsRequest": {
            "keywords": query,
            "sortOrder": "StartTimeNewest",
            "paginationInput": {
                "entriesPerPage": int(os.environ.get("BONANZA_PAGE_SIZE", "50")),
                "pageNumber": 1,
            },
        }
    }
    async with httpx.AsyncClient(timeout=30, headers=_headers()) as client:
        r = await client.post(URL, json=body)
    if r.status_code in (401, 403):
        raise NotReady(f"HTTP {r.status_code}: {r.text[:200]}")
    r.raise_for_status()
    data = r.json()
    response = data.get("findItemsByKeywordsResponse") or {}
    ack = data.get("ack") or (response.get("ack") if isinstance(response, dict) else None)
    if ack != "Success":
        msg = str(data.get("errorMessage") or data)[:200]
        if "dev" in msg.lower() or "cert" in msg.lower() or "auth" in msg.lower():
            raise NotReady(msg)
        raise RuntimeError(msg)
    items = items_of(data)
    out = []
    for raw in items:
        try:
            out.append(to_listing(raw, query))
        except Exception:
            continue
    return out, 1
