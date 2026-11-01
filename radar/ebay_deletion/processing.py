"""Explicit offline maintenance of inventoried copies; never run on app startup."""

import hashlib
import json
import sqlite3
from pathlib import Path

from radar.ebay_deletion.protocol import digest
from radar.ebay_deletion.store import Queue

SOURCE = {"ebay", "ebay production"}
REMOVED = "eBay account data removed"
# Owner financial evidence and decisions survive; seller-derived prose/URLs do not.
SAFE_FIELDS = {
    "item_price",
    "currency",
    "shipping_cost",
    "buyer_premium",
    "tax",
    "handling_fee",
    "service_fee",
    "resale_low",
    "resale_high",
    "resale_confirmed",
    "synthetic",
    "discovery_mode",
    "cost_status",
}


def is_ebay(data):
    return isinstance(data, dict) and str(data.get("source", "")).casefold() in SOURCE


def dictionaries(value):
    if isinstance(value, dict):
        yield value
        for v in value.values():
            yield from dictionaries(v)
    elif isinstance(value, list):
        for v in value:
            yield from dictionaries(v)


def identity_state(values, identifiers, secret, aliases):
    stable, usernames = set(), set()
    for value in values:
        for part in dictionaries(value):
            seller = part.get("seller")
            if isinstance(seller, dict):
                for field in ("userId", "eiasToken"):
                    if isinstance(seller.get(field), str):
                        stable.add((field, digest(secret, field, seller[field])))
                if isinstance(seller.get("username"), str):
                    usernames.add(digest(secret, "username", seller["username"]))
            if isinstance(part.get("seller_id"), str):
                usernames.add(digest(secret, "username", part["seller_id"]))
    matches = [(kind, value) for kind, value in stable if identifiers.get(kind) == value]
    conflicts = [
        (kind, value)
        for kind, value in stable
        if kind in identifiers and identifiers[kind] != value
    ]
    alias_matches = [
        name
        for name in usernames
        if name in aliases and identifiers.get("userId") and aliases[name] == identifiers["userId"]
    ]
    if (matches or alias_matches) and not conflicts:
        return "MATCH"
    if matches or alias_matches or identifiers.get("username") in usernames:
        return "AMBIGUOUS"
    # A username-only record cannot be excluded by a different immutable event ID.
    # Only an explicit alias or comparable stable field can establish non-match.
    if usernames and not stable and any(name not in aliases for name in usernames):
        return "UNMAPPED"
    return "NONE"


def tombstone(data):
    return {
        **{k: v for k, v in data.items() if k in SAFE_FIELDS},
        "source": "eBay",
        "raw_title": REMOVED,
        "raw_description": "",
        "resale_basis": "Owner amounts retained; source evidence removed",
        "shipping_status": "UNKNOWN",
        "material_evidence": "unknown",
    }


