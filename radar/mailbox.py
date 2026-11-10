"""Poll the alerts mailbox over IMAP and feed messages into the intake importer.

    python -m radar.mailbox test      # log in, report folder size and the highest UID
    python -m radar.mailbox poll      # import everything newer than the last seen UID
    python -m radar.mailbox status    # last poll, per-source counts

Settings (.env):
    RADAR_IMAP_HOST=imap.gmail.com
    RADAR_IMAP_USER=turquoise.radar8@gmail.com
    RADAR_IMAP_PASSWORD=<16-char Gmail app password>
    RADAR_IMAP_FOLDER=INBOX
    RADAR_IMAP_MAX_PER_POLL=300

Progress is tracked by IMAP UID in Setting row 92, so messages are never imported twice
even if they are read, moved or flagged in the mailbox. Senders are routed to an intake
source by their From domain; messages from unknown senders are skipped (and counted) so a
newsletter cannot inject listings. Message bodies are handled only by the existing
intake parsers, which treat them as untrusted data.
"""

import argparse
import email
import email.utils
import imaplib
import logging
import os
import sys
from collections import Counter, defaultdict
from datetime import UTC, datetime

from sqlalchemy import select

from radar.db import IntakeSource, Setting, database
from radar.intake.parsers import MAX_BATCH_BYTES, MAX_MESSAGES
from radar.intake.registry import seed_sources
from radar.intake.service import import_alerts

log = logging.getLogger("radar.mailbox")
UID_SETTING_ID = 92


def settings():
    return dict(
        host=os.environ.get("RADAR_IMAP_HOST", "").strip(),
        user=os.environ.get("RADAR_IMAP_USER", "").strip(),
        password=os.environ.get("RADAR_IMAP_PASSWORD", "").replace(" ", "").strip(),
        folder=os.environ.get("RADAR_IMAP_FOLDER", "INBOX").strip() or "INBOX",
        max_per_poll=int(os.environ.get("RADAR_IMAP_MAX_PER_POLL", "300")),
    )


def configured(cfg):
    return bool(cfg["host"] and cfg["user"] and cfg["password"])


# ---------------------------------------------------------------- routing


def domain_map(session):
    """{sender_domain: source_id} from the intake source registry (owner-editable in /sources)."""
    out = {}
    for row in session.scalars(select(IntakeSource)):
        for d in row.data.get("domains") or []:
            d = d.lower().strip()
            if d and d != "example.invalid":
                out[d] = row.id
    return out


def sender_domain(raw):
    try:
        msg = email.message_from_bytes(raw)
    except Exception:
        return None
    _, addr = email.utils.parseaddr(msg.get("From", ""))
    if "@" not in addr:
        return None
    return addr.rsplit("@", 1)[1].lower()


def route(domain, domains):
    """Match exact domain or any parent (alerts.hibid.com -> hibid.com)."""
    if not domain:
        return None
    parts = domain.split(".")
    for i in range(len(parts) - 1):
        candidate = ".".join(parts[i:])
        if candidate in domains:
            return domains[candidate]
    return None


# ---------------------------------------------------------------- uid ledger


def last_uid(session):
    row = session.get(Setting, UID_SETTING_ID)
    return int((row.data or {}).get("last_uid", 0)) if row else 0


def save_state(session, **fields):
    row = session.get(Setting, UID_SETTING_ID)
    data = dict(row.data) if row else {}
    data.update(fields)
    if row:
        row.data = data
    else:
        session.add(Setting(id=UID_SETTING_ID, data=data))


# ---------------------------------------------------------------- imap


def connect(cfg):
    conn = imaplib.IMAP4_SSL(cfg["host"])
    conn.login(cfg["user"], cfg["password"])
    status, data = conn.select(cfg["folder"], readonly=True)
    if status != "OK":
        raise RuntimeError(f"cannot select folder {cfg['folder']!r}: {data}")
    return conn


def fetch_new(conn, since_uid, limit):
    """Return [(uid, raw_bytes)] for UIDs greater than since_uid, oldest first."""
    status, data = conn.uid("search", None, f"UID {since_uid + 1}:*")
    if status != "OK":
        raise RuntimeError(f"UID search failed: {data}")
    uids = [int(u) for u in (data[0] or b"").split() if int(u) > since_uid]
    uids = sorted(uids)[:limit]
    out = []
    for uid in uids:
        status, parts = conn.uid("fetch", str(uid), "(BODY.PEEK[])")
        if status != "OK" or not parts or not isinstance(parts[0], tuple):
            log.warning("fetch failed for uid %d", uid)
            continue
        out.append((uid, parts[0][1]))
    return out


