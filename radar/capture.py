"""Deterministic suggestions only. No URL fetching or AI calls."""

import re
from urllib.parse import urlsplit

from radar.schemas import ListingInput


def suggest(text, url="", source="manual"):
    if not text.strip() or len(text) > 30000:
        raise ValueError("Paste between 1 and 30,000 characters")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    data = dict(
        source=source,
        raw_title=lines[0][:500],
        raw_description=text,
        pasted_text=text,
        capture_confirmed=False,
    )
    if url:
        data["listing_url"] = url
        host = urlsplit(url).hostname or ""
        if source == "manual":
            for domain, name in [
                ("ebay.com", "eBay"),
                ("mercari.com", "Mercari"),
                ("poshmark.com", "Poshmark"),
                ("ctbids.com", "CTBids"),
                ("estatesales.net", "EstateSales.net"),
                ("estatesales.org", "EstateSales.org"),
            ]:
                if host == domain or host.endswith("." + domain):
                    data["source"] = name
    currency = r"\$?\s*(\d+(?:,\d{3})*(?:\.\d{1,2})?)"
    price = re.search(
        r"(?:item price|asking price|current bid|price)\s*[:=]?\s*" + currency, text, re.I
    )
    if not price:
        for line in lines:
            if re.search(r"shipping|handling|tax|fee|resale", line, re.I):
                continue
            price = re.search(r"\$\s*(\d+(?:,\d{3})*(?:\.\d{1,2})?)", line)
            if price:
                break
    if price:
        data["item_price"] = price.group(1).replace(",", "")
    shipping = re.search(r"(?:shipping|postage)\s*[:=]?\s*" + currency, text, re.I)
    if shipping:
        data["shipping_cost"] = shipping.group(1).replace(",", "")
    elif re.search(r"\bfree shipping\b|\bshipping\s*:\s*free\b", text, re.I):
        data["shipping_cost"] = "0"
    weight = re.search(r"(?<![\d.])(\d+(?:\.\d+)?)\s*(dwt|pennyweights?|grams?|g)\b", text, re.I)
    if weight:
        data["stated_weight"] = weight.group(1)
        data["weight_unit"] = "dwt" if weight.group(2).lower().startswith(("dwt", "penny")) else "g"
        data["weight_kind"] = "gross"
        data["weight_evidence"] = "Unverified term extracted from owner-pasted listing text"
    count = re.search(r"\b(\d+)\s*(?:pieces?|pcs|rings?)\b|lot of\s+(\d+)\b", text, re.I)
    if count:
        data["piece_count"] = int(count.group(1) or count.group(2))
    size = re.search(r"\b(?:ring size|size)\s*[:#]?\s*(\d+(?:\.\d+|\s*1/2)?)", text, re.I)
    if size:
        data["ring_size"] = size.group(1)
    terms = re.findall(r"\b(?:sterling|925|silver[ -]plated|plated)\b", text, re.I)
    data["material_terms"] = list(dict.fromkeys(terms))
    data["material_evidence"] = (
        "plated"
        if any("plated" in t.lower() for t in terms)
        else ("seller_claim" if terms else "unknown")
    )
    # Mixed lots remain candidates for review; one plated clasp does not condemn the entire lot.
    if data.get("piece_count", 1) > 1 and re.search(r"sterling|925", text, re.I):
        data["material_evidence"] = "seller_claim"
    for key, label in [
        ("seller_id", "seller"),
        ("sale_auction_id", "auction"),
        ("source_listing_id", "listing"),
    ]:
        found = re.search(r"^" + label + r"(?: id)?\s*:\s*(.+)$", text, re.I | re.M)
        if found:
            data[key] = found.group(1).strip()
    query = re.search(r"^(?:search phrase|query|search)\s*:[ \t]?(.*)$", text, re.I | re.M)
    if query:
        data["raw_search_phrase"] = query.group(1)
    data["cost_status"] = {
        key: "ESTIMATED" for key in ("item_price", "shipping_cost") if key in data
    }
    return ListingInput.model_validate(data)
