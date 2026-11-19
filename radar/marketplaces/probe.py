"""Show what Etsy and Bonanza actually return for one keyword, without storing anything.

    python -m radar.marketplaces.probe "navajo cuff"

Prints status codes, result counts and field NAMES only: no keys, no listing content beyond one
title per variant. Used to diagnose "0 listings, 0 errors".
"""

import argparse
import asyncio
import json
import os

import httpx

from radar.marketplaces import bonanza, etsy


def _shape(value, depth=0):
    if isinstance(value, dict):
        if depth >= 3:
            return "{…}"
        return {k: _shape(v, depth + 1) for k, v in list(value.items())[:25]}
    if isinstance(value, list):
        return [f"{len(value)} items", _shape(value[0], depth + 1)] if value else []
    return type(value).__name__


async def probe_etsy(query):
    if not etsy.configured():
        print("etsy: not configured")
        return
    variants = {
        "as the radar asks (shop_location=US, newest)": dict(
            keywords=query, sort_on="created", sort_order="desc", limit=5, shop_location="US"
        ),
        "shop_location=United States": dict(keywords=query, limit=5, shop_location="United States"),
        "no location, newest": dict(keywords=query, sort_on="created", sort_order="desc", limit=5),
        "keywords only": dict(keywords=query, limit=5),
    }
    async with httpx.AsyncClient(timeout=30, headers=etsy._headers()) as client:
        for label, params in variants.items():
            r = await client.get(f"{etsy.BASE}/listings/active", params=params)
            try:
                data = r.json()
            except ValueError:
                data = {}
            results = data.get("results") or []
            first = (results[0].get("title") or "")[:60] if results else ""
            print(
                f"etsy | {label}: HTTP {r.status_code}, count={data.get('count')}, "
                f"results={len(results)} {first!r}"
            )
            if r.status_code != 200:
                print("     ", r.text[:200])
        if results:
            print("etsy result fields:", sorted(results[0])[:40])


async def probe_bonanza(query):
    if not bonanza.configured():
        print("bonanza: not configured")
        return
    bodies = {
        "as the radar asks (newest, 50/page)": {
            "keywords": query,
            "sortOrder": "StartTimeNewest",
            "paginationInput": {"entriesPerPage": 50, "pageNumber": 1},
        },
        "keywords only": {"keywords": query},
    }
    async with httpx.AsyncClient(timeout=30, headers=bonanza._headers()) as client:
        for label, inner in bodies.items():
            payload = json.dumps({"findItemsByKeywordsRequest": inner})
            for style, kwargs in {
                "json body": dict(content=payload),
                "form body": dict(content="findItemsByKeywordsRequest=" + json.dumps(inner)),
            }.items():
                r = await client.post(bonanza.URL, **kwargs)
                try:
                    data = r.json()
                except ValueError:
                    data = {}
                print(
                    f"bonanza | {label} | {style}: HTTP {r.status_code}, "
                    f"items={len(bonanza.items_of(data))}"
                )
                print("      shape:", json.dumps(_shape(data))[:700])


def main(argv=None):
    from radar.scheduler import load_env

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("query", nargs="?", default="navajo cuff")
    parser.add_argument("--env")
    args = parser.parse_args(argv)
    load_env(args.env)
    os.environ.setdefault("PYTHONWARNINGS", "ignore")
    asyncio.run(probe_etsy(args.query))
    asyncio.run(probe_bonanza(args.query))


if __name__ == "__main__":
    main()