def highest_uid(conn):
    status, data = conn.uid("search", None, "ALL")
    uids = [int(u) for u in (data[0] or b"").split()] if status == "OK" else []
    return (max(uids) if uids else 0), len(uids)


# ---------------------------------------------------------------- poll


def poll(sessions, cfg=None):
    """Import new mailbox messages. Returns stats dict (also stored in Setting 92)."""
    cfg = cfg or settings()
    if not configured(cfg):
        log.info("mailbox not configured (RADAR_IMAP_*); skipping")
        return None
    with sessions.begin() as s:
        seed_sources(s)
        domains = domain_map(s)
        since = last_uid(s)
    conn = connect(cfg)
    try:
        batch = fetch_new(conn, since, cfg["max_per_poll"])
    finally:
        try:
            conn.logout()
        except Exception:
            pass
    stats = dict(
        fetched=len(batch),
        imported=0,
        duplicates=0,
        candidates=0,
        unknown_sender=0,
        failed=0,
        by_source=Counter(),
        unknown_domains=Counter(),
    )
    if not batch:
        with sessions.begin() as s:
            save_state(s, last_poll_at=datetime.now(UTC).isoformat(), last_stats=_plain(stats))
        log.info("mailbox: nothing new (last uid %d)", since)
        return stats

    grouped = defaultdict(list)
    for _uid, raw in batch:
        dom = sender_domain(raw)
        source_id = route(dom, domains)
        if source_id is None:
            stats["unknown_sender"] += 1
            stats["unknown_domains"][dom or "?"] += 1
            continue
        grouped[source_id].append(raw)

    for source_id, raws in grouped.items():
        for chunk in _chunks(raws):
            try:
                with sessions.begin() as s:
                    result = import_alerts(s, source_id, chunk)
                    st = result.stats
                stats["imported"] += st.get("new_messages", 0)
                stats["duplicates"] += st.get("duplicate_messages", 0)
                stats["candidates"] += st.get("new_candidates", 0)
                stats["by_source"][source_id] += st.get("new_messages", 0)
            except Exception:
                # One bad message must not block the mailbox; it is skipped (UID advances)
                # and visible in the log. Re-import by hand via /sources if needed.
                stats["failed"] += len(chunk)
                log.exception("import failed for %d %s message(s)", len(chunk), source_id)

    newest = max(uid for uid, _ in batch)
    with sessions.begin() as s:
        save_state(
            s,
            last_uid=newest,
            last_poll_at=datetime.now(UTC).isoformat(),
            last_stats=_plain(stats),
        )
    log.info(
        "mailbox: fetched %d, imported %d (%d dup), %d candidates, "
        "%d unknown sender, %d failed; uid -> %d",
        stats["fetched"],
        stats["imported"],
        stats["duplicates"],
        stats["candidates"],
        stats["unknown_sender"],
        stats["failed"],
        newest,
    )
    if stats["unknown_domains"]:
        log.info("  unknown sender domains: %s", dict(stats["unknown_domains"]))
    return stats


def _chunks(raws):
    chunk, size = [], 0
    for raw in raws:
        if chunk and (len(chunk) >= MAX_MESSAGES or size + len(raw) > MAX_BATCH_BYTES):
            yield chunk
            chunk, size = [], 0
        chunk.append(raw)
        size += len(raw)
    if chunk:
        yield chunk


def _plain(stats):
    return {k: (dict(v) if isinstance(v, Counter) else v) for k, v in stats.items()}


# ---------------------------------------------------------------- cli


def main(argv=None):
    from radar.scheduler import load_env

    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("command", choices=["test", "poll", "status"])
    parser.add_argument("--env")
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S"
    )
    load_env(args.env)
    cfg = settings()
    if args.command == "test":
        if not configured(cfg):
            print("not configured: set RADAR_IMAP_HOST, RADAR_IMAP_USER, RADAR_IMAP_PASSWORD")
            return 2
        conn = connect(cfg)
        try:
            top, count = highest_uid(conn)
        finally:
            conn.logout()
        print(
            f"logged in as {cfg['user']}; {cfg['folder']} has {count} messages, highest uid {top}"
        )
        return 0
    engine, sessions = database()
    try:
        if args.command == "poll":
            stats = poll(sessions, cfg)
            if stats is not None:
                print(_plain(stats))
        else:
            with sessions() as s:
                row = s.get(Setting, UID_SETTING_ID)
                print(row.data if row else "never polled")
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(main())
