"""Unattended discovery scheduler.

    python -m radar.scheduler seed            # load fixtures/seed_queries.json into search groups
    python -m radar.scheduler run --once      # one discovery cycle, then exit
    python -m radar.scheduler run             # cycle every RADAR_SCHEDULE_HOURS until Ctrl-C
    python -m radar.scheduler status          # recent runs, daily call count, newest finds

Reads .env from the repository root (or RADAR_ENV_FILE) so credentials never pass
through a shell history. Reuses the existing eBay adapter, normalization, dedup and
DiscoveredItem storage in radar.discovery.service.run_discovery. Deliberately has no
external receiver, cohort or per-run approval dependency: a failed cycle is logged and
the next cycle runs on schedule.
"""

import argparse
import asyncio
import json
import logging
import os
import signal
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import func, select

from radar.db import (
    DiscoveredItem,
    DiscoveryGroup,
    DiscoveryRun,
    SearchExecution,
    Setting,
    database,
)
from radar.discovery.config import Budgets, Connection, DiscoveryError, GroupInput, SearchOptions
from radar.discovery.service import run_discovery

log = logging.getLogger("radar.scheduler")

ROOT = Path(__file__).resolve().parent.parent
BUDGET_SETTING_ID = 3  # owner Budgets row used by run_discovery
COUNTER_SETTING_ID = 91  # daily API call ledger (90 is used by buyer_worker)
SEED_PREFIX = "seed-"


# ---------------------------------------------------------------- environment


def load_env(path=None):
    """Minimal .env loader: KEY=VALUE lines, # comments, optional quotes. Never overrides
    variables already set in the environment."""
    path = Path(path or os.environ.get("RADAR_ENV_FILE") or ROOT / ".env")
    if not path.is_file():
        return None
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(key, value)
    return path


def settings():
    return dict(
        mode=os.environ.get("EBAY_MODE", "fixture"),
        hours=float(os.environ.get("RADAR_SCHEDULE_HOURS", "1")),
        daily_limit=int(os.environ.get("RADAR_DAILY_CALL_LIMIT", "4000")),
        queries=Path(os.environ.get("RADAR_QUERIES", ROOT / "fixtures" / "seed_queries.json")),
        page_size=int(os.environ.get("RADAR_PAGE_SIZE", "50")),
        pages=int(os.environ.get("RADAR_PAGES_PER_QUERY", "1")),
    )


# ---------------------------------------------------------------- seeding


def seed(sessions, path, page_size, pages):
    """Create one DiscoveryGroup per (seed group, buying mode). Idempotent: existing
    seed groups are replaced, owner-created groups are untouched."""
    spec = json.loads(Path(path).read_text(encoding="utf-8"))
    modes = spec.get("buying_options", ["FIXED_PRICE", "AUCTION"])
    sorts = spec.get("sorts", {"FIXED_PRICE": "newlyListed", "AUCTION": "endingSoonest"})
    created = 0
    with sessions.begin() as s:
        for row in s.scalars(select(DiscoveryGroup)):
            if row.id.startswith(SEED_PREFIX):
                s.delete(row)
        s.flush()
        for group in spec["groups"]:
            slug = "".join(c if c.isalnum() else "-" for c in group["name"].lower()).strip("-")
            for mode in modes:
                data = GroupInput(
                    name=f"{group['name']} [{mode}]",
                    enabled=True,
                    queries=group["queries"],
                    options=SearchOptions(buying_format=mode, sort=sorts.get(mode, "newlyListed")),
                )
                s.add(
                    DiscoveryGroup(
                        id=f"{SEED_PREFIX}{slug}-{mode.lower()}",
                        data=data.model_dump(mode="json"),
                    )
                )
                created += 1
        total_queries = sum(len(g["queries"]) for g in spec["groups"]) * len(modes)
        limits = Budgets(
            enabled=True,
            max_queries=min(200, total_queries),
            max_results=min(5000, total_queries * page_size * pages),
            max_pages_per_query=pages,
            page_size=page_size,
        )
        row = s.get(Setting, BUDGET_SETTING_ID)
        if row:
            row.data = limits.model_dump(mode="json")
        else:
            s.add(Setting(id=BUDGET_SETTING_ID, data=limits.model_dump(mode="json")))
    return created, total_queries, limits


# ---------------------------------------------------------------- daily ledger


def _today():
    return datetime.now(UTC).strftime("%Y-%m-%d")


def ledger(session):
    row = session.get(Setting, COUNTER_SETTING_ID)
    data = dict(row.data) if row else {}
    if data.get("day") != _today():
        data = {"day": _today(), "calls": 0, "cycles": 0}
    return row, data


def record_calls(sessions, calls):
    with sessions.begin() as s:
        row, data = ledger(s)
        data["calls"] += calls
        data["cycles"] += 1
        if row:
            row.data = data
        else:
            s.add(Setting(id=COUNTER_SETTING_ID, data=data))
        return data


def calls_today(sessions):
    with sessions() as s:
        return ledger(s)[1]["calls"]


# ---------------------------------------------------------------- one cycle


