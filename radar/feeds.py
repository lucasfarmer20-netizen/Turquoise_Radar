"""Trading-post and shop product feeds.

Shopify stores publish a standard Atom feed of their products at
    https://<store>/collections/all.atom
(observed on turquoisetraders.com, Nov 2026). Reading it is reading a published feed, not a
page: each <entry> carries the title, product link, publish time, the price in
<s:variant><s:price currency="USD">, the product type and vendor, tags such as
"artist_chee-jerry" and "tribal-affiliation_Navajo", and a summary whose HTML holds one image.

    RADAR_SHOP_FEEDS=https://turquoisetraders.com,https://example-post.com
    RADAR_SHOP_FEED_TYPES=jewel,bracelet,ring,...     product types to keep (substring match)

These are retail prices, so most entries will screen as fair or overpriced and be archived.
The value is statement pieces at fair prices with the maker and tribe documented by the seller.
Tags are the SELLER's attribution: evidence to verify, never proof.

    python -m radar.feeds run        # fetch every feed once
"""

import argparse
import asyncio
import logging
import os
import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime
from urllib.parse import urlsplit

import httpx

from radar.schemas import ListingInput, Shipping

log = logging.getLogger("radar.feeds")
ATOM = "{http://www.w3.org/2005/Atom}"
SHOP = "{http://jadedpixel.com/-/spec/shopify}"
MAX_BYTES = 3_000_000
DEFAULT_FEEDS = "https://turquoisetraders.com"
DEFAULT_TYPES = (
    "jewel,bracelet,cuff,ring,necklace,pendant,earring,buckle,bolo,concho,pin,brooch,"
    "watch,squash,belt,pawn"
)


def settings():
    feeds = [
        f.strip().rstrip("/")
        for f in os.environ.get("RADAR_SHOP_FEEDS", DEFAULT_FEEDS).split(",")
        if f.strip()
    ]
    types = [
        t.strip().casefold()
        for t in os.environ.get("RADAR_SHOP_FEED_TYPES", DEFAULT_TYPES).split(",")
        if t.strip()
    ]
    return dict(feeds=[f for f in feeds if f.startswith("https://")], types=types)


def feed_url(base):
    return base if base.endswith(".atom") else f"{base}/collections/all.atom"


def _text(html):
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html or "", flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    for a, b in (("&amp;", "&"), ("&nbsp;", " "), ("&quot;", '"'), ("&#39;", "'")):
        text = text.replace(a, b)
    return " ".join(text.split())


def _wanted(product_type, title, types):
    if not types:
        return True
    hay = f"{product_type} {title}".casefold()
    return any(t in hay for t in types)


def parse(xml_text, base, types=None):
    """ListingInput rows from one Shopify Atom document. Entries without a price or link are
    skipped; nothing is invented."""
    if len(xml_text) > MAX_BYTES or "<!DOCTYPE" in xml_text or "<!ENTITY" in xml_text:
        raise ValueError("feed rejected: too large or declares entities")
    root = ET.fromstring(xml_text)
    host = urlsplit(base).netloc.lower()
    shop = (root.findtext(f"{ATOM}title") or host).strip()
    out = []
    for entry in root.iter(f"{ATOM}entry"):
        title = (entry.findtext(f"{ATOM}title") or "").strip()
        link = next(
            (
                el.get("href")
                for el in entry.findall(f"{ATOM}link")
                if el.get("rel", "alternate") == "alternate" and el.get("href")
            ),
            None,
        )
        price_el = entry.find(f"{SHOP}variant/{SHOP}price")
        if not title or not link or price_el is None or not (price_el.text or "").strip():
            continue
        if urlsplit(link).netloc.lower() != host or not link.startswith("https://"):
            continue  # a feed may only point at its own store
        product_type = (entry.findtext(f"{SHOP}type") or "").strip()
        if not _wanted(product_type, title, types):
            continue
        summary = entry.findtext(f"{ATOM}summary") or ""
        image = re.search(r'<img[^>]+src="(https://[^"]+)"', summary)
        tags = [t.text.strip() for t in entry.findall(f"{SHOP}tag") if t.text and t.text.strip()]
        said = [t.replace("_", ": ").replace("-", " ") for t in tags][:12]
        description = _text(summary)
        if said:
            description = f"{description}\nSeller's tags: {'; '.join(said)}"
        product_id = (entry.findtext(f"{ATOM}id") or link).rstrip("/").rsplit("/", 1)[-1]
        published = entry.findtext(f"{ATOM}published") or entry.findtext(f"{ATOM}updated")
        try:
            listed = datetime.fromisoformat(published) if published else None
        except ValueError:
            listed = None
        try:
            out.append(
                ListingInput(
                    source=f"Shop: {shop}"[:80],
                    source_listing_id=f"{host}-{product_id}"[:120],
                    listing_url=link,
                    seller_id=(entry.findtext(f"{SHOP}vendor") or shop)[:120],
                    raw_title=title[:500],
                    raw_description=description[:4000],
                    item_price=price_el.text.strip(),
                    currency=price_el.get("currency") or "USD",
                    shipping_status=Shipping.UNKNOWN,
                    image_urls=[image[1]] if image else [],
                    listing_time=listed,
                    raw_search_phrase=f"feed:{host}",
                    original_search_phrase=f"feed:{host}",
                    effective_query=f"feed:{host}",
                    buying_options=["FIXED_PRICE"],
                    discovery_mode="production",
                    category=product_type or "UNKNOWN",
                    style_description="; ".join(said) or None,
                )
            )
        except Exception:  # noqa: BLE001 - one malformed entry must not drop the feed
            continue
    return out


async def run(sessions, cfg=None):
    """Fetch every configured feed and store its jewelry entries. Returns stats."""
    from radar.marketplaces import store

    cfg = cfg or settings()
    stats = dict(feeds=0, listings=0, new=0, refreshed=0, errors=0)
    if not cfg["feeds"]:
        return stats
    headers = {"User-Agent": "TurquoiseRadar/0.1 (feed reader)", "Accept": "application/atom+xml"}
    async with httpx.AsyncClient(timeout=30, headers=headers, follow_redirects=True) as client:
        for base in cfg["feeds"]:
            try:
                r = await client.get(feed_url(base))
                r.raise_for_status()
                # a redirect to another host is followed for the fetch, but entries must then
                # belong to the host that actually served the feed
                items = parse(r.text, str(r.url), cfg["types"])
            except Exception as e:  # noqa: BLE001
                stats["errors"] += 1
                log.warning("feed %s failed: %s", base, str(e)[:160])
                continue
            usd = [i for i in items if i.currency == "USD"]
            n, refreshed = store(sessions, usd)
            stats["feeds"] += 1
            stats["listings"] += len(usd)
            stats["new"] += n
            stats["refreshed"] += refreshed
    log.info(
        "shop feeds: %d feeds, %d listings, %d new, %d refreshed, %d errors",
        stats["feeds"],
        stats["listings"],
        stats["new"],
        stats["refreshed"],
        stats["errors"],
    )
    return stats


def main(argv=None):
    from radar.db import database
    from radar.scheduler import load_env

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["run"])
    parser.add_argument("--env")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s")
    load_env(args.env)
    engine, sessions = database()
    try:
        print(asyncio.run(run(sessions)))
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(main())
