"""Unattended discovery scheduler.

    python -m radar.scheduler seed            # load fixtures/seed_queries.json into search groups
    python -m radar.scheduler run --once      # one discovery cycle, then exit
    python -m radar.scheduler run             # cycle every RADAR_SCHEDULE_HOURS until Ctrl-C
    python -m radar.scheduler status          # recent runs, daily call count, newest finds
    python -m radar.scheduler prune-foreign   # mark stored items located outside the US as passed

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
from datetime import UTC, datetime
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


# ---------------------------------------------------------------- data dir / state


def data_dir():
    """Directory holding the SQLite file; also used for the scan trigger and state file."""
    url = os.environ.get("RADAR_DATABASE_URL", "sqlite:///./data/radar.db")
    path = url.split("sqlite:///", 1)[1] if url.startswith("sqlite:///") else "./data/radar.db"
    return Path(path).expanduser().resolve().parent


def trigger_path():
    return data_dir() / "scan-now"


def state_path():
    return data_dir() / "scheduler-state.json"


def write_state(**fields):
    try:
        state_path().parent.mkdir(parents=True, exist_ok=True)
        current = read_state()
        current.update(fields)
        current["updated_at"] = datetime.now(UTC).isoformat()
        tmp = state_path().with_suffix(".tmp")
        tmp.write_text(json.dumps(current), encoding="utf-8")
        tmp.replace(state_path())
    except OSError as e:  # state is advisory; never fail a cycle over it
        log.debug("state write failed: %s", e)


def read_state():
    try:
        return json.loads(state_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def request_scan():
    """Called by the web app: ask the running scheduler for an immediate cycle."""
    trigger_path().parent.mkdir(parents=True, exist_ok=True)
    trigger_path().write_text(datetime.now(UTC).isoformat(), encoding="utf-8")


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
        fast_minutes=float(os.environ.get("RADAR_FAST_MINUTES", "5")),
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
        from radar.discovery.service import DEFAULT_GROUPS

        for row in s.scalars(select(DiscoveryGroup)):
            if row.id.startswith(SEED_PREFIX):
                s.delete(row)
            elif row.id in DEFAULT_GROUPS and row.data.get("enabled", True):
                # Built-in groups would otherwise outrank the owner's seed list and
                # crowd it out of the per-cycle budget. Disable, don't delete.
                row.data = {**row.data, "enabled": False}
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
        s.flush()
        enabled_total = sum(
            len(g.data.get("queries") or [])
            for g in s.scalars(select(DiscoveryGroup))
            if g.data.get("enabled", True)
        )
        total_queries = max(total_queries, enabled_total)
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


def add_calls(sessions, calls):
    """Count API calls against today's ledger without counting a cycle."""
    with sessions.begin() as s:
        row, data = ledger(s)
        data["calls"] += calls
        if row:
            row.data = data
        else:
            s.add(Setting(id=COUNTER_SETTING_ID, data=data))


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
    try:
        from radar.category_sweep import run as run_sweep

        await run_sweep(sessions)
    except Exception:
        log.exception("category sweep failed; will retry next cycle")
    try:
        from radar.marketplaces import run_marketplaces

        await run_marketplaces(sessions)
    except Exception:
        log.exception("marketplace adapters failed; will retry next cycle")
    try:
        from radar.foreign import run as run_foreign

        foreign_stats = await run_foreign(sessions)
        if foreign_stats:
            add_calls(sessions, foreign_stats["calls"])
    except Exception:
        log.exception("foreign marketplaces failed; will retry next cycle")
    try:
        from radar.feeds import run as run_feeds

        await run_feeds(sessions)
    except Exception:
        log.exception("shop feeds failed; will retry next cycle")
    try:
        from radar.mailbox import poll as poll_mailbox

        poll_mailbox(sessions)
    except Exception:
        log.exception("mailbox poll failed; will retry next cycle")
    # Always run: with a per-cycle cap, a backlog must keep draining even in an hour that
    # found nothing new. run_triage returns at once when nothing is waiting.
    try:
        from radar.triage import run_triage
        from radar.triage import settings as triage_settings

        await run_triage(sessions, triage_settings())
    except Exception:
        log.exception("triage failed; discovery results are kept")
    try:
        from radar.triage import run_deep
        from radar.triage import settings as triage_settings

        await run_deep(sessions, triage_settings())
    except Exception:
        log.exception("deep pass failed")
    try:
        from radar.queue import purge, sweep

        sweep(sessions)
        purge(sessions)
    except Exception:
        log.exception("archive sweep failed")
    try:
        from radar.watch import refresh as refresh_watched

        await refresh_watched(sessions)
        from radar.triage import reconcile

        changed, total = reconcile(sessions)
        if changed:
            log.info("reconciled %d of %d verdicts against current prices", changed, total)
    except Exception:
        log.exception("auction watch / reconcile failed")
    try:
        from radar.notify import notify_pending

        await notify_pending(sessions)
    except Exception:
        log.exception("notification failed; will retry next cycle")
    try:
        from radar.sourcing_calendar import remind

        await remind(sessions)
    except Exception:
        log.exception("calendar reminder failed; will retry next cycle")
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


