"""Where the screening money goes, and what it bought.

    python -m radar.costs            # by source and search term, most expensive first
    python -m radar.costs --days 3   # only listings screened in the last 3 days

For each source / search term: listings screened, how many were relevant, how many scored 80+
before any price cap, how many the owner marked Interested or passed, and the recorded cost.
A term that costs money and never produces a high score or an Interested is a candidate to
drop. Costs are the radar's own records; a rescreen overwrites the earlier cost, so the
Anthropic console is the authority on totals.
"""

import argparse
import sys
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select

from radar.db import DiscoveredItem, TriageResult, database


def report(sessions, days=None):
    """Rows of dict(source, term, screened, relevant, high, interested, passed, cost, tokens)."""
    query = select(
        DiscoveredItem.source,
        func.json_extract(DiscoveredItem.normalized, "$.raw_search_phrase"),
        DiscoveredItem.inbox_status,
        TriageResult.relevant,
        func.coalesce(func.json_extract(TriageResult.data, "$.model_score"), TriageResult.score),
        TriageResult.cost_usd,
        func.json_extract(TriageResult.data, "$.tokens[0]"),
    ).join(TriageResult, TriageResult.item_id == DiscoveredItem.id)
    if days:
        since = (datetime.now(UTC) - timedelta(days=days)).replace(tzinfo=None)
        query = query.where(TriageResult.created_at >= since)
    groups = defaultdict(
        lambda: dict(
            screened=0, relevant=0, high=0, interested=0, passed=0, cost=Decimal(0), tok=0, tokn=0
        )
    )
    with sessions() as s:
        for source, term, status, relevant, score, cost, tokens in s.execute(query):
            g = groups[(source or "?", term or "(none)")]
            g["screened"] += 1
            g["relevant"] += 1 if relevant else 0
            g["high"] += 1 if relevant and (score or 0) >= 80 else 0
            g["interested"] += 1 if status in ("INTERESTED", "ENDED") else 0
            g["passed"] += 1 if status == "IRRELEVANT" else 0
            g["cost"] += Decimal(cost or 0)
            if tokens:
                g["tok"] += int(tokens)
                g["tokn"] += 1
    rows = [
        dict(
            source=k[0],
            term=k[1],
            **{x: v[x] for x in v if x not in ("tok", "tokn")},
            tokens=(v["tok"] // v["tokn"]) if v["tokn"] else None,
        )
        for k, v in groups.items()
    ]
    return sorted(rows, key=lambda r: r["cost"], reverse=True)


def money(value):
    return f"${value:.2f}"


def render(rows):
    lines = [
        f"{'source':<14}{'search term':<34}{'screened':>9}{'relev':>7}{'80+':>6}"
        f"{'int':>5}{'pass':>6}{'cost':>9}{'in-tok':>8}"
    ]
    total = Decimal(0)
    for r in rows:
        total += r["cost"]
        lines.append(
            f"{r['source'][:13]:<14}{str(r['term'])[:33]:<34}{r['screened']:>9}{r['relevant']:>7}"
            f"{r['high']:>6}{r['interested']:>5}{r['passed']:>6}{money(r['cost']):>9}"
            f"{(r['tokens'] or ''):>8}"
        )
    lines.append(f"{'TOTAL':<48}{sum(r['screened'] for r in rows):>9}{'':>24}{money(total):>9}")
    return "\n".join(lines)


def main(argv=None):
    from radar.scheduler import load_env

    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--days", type=float)
    parser.add_argument("--env")
    args = parser.parse_args(argv)
    load_env(args.env)
    engine, sessions = database()
    try:
        print(render(report(sessions, args.days)))
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(main())
