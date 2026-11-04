"""Observed query relationships and bounded vocabulary; no source requests or writes."""

import re
from collections import Counter, defaultdict

from sqlalchemy import select

from radar.db import SearchPattern
from radar.schemas import Shipping
from radar.sourcing_catalog import catalog_entries
from radar.sourcing_feed import build_feed

MAX_SUGGESTIONS = 100
MAX_PHRASES = 200
MAX_EVIDENCE_LINKS = 5
ACTIONS = {
    "Interested": "interested",
    "Passed": "passed",
    "False positive": "false_positive",
    "Purchased": "purchased",
    "Sold": "sold",
}


def _feedback(cards):
    latest = {
        card["listing_id"]: card["latest_feedback"]["action"]
        for card in cards
        if card["listing_id"] is not None and card["latest_feedback"]
    }
    counts = Counter(latest.values())
    return dict(
        reviewed_items=len(latest), **{key: counts[action] for action, key in ACTIONS.items()}
    )


def _links(cards):
    links = []
    for card in cards[:MAX_EVIDENCE_LINKS]:
        if card["listing_id"] is not None:
            url = f"/listings/{card['listing_id']}"
        else:
            kind = (
                "discovery/items"
                if any(e["kind"] == "discovery" for e in card["query_evidence"])
                else "inbox/candidates"
            )
            url = f"/{kind}/{card['candidate_ids'][0]}"
        links.append(dict(key=card["key"], url=url, title=card["item"].raw_title))
    return links


def evaluate_searches(session, prefs):
    """Count surviving real records, including closed/ended items, without autoflush.

    An attributed observation establishes a recorded source-query relationship,
    not HTTP dispatch statistics or literal marketplace matching. No execution
    row alone, owner phrase or title overlap is counted as a query observation.
    """
    with session.no_autoflush:
        cards = [card for card in build_feed(session, prefs) if card["dataset"] == "real"]
        patterns = list(session.scalars(select(SearchPattern).order_by(SearchPattern.phrase)))
    by_key = {card["key"]: card for card in cards}
    sources, groups = {}, {}
    for card in cards:
        source = sources.setdefault(
            card["item"].source,
            dict(
                source=card["item"].source,
                items=0,
                observations=0,
                attributed_observations=0,
                missing_cost_items=0,
                unknown_shipping_items=0,
                unavailable_shipping_items=0,
            ),
        )
        source["items"] += 1
        source["observations"] += card["observation_count"]
        source["missing_cost_items"] += bool(card["missing_costs"])
        source["unknown_shipping_items"] += card["item"].shipping_status not in (
            Shipping.CONFIRMED_TO_ALASKA,
            Shipping.CONFIRMED_UNAVAILABLE,
        )
        source["unavailable_shipping_items"] += (
            card["item"].shipping_status == Shipping.CONFIRMED_UNAVAILABLE
        )
        for evidence in card["query_evidence"]:
            if (
                evidence["kind"] not in ("discovery", "alert")
                or evidence["mode"] != "production"
                or evidence["observation_id"] is None
                or evidence["query"] is None
                or evidence["attribution"] not in ("SOURCE QUERY", "SOURCE CORRECTED")
            ):
                continue
            source["attributed_observations"] += 1
            key = (evidence["source"], evidence["query"], evidence["attribution"])
            group = groups.setdefault(key, dict(observations=0, items=set(), corrections=set()))
            group["observations"] += 1
            group["items"].add(card["key"])
            if evidence["corrected_query"] is not None:
                group["corrections"].add(evidence["corrected_query"])
    phrases, totals = [], defaultdict(lambda: dict(observations=0, corrected=0, items=set()))
    for (source, phrase, attribution), group in sorted(groups.items()):
        supported = [by_key[key] for key in sorted(group["items"])]
        phrases.append(
            dict(
                source=source,
                phrase=phrase,
                attribution=attribution,
                observations=group["observations"],
                unique_items=len(supported),
                corrected_queries=sorted(group["corrections"]),
                evidence=_links(supported),
                **_feedback(supported),
            )
        )
        total = totals[phrase]
        total["observations"] += group["observations"]
        total["corrected"] += group["observations"] if attribution == "SOURCE CORRECTED" else 0
        total["items"].update(group["items"])
    candidates = {entry["phrase"]: "CATALOG" for entry in catalog_entries()}
    extras = [pattern.phrase for pattern in patterns if pattern.phrase not in candidates]
    capacity = max(0, MAX_SUGGESTIONS - len(candidates))
    candidates.update((phrase, "EXISTING PATTERN") for phrase in extras[:capacity])
    suggestions = []
    for phrase, origin in candidates.items():
        total = totals[phrase]
        supported = [by_key[key] for key in sorted(total["items"])]
        # Preserve exact spelling and whitespace; boundaries avoid ring/earrings matches.
        matcher = re.compile(r"(?<!\w)" + re.escape(phrase) + r"(?!\w)", re.IGNORECASE)
        title_cards = [card for card in cards if matcher.search(card["item"].raw_title)]
        suggestions.append(
            dict(
                phrase=phrase,
                origin=origin,
                tested=bool(total["observations"]),
                observations=total["observations"],
                corrected_observations=total["corrected"],
                unique_items=len(supported),
                title_matches=len(title_cards),
                evidence_label="OBSERVED SOURCE MATCH"
                if total["observations"]
                else "UNTESTED SEARCH",
                evidence=_links(supported or title_cards),
                **_feedback(supported),
            )
        )
    suggestions.sort(
        key=lambda row: (
            -(row["interested"] - row["false_positive"]),
            -row["unique_items"],
            -row["observations"],
            -row["title_matches"],
            row["phrase"],
        )
    )
    return dict(
        sources=sorted(sources.values(), key=lambda row: row["source"]),
        phrases=phrases[:MAX_PHRASES],
        suggestions=suggestions,
        omitted_phrases=max(0, len(phrases) - MAX_PHRASES),
        omitted_suggestions=max(0, len(extras) - capacity),
        limitations=[
            "Counts cover surviving real-data records in this database, including closed/ended "
            "items; they do not establish complete private history or current availability.",
            "Fixture, sandbox, mixed provenance and deletion tombstones are excluded.",
            "Inventory uses the current owner source label; phrase rows retain the original "
            "recorded source. Missing-cost counts identify absent amounts, not every evidence gap.",
            "Only attributed source observations count as observed query relationships. "
            "Owner phrases, provisional/recommended alerts and title matches are not search yield.",
            "Source corrections remain separate. No literal matching, material verification, "
            "profitability, HTTP attempt count or complete query yield is established.",
            "Failed bookkeeping and zero-result executions are outside this surviving-item view.",
            "Owner outcomes use one latest feedback action per distinct linked listing, separate "
            "from automated screening. Per-query item counts must not be summed across queries.",
            "Suggestions preserve the 65 catalog phrases plus up to 35 existing patterns; "
            "title overlap is vocabulary evidence only and never starts a search.",
        ],
    )