FAST_RESERVE_CALLS = 400


async def fast_cycle(sessions, daily_limit):
    """Between full cycles: fetch the newest listings in the jewelry category, screen only
    those, and notify at once. A mispriced Buy It Now sells in minutes; the hourly cycle is
    too slow for it. Returns the number of new listings found."""
    from radar.category_sweep import run as run_sweep

    if calls_today(sessions) >= daily_limit - FAST_RESERVE_CALLS:
        return 0  # leave the rest of today's eBay calls to the full cycles
    with sessions() as s:
        before = s.scalar(select(func.max(DiscoveredItem.id))) or 0
    stats = await run_sweep(sessions, fast=True)
    if stats:
        add_calls(sessions, stats["calls"])  # eBay calls only; Etsy has its own allowance
    try:
        from radar.marketplaces import run_marketplaces

        await run_marketplaces(sessions, fast=True)
    except Exception:
        log.exception("fast lane: marketplace check failed")
    with sessions() as s:
        ids = list(s.scalars(select(DiscoveredItem.id).where(DiscoveredItem.id > before)))
    from radar.notify import notify_pending
    from radar.triage import run_triage
    from radar.triage import settings as triage_settings

    if ids:
        await run_triage(sessions, triage_settings(), item_ids=ids)
    # Always check: an auction already known may just have entered its last hour.
    await notify_pending(sessions)
    return len(ids)


def record_fast(found, ok, now=None):
    """Keep a rolling hour of fast-lane checks in the state file, so the page can show that
    the lane is alive even when it is finding nothing."""
    now = now or datetime.now(UTC)
    cutoff = (now.timestamp()) - 3600
    recent = [r for r in (read_state().get("fast_recent") or []) if r[0] >= cutoff]
    recent.append([now.timestamp(), int(found or 0), bool(ok)])
    write_state(fast_recent=recent[-40:], fast_at=now.isoformat(), fast_ok=bool(ok))


def fast_summary(state, now=None):
    """dict(at, ok, checks, new) for the last hour, or None when the lane has never run."""
    if not state.get("fast_at"):
        return None
    now = now or datetime.now(UTC)
    cutoff = now.timestamp() - 3600
    recent = [r for r in (state.get("fast_recent") or []) if r[0] >= cutoff]
    try:
        at = datetime.fromisoformat(state["fast_at"])
    except (TypeError, ValueError):
        return None
    return dict(
        at=at,
        ok=bool(state.get("fast_ok", True)),
        checks=len(recent),
        new=sum(r[1] for r in recent),
        stale=(now - at).total_seconds() > 1200,
    )


MIN_REST_SECONDS = 600


