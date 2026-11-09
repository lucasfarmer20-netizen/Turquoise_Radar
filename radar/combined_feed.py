"""Read-only joining of existing pilot and permitted local email-review leads."""

import base64
import json
import os
import sqlite3
import stat
from pathlib import Path
from urllib.parse import quote

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from radar.compact_feed import decorate_compact_cards
from radar.db import AlertMessage, IntakeSource
from radar.intake.parsers import parse_message
from radar.intake.registry import SourceDefinition
from radar.sourcing_feed import build_feed


def snapshot_details(session):
    """Recover omitted fields from retained messages in memory; never reimport."""
    details = {}
    definitions = {
        row.id: SourceDefinition.model_validate(row.data)
        for row in session.scalars(select(IntakeSource))
        if row.id in ("shopgoodwill", "hibid")
    }
    for message in session.scalars(
        select(AlertMessage).order_by(AlertMessage.sent_at, AlertMessage.id)
    ):
        if message.source_id not in definitions:
            continue
        parsed = parse_message(
            base64.b64decode(message.raw_b64, validate=True),
            message.source_id,
            definitions[message.source_id],
        )
        for candidate in parsed.candidates:
            evidence = candidate.evidence
            details[(candidate.item.source, candidate.item.source_listing_id)] = {
                "bids": evidence.get("bid_count_quote"),
                "started": evidence.get("listing_started_raw"),
                "closing_hint": evidence.get("auction_time_raw"),
                "price_quote": evidence.get("price_quote"),
            }
    return details


def email_cards(path, prefs):
    """No migrations, copies, remote resources or writes to the sibling database."""
    path = Path(path)
    if (
        not path.is_absolute()
        or path.name != "review.db"
        or path.parent.name != "email-review-nov08"
        or any(p.is_symlink() for p in (path, *path.parents))
    ):
        raise ValueError("Separate email-review database required")
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1:
        raise ValueError("Owner-controlled regular database required")
    if path.parent.stat().st_mode & 0o077:
        raise ValueError("Private email-review directory required")
    marker_path = path.parent / "ready.json"
    if marker_path.is_symlink() or not marker_path.is_file():
        raise ValueError("Email review not ready")
    marker = json.loads(marker_path.read_text())
    if marker.get("version") != 1 or set(marker.get("samples", {})) != {"shopgoodwill", "hibid"}:
        raise ValueError("Unknown email review")

    def connect():
        db = sqlite3.connect(f"file:{quote(str(path))}?mode=ro", uri=True)
        db.execute("PRAGMA query_only=ON")
        return db

    engine = create_engine("sqlite://", creator=connect)
    try:
        with engine.connect() as connection:
            for table in ("discovery_runs", "search_executions", "discovery_observations"):
                if connection.exec_driver_sql(f"SELECT 1 FROM {table} LIMIT 1").first():
                    raise ValueError("Discovery data outside email scope")
            if connection.exec_driver_sql(
                "SELECT 1 FROM discovered_items "
                "WHERE source NOT IN ('ShopGoodwill','HiBid') LIMIT 1"
            ).first():
                raise ValueError("Unexpected source")
            if connection.exec_driver_sql(
                "SELECT 1 FROM alert_messages WHERE source_id NOT IN ('shopgoodwill','hibid') "
                "OR synthetic != 0 LIMIT 1"
            ).first():
                raise ValueError("Unexpected message")
        with Session(engine) as session:
            details = snapshot_details(session)
            cards = decorate_compact_cards(
                session, build_feed(session, prefs), photo_root=path.parent / "photos"
            )
            result = []
            for card in cards:
                if card["dataset"] != "real":
                    continue
                if card["item"].source not in ("ShopGoodwill", "HiBid"):
                    raise ValueError("Unexpected retained listing")
                # Email photos are not served through the pilot's photo-ID namespace.
                card["compact"]["photo_url"] = None
                card["origin"] = "Email snapshot"
                card["email_snapshot"] = details.get(
                    (card["item"].source, card["item"].source_listing_id), {}
                )
                result.append(card)
            return result
    finally:
        engine.dispose()


def combined_cards(session, prefs, email_path=None):
    local = decorate_compact_cards(session, build_feed(session, prefs))
    withheld = sum(c["dataset"] == "real" and not c["compact"]["listing_fresh"] for c in local)
    cards = [c for c in local if c["dataset"] == "real" and c["compact"]["listing_fresh"]]
    for card in cards:
        card["origin"] = "Retained pilot observation"
    notice = "Email review is not connected."
    if email_path:
        try:
            cards.extend(email_cards(email_path, prefs))
            notice = "Email snapshots read locally; no new search or price refresh."
        except Exception:
            # Never expose database contents/paths or bypass a failed scope check.
            notice = (
                "Email review unavailable or outside its allowed scope; "
                "pilot results remain separate."
            )
    return cards, withheld, notice