def sanitize_database(path, identifiers, secret, aliases=None, photo_reviews=None):
    """Stop app first. Uses only existing DBs; no schema migration or demo seeding."""
    aliases = aliases or {}
    photo_reviews = photo_reviews or {}
    path = Path(path)
    if not path.is_file() or path.is_symlink():
        raise ValueError("Database unavailable")
    db = sqlite3.connect(f"file:{path.resolve()}?mode=rw", uri=True, timeout=1)
    db.row_factory = sqlite3.Row
    try:
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA secure_delete=ON")
        if db.execute("PRAGMA journal_mode=DELETE").fetchone()[0].lower() != "delete":
            raise ValueError("Stop all database users before deletion")
        db.execute("BEGIN IMMEDIATE")

        def loads(value):
            return json.loads(value) if isinstance(value, str) else value

        listings = list(db.execute("SELECT * FROM listings"))
        discovered = list(db.execute("SELECT * FROM discovered_items"))
        matched_listings, matched_items = set(), set()
        ambiguous = False
        for row in listings:
            data = loads(row["data"])
            if not is_ebay(data):
                continue
            values = [data, loads(row["original_data"])]
            values += [
                loads(e[k])
                for e in db.execute(
                    "SELECT before_data,after_data FROM listing_edits WHERE listing_id=?",
                    (row["id"],),
                )
                for k in ("before_data", "after_data")
            ]
            # Snapshot-only stable IDs also identify the linked listing.
            for case in db.execute("SELECT id FROM maker_cases WHERE listing_id=?", (row["id"],)):
                values += [
                    loads(x[0])
                    for x in db.execute(
                        "SELECT data FROM maker_scenarios WHERE case_id=?", (case[0],)
                    )
                ]
            state = identity_state(values, identifiers, secret, aliases)
            if state == "MATCH":
                matched_listings.add(row["id"])
            ambiguous |= state in ("AMBIGUOUS", "UNMAPPED")
        for row in discovered:
            if row["source"].casefold() not in SOURCE:
                continue
            values = [loads(row["normalized"]), loads(row["original_data"])]
            values += [
                loads(x[0])
                for x in db.execute(
                    "SELECT raw_data FROM discovery_observations WHERE item_id=?", (row["id"],)
                )
            ]
            state = identity_state(values, identifiers, secret, aliases)
            if state == "MATCH":
                matched_items.add(row["id"])
                if row["listing_id"] is not None:
                    linked = next((x for x in listings if x["id"] == row["listing_id"]), None)
                    if linked and is_ebay(loads(linked["data"])):
                        matched_listings.add(row["listing_id"])
                    else:
                        ambiguous = True  # Never erase a different source through a foreign key.
            ambiguous |= state in ("AMBIGUOUS", "UNMAPPED")
        matched_items |= {
            r["id"]
            for r in discovered
            if r["listing_id"] in matched_listings and r["source"].casefold() in SOURCE
        }
        snapshot_updates = []
        snapshot_runs = set()
        snapshot_scenarios = set()
        snapshot_cases = set()
        for edit in db.execute("SELECT id,listing_id,before_data,after_data FROM listing_edits"):
            if edit["listing_id"] in matched_listings:
                continue
            for field in ("before_data", "after_data"):
                value = loads(edit[field])
                if is_ebay(value):
                    state = identity_state([value], identifiers, secret, aliases)
                    ambiguous |= state in ("AMBIGUOUS", "UNMAPPED")
                    if state == "MATCH":
                        snapshot_updates.append((field, json.dumps(tombstone(value)), edit["id"]))
        matched_cases = {
            r[0]
            for listing in matched_listings
            for r in db.execute("SELECT id FROM maker_cases WHERE listing_id=?", (listing,))
        }
        for table, selected in (
            ("maker_runs", snapshot_runs),
            ("maker_scenarios", snapshot_scenarios),
        ):
            for row in db.execute("SELECT id,case_id,data FROM " + table):
                if row["case_id"] in matched_cases:
                    continue
                for value in dictionaries(loads(row["data"])):
                    if is_ebay(value):
                        state = identity_state([value], identifiers, secret, aliases)
                        ambiguous |= state in ("AMBIGUOUS", "UNMAPPED")
                        if state == "MATCH":
                            selected.add(row["id"])
                            snapshot_cases.add(row["case_id"])
        matching_comparables = set()
        for row in db.execute("SELECT id,data FROM comparable_sales"):
            value = loads(row["data"])
            if is_ebay(value):
                state = identity_state([value], identifiers, secret, aliases)
                ambiguous |= state in ("AMBIGUOUS", "UNMAPPED")
                if state == "MATCH":
                    matching_comparables.add(row["id"])
        for item in matched_items:
            if db.execute("SELECT 1 FROM alert_observations WHERE item_id=?", (item,)).fetchone():
                db.rollback()
                return "REVIEW_ALERT_COPIES", 0
        # Every linked photograph needs a provenance decision; preserve independently owned
        # photographs. Pending paid inference must finish before removing its context.
        photos, vision_cases = [], set()
        for listing in matched_listings:
            photos += [
                ("maker_photos", p)
                for p in db.execute(
                    "SELECT p.* FROM maker_photos p JOIN maker_cases c ON p.case_id=c.id "
                    "WHERE c.listing_id=?",
                    (listing,),
                )
            ]
            vision_cases |= {
                r[0]
                for r in db.execute("SELECT id FROM vision_cases WHERE listing_id=?", (listing,))
            }
        for case in snapshot_cases - matched_cases:
            photos += [
                ("maker_photos", p)
                for p in db.execute("SELECT * FROM maker_photos WHERE case_id=?", (case,))
            ]
        for item in matched_items:
            vision_cases |= {
                r[0]
                for r in db.execute("SELECT id FROM vision_cases WHERE candidate_id=?", (item,))
            }
        for case in vision_cases:
            photos += [
                ("vision_photos", p)
                for p in db.execute("SELECT * FROM vision_photos WHERE case_id=?", (case,))
            ]
            if db.execute(
                "SELECT 1 FROM vision_runs WHERE case_id=? AND status IN ('RESERVED','SENT')",
                (case,),
            ).fetchone():
                db.rollback()
                return "REVIEW_INFLIGHT_VISION", 0
        blocked = False
        for table, photo in photos:
            review = photo_reviews.get(table + ":" + photo["id"], {})
            if review.get("kind") not in ("independent", "seller_copy") or not review.get(
                "evidence"
            ):
                blocked = True
            if review.get("kind") == "seller_copy":
                file = Path(review.get("path", ""))
                if file.name != photo["id"] + ".png" or file.is_symlink():
                    blocked = True
                elif (
                    file.exists()
                    and hashlib.sha256(file.read_bytes()).hexdigest() != photo["content_hash"]
                ):
                    blocked = True
        if ambiguous or blocked:
            db.rollback()
            return "REVIEW_IDENTITY" if ambiguous else "REVIEW_ARTIFACTS", 0
        # File deletion precedes the SQL commit: interruption leaves the account match available
        # for replay. Missing already-deleted files are harmless on recovery.
        for table, photo in photos:
            review = photo_reviews[table + ":" + photo["id"]]
            if review["kind"] == "seller_copy":
                Path(review["path"]).unlink(missing_ok=True)
                db.execute("DELETE FROM " + table + " WHERE id=?", (photo["id"],))
        for case in vision_cases:
            for run in list(db.execute("SELECT id FROM vision_runs WHERE case_id=?", (case,))):
                redacted = dict(
                    photo_ids=[],
                    error_code="SOURCE_REMOVED",
                    usage=None,
                    context={},
                    image_hashes=[],
                    prompt_version="removed",
                    schema_version="removed",
                )
                db.execute(
                    "UPDATE vision_runs SET data=?,status='REMOVED',active_key=NULL,"
                    "cache_key=? WHERE id=?",
                    (json.dumps(redacted), "removed-ebay-vision-" + str(run[0]), run[0]),
                )
                for table in ("vision_findings", "vision_sub_items"):
                    db.execute("DELETE FROM " + table + " WHERE run_id=?", (run[0],))
                for correction in list(
                    db.execute("SELECT id,data FROM vision_corrections WHERE run_id=?", (run[0],))
                ):
                    original = loads(correction["data"])
                    db.execute(
                        "UPDATE vision_corrections SET data=? WHERE id=?",
                        (
                            json.dumps(
                                {
                                    "action": original.get("action", "UNCERTAIN"),
                                    "note": "Source evidence removed",
                                }
                            ),
                            correction["id"],
                        ),
                    )
                db.execute(
                    "UPDATE vision_billing SET evidence='Source evidence removed' WHERE run_id=?",
                    (run[0],),
                )
        for field, value, row_id in snapshot_updates:
            db.execute("UPDATE listing_edits SET " + field + "=? WHERE id=?", (value, row_id))
        for run_id in snapshot_runs:
            db.execute(
                "UPDATE maker_decisions SET run_id=NULL,data='{}',candidate_id=NULL WHERE run_id=?",
                (run_id,),
            )
            db.execute("DELETE FROM maker_runs WHERE id=?", (run_id,))
        for scenario_id in snapshot_scenarios:
            db.execute("DELETE FROM maker_scenarios WHERE id=?", (scenario_id,))
        for listing in matched_listings:
            row = next(x for x in listings if x["id"] == listing)
            data = json.dumps(tombstone(loads(row["data"])))
            db.execute(
                "UPDATE listings SET data=?,original_data='{}',dedup_key=? WHERE id=?",
                (data, "deleted-ebay-listing-" + str(listing), listing),
            )
            for edit in list(
                db.execute("SELECT * FROM listing_edits WHERE listing_id=?", (listing,))
            ):
                db.execute(
                    "UPDATE listing_edits SET before_data=?,after_data=? WHERE id=?",
                    (
                        json.dumps(tombstone(loads(edit["before_data"]))),
                        json.dumps(tombstone(loads(edit["after_data"]))),
                        edit["id"],
                    ),
                )
            db.execute("UPDATE feedback SET note='' WHERE listing_id=?", (listing,))
            for case in list(
                db.execute("SELECT id FROM maker_cases WHERE listing_id=?", (listing,))
            ):
                db.execute(
                    "UPDATE maker_decisions SET data='{}',run_id=NULL,candidate_id=NULL "
                    "WHERE case_id=?",
                    (case[0],),
                )
                db.execute("DELETE FROM maker_scenarios WHERE case_id=?", (case[0],))
                db.execute("DELETE FROM maker_runs WHERE case_id=?", (case[0],))
                db.execute(
                    "UPDATE maker_cases SET data=? WHERE id=?",
                    (json.dumps({"title": REMOVED, "details": "", "listing_url": None}), case[0]),
                )
        for case in snapshot_cases - matched_cases:
            db.execute(
                "UPDATE maker_decisions SET data='{}',run_id=NULL,candidate_id=NULL "
                "WHERE case_id=?",
                (case,),
            )
            db.execute("DELETE FROM maker_scenarios WHERE case_id=?", (case,))
            db.execute("DELETE FROM maker_runs WHERE case_id=?", (case,))
            db.execute(
                "UPDATE maker_cases SET data=? WHERE id=?",
                (json.dumps({"title": REMOVED, "details": "", "listing_url": None}), case),
            )
        for item in matched_items:
            db.execute(
                "UPDATE discovered_items SET normalized=?,original_data='{}',ebay_item_id=? "
                "WHERE id=?",
                (json.dumps(tombstone({})), "removed-" + str(item), item),
            )
            db.execute("UPDATE discovery_observations SET raw_data='{}' WHERE item_id=?", (item,))
        for comparable in matching_comparables:
            db.execute("DELETE FROM comparable_sales WHERE id=?", (comparable,))
        db.commit()
        db.execute("VACUUM")
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("Integrity check failed")
        if db.execute("PRAGMA foreign_key_check").fetchall():
            raise ValueError("Foreign key check failed")
        return (
            "SANITIZED"
            if matched_listings
            or matched_items
            or snapshot_updates
            or snapshot_runs
            or snapshot_scenarios
            or matching_comparables
            else "NO_MATCH"
        ), (len(matched_listings) + len(matched_items))
    finally:
        db.close()