def next_boundary(now, interval, min_rest=MIN_REST_SECONDS):
    """The next clock-aligned start (hourly -> top of the hour), so cycles keep a fixed cadence
    instead of drifting by however long each one ran. A boundary closer than min_rest is
    skipped so a long cycle is not followed immediately by another."""
    interval = max(60, int(interval))
    epoch = now.timestamp()
    nxt = (int(epoch) // interval + 1) * interval
    if nxt - epoch < min(min_rest, interval / 2):
        nxt += interval
    return datetime.fromtimestamp(nxt, UTC)


HEARTBEAT_SECONDS = 60
STALE_AFTER_SECONDS = 300


async def with_heartbeat(coro, every=HEARTBEAT_SECONDS):
    """Run a cycle while touching the state file, so the page can tell a live cycle from a
    'running' flag left behind by a restart or a hang."""

    async def beat():
        while True:
            await asyncio.sleep(every)
            write_state(running=True)

    task = asyncio.create_task(beat())
    try:
        return await coro
    finally:
        task.cancel()


def cycle_running(state, now=None):
    """True only while a cycle is live: flagged running and heard from recently."""
    if not state.get("running"):
        return False
    try:
        seen = datetime.fromisoformat(state.get("updated_at"))
    except (TypeError, ValueError):
        return False
    return ((now or datetime.now(UTC)) - seen).total_seconds() <= STALE_AFTER_SECONDS


def run_forever(sessions, connection, cfg, once=False):
    stop = {"flag": False}

    def _stop(*_):
        stop["flag"] = True
        log.info("stop requested; finishing current cycle")

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)
    interval = cfg["hours"] * 3600
    reason = "startup"
    while True:
        write_state(running=True, reason=reason, started_at=datetime.now(UTC).isoformat())
        try:
            run = asyncio.run(
                with_heartbeat(cycle(sessions, connection, cfg["mode"], cfg["daily_limit"]))
            )
            summary = (
                dict(run_id=run.id, status=run.status, new=run.new_count, dup=run.duplicate_count)
                if run
                else None
            )
        except Exception:
            # Never let one bad cycle kill the schedule.
            log.exception("cycle failed; will retry next interval")
            summary = dict(status="FAILED")
        if trigger_path().exists():
            trigger_path().unlink(missing_ok=True)
        if once or stop["flag"]:
            write_state(running=False, last=summary, next_at=None)
            return
        next_at = next_boundary(datetime.now(UTC), interval)
        wait = (next_at - datetime.now(UTC)).total_seconds()
        write_state(
            running=False,
            last=summary,
            finished_at=datetime.now(UTC).isoformat(),
            next_at=next_at.isoformat(),
            interval_hours=cfg["hours"],
        )
        log.info(
            "sleeping %.0f min; next cycle at %s",
            wait / 60,
            next_at.astimezone().strftime("%H:%M %Z"),
        )
        reason = "scheduled"
        fast_every = cfg.get("fast_minutes", 0) * 60
        next_fast = time.monotonic() + fast_every
        while datetime.now(UTC) < next_at and not stop["flag"]:
            time.sleep(max(0.0, min(5, (next_at - datetime.now(UTC)).total_seconds())))
            if trigger_path().exists():
                log.info("scan requested from the web app; running now")
                reason = "requested"
                break
            # Fast lane between full cycles, unless one is about to start anyway.
            if (
                fast_every
                and time.monotonic() >= next_fast
                and (next_at - datetime.now(UTC)).total_seconds() > 120
            ):
                found, ok = 0, True
                try:
                    found = asyncio.run(fast_cycle(sessions, cfg["daily_limit"]))
                except Exception:
                    ok = False
                    log.exception("fast lane failed; will retry")
                record_fast(found, ok)
                next_fast = time.monotonic() + fast_every
        if stop["flag"]:
            write_state(running=False)
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


# ---------------------------------------------------------------- cleanup


def prune_foreign(sessions, country="US"):
    """Mark stored items whose eBay itemLocation is outside `country` as IRRELEVANT.
    Searches now filter by itemLocationCountry; this handles items found before that."""
    changed = 0
    with sessions.begin() as s:
        for item in s.scalars(select(DiscoveredItem).where(DiscoveredItem.inbox_status == "NEW")):
            loc = ((item.original_data or {}).get("itemLocation") or {}).get("country")
            if loc and loc != country:
                item.inbox_status = "IRRELEVANT"
                changed += 1
    return changed


# ---------------------------------------------------------------- main


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("command", choices=["seed", "run", "status", "prune-foreign"])
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
        elif args.command == "prune-foreign":
            print(f"marked {prune_foreign(sessions)} non-US items as passed")
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