async def cycle(sessions, connection, mode, daily_limit):
    used = calls_today(sessions)
    if used >= daily_limit:
        log.warning("daily call limit reached (%d/%d); skipping cycle", used, daily_limit)
        return None
    started = time.monotonic()
    before = _max_run_id(sessions)
    try:
        await run_discovery(sessions, connection, mode, approved_run=True)
    except DiscoveryError as e:
        log.error("discovery refused: %s: %s", e.code, e)
        return None
    run = _latest_run(sessions, after=before)
    if run is None:
        log.error("discovery produced no run record")
        return None
    # One OAuth call plus one search call per execution (pages beyond the first are
    # counted conservatively as one more call each).
    with sessions() as s:
        pages = (
            s.scalar(select(func.count(SearchExecution.id)).where(SearchExecution.run_id == run.id))
            or 0
        )
    calls = 1 + pages
    data = record_calls(sessions, calls)
    log.info(
        "cycle done in %.0fs: run=%d status=%s new=%d dup=%d invalid=%d calls=%d today=%d/%d",
        time.monotonic() - started,
        run.id,
        run.status,
        run.new_count,
        run.duplicate_count,
        run.invalid_count,
        calls,
        data["calls"],
        daily_limit,
    )
    if run.errors:
        for err in run.errors[:5]:
            log.warning("  %s", err)
    if run.new_count:
        from radar.triage import run_triage
        from radar.triage import settings as triage_settings

        try:
            await run_triage(sessions, triage_settings())
        except Exception:
            log.exception("triage failed; discovery results are kept")
    return run


def _max_run_id(sessions):
    with sessions() as s:
        return s.scalar(select(func.max(DiscoveryRun.id))) or 0


def _latest_run(sessions, after=0):
    with sessions() as s:
        return s.scalar(
            select(DiscoveryRun).where(DiscoveryRun.id > after).order_by(DiscoveryRun.id.desc())
        )


# ---------------------------------------------------------------- loop


def run_forever(sessions, connection, cfg, once=False):
    stop = {"flag": False}

    def _stop(*_):
        stop["flag"] = True
        log.info("stop requested; finishing current cycle")

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)
    interval = cfg["hours"] * 3600
    while True:
        try:
            asyncio.run(cycle(sessions, connection, cfg["mode"], cfg["daily_limit"]))
        except Exception:
            # Never let one bad cycle kill the schedule.
            log.exception("cycle failed; will retry next interval")
        if once or stop["flag"]:
            return
        next_at = datetime.now().astimezone() + timedelta(seconds=interval)
        log.info("sleeping %.1fh; next cycle at %s", cfg["hours"], next_at.strftime("%H:%M %Z"))
        slept = 0
        while slept < interval and not stop["flag"]:
            time.sleep(min(5, interval - slept))
            slept += 5
        if stop["flag"]:
            return


# ---------------------------------------------------------------- status


def status(sessions, cfg):
    with sessions() as s:
        groups = [g for g in s.scalars(select(DiscoveryGroup)) if g.id.startswith(SEED_PREFIX)]
        nq = sum(len(g.data["queries"]) for g in groups)
        _, led = ledger(s)
        runs = list(s.scalars(select(DiscoveryRun).order_by(DiscoveryRun.id.desc()).limit(5)))
        total = s.scalar(select(func.count(DiscoveredItem.id))) or 0
        newest = list(
            s.scalars(select(DiscoveredItem).order_by(DiscoveredItem.id.desc()).limit(10))
        )
    print(f"mode={cfg['mode']}  seed groups={len(groups)}  queries/cycle={nq}")
    print(f"today: {led['calls']} calls in {led['cycles']} cycles (limit {cfg['daily_limit']})")
    print(f"discovered items total: {total}")
    print("\nrecent runs:")
    for r in runs:
        print(
            f"  #{r.id} {r.started_at:%m-%d %H:%M} {r.mode:<10} {r.status:<22} "
            f"new={r.new_count} dup={r.duplicate_count} errors={len(r.errors or [])}"
        )
    print("\nnewest finds:")
    for it in newest:
        n = it.normalized or {}
        price = n.get("item_price") or "?"
        ship = n.get("shipping_cost")
        ship = f"+{ship} ship" if ship not in (None, "") else "ship ?"
        ends = (n.get("item_end_time") or "")[:16].replace("T", " ")
        opts = "/".join(n.get("buying_options") or [])[:12]
        title = (n.get("raw_title") or "")[:60]
        print(f"  ${str(price):>8} {ship:<12} {opts:<12} {ends:<16} {title}")
        print(f"           via {n.get('raw_search_phrase', '')!r}  {n.get('listing_url', '')}")


# ---------------------------------------------------------------- main


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("command", choices=["seed", "run", "status"])
    parser.add_argument("--once", action="store_true", help="run a single cycle and exit")
    parser.add_argument("--env", help="path to .env (default: repo root .env)")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )
    env_path = load_env(args.env)
    log.info("env file: %s", env_path or "(none; using process environment)")
    cfg = settings()
    engine, sessions = database()
    try:
        if args.command == "seed":
            created, nq, limits = seed(sessions, cfg["queries"], cfg["page_size"], cfg["pages"])
            print(
                f"seeded {created} groups, {nq} queries per cycle; "
                f"budget max_results={limits.max_results} page_size={limits.page_size}"
            )
        elif args.command == "status":
            status(sessions, cfg)
        else:
            connection = Connection.from_env()
            try:
                connection.require(cfg["mode"], approved_run=True)
            except DiscoveryError as e:
                log.error("cannot start in mode %r: %s: %s", cfg["mode"], e.code, e)
                return 2
            with sessions() as s:
                if not any(g.id.startswith(SEED_PREFIX) for g in s.scalars(select(DiscoveryGroup))):
                    log.error("no seed groups; run `python -m radar.scheduler seed` first")
                    return 2
            log.info(
                "starting: mode=%s every %.1fh, daily limit %d calls",
                cfg["mode"],
                cfg["hours"],
                cfg["daily_limit"],
            )
            run_forever(sessions, connection, cfg, once=args.once)
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(main())