def replay(manifest_path, queue_path, secret):
    """Manifest inventories ALL DB copies and files. No real changes without explicit CLI apply."""
    manifest = json.loads(Path(manifest_path).read_text())
    if (
        manifest.get("version") != 1
        or manifest.get("inventory_complete") is not True
        or not isinstance(manifest.get("assets"), list)
        or not manifest["assets"]
    ):
        raise ValueError("Complete nonempty inventory attestation required")
    queue = Queue(queue_path)
    queue.bind(secret)
    statuses = []
    for event_id, identifiers in queue.events():
        for asset in manifest["assets"]:
            path = Path(asset["path"]).resolve()
            asset_id = hashlib.sha256(str(path).encode()).hexdigest()
            count = 0
            if asset["kind"] == "sqlite":
                status, count = sanitize_database(
                    path,
                    identifiers,
                    secret,
                    manifest.get("aliases"),
                    manifest.get("photo_reviews"),
                )
            elif asset["kind"] == "independent":
                if not path.exists() or not asset.get("review_evidence"):
                    raise ValueError("Independent asset review evidence required")
                status = "INDEPENDENT"
            else:
                # Opaque/mixed archives and reports are NOT exempt. Rebuild/sanitize them before
                # recording independent evidence; never delete unrelated owner data wholesale.
                status = "REVIEW_EXPORT_OR_ARCHIVE"
            queue.receipt(event_id, asset_id, status, count)
            statuses.append(status)
    return statuses
