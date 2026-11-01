"""Explicit offline maintenance of inventoried copies; never run on app startup."""

import hashlib
import json
import sqlite3
from pathlib import Path

from radar.ebay_deletion.protocol import digest
from radar.ebay_deletion.sync import maintenance_ledger

SOURCE = {"ebay", "ebay production"}
REMOVED = "eBay account data removed"
# ListingInput financial values are owner records; prose can contain seller data.
FINANCIAL_AMOUNT_FIELDS = {
    "item_price",
    "shipping_cost",
    "buyer_premium",
    "buyer_service_fee",
    "sales_tax",
    "handling_fee",
    "invoice_fee_allocation",
    "resale_low",
    "resale_high",
    "resale_buyer_shipping",
    "resale_buyer_tax",
}
SAFE_FIELDS = FINANCIAL_AMOUNT_FIELDS | {
    "currency",
    "buyer_profile",
    "selling_category",
    "commission_tier",
    "resale_shipping_status",
    "resale_tax_status",
    "resale_confirmed",
    "capture_confirmed",
    "invoice_allocation_confirmed",
    "synthetic",
    "discovery_mode",
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
    # Reviewed aliases are identity evidence, including when they contradict a direct ID.
    stable |= {("userId", aliases[name]) for name in usernames if name in aliases}
    matches = {(kind, value) for kind, value in stable if identifiers.get(kind) == value}
    conflicts = {
        (kind, value)
        for kind, value in stable
        if kind in identifiers and identifiers[kind] != value
    }
    comparable = {kind for kind, _ in stable if kind in identifiers}
    if matches and conflicts:
        return "AMBIGUOUS"
    if matches:
        return "UNMAPPED" if any(kind not in identifiers for kind, _ in stable) else "MATCH"
    if identifiers.get("username") in usernames:
        return "AMBIGUOUS"
    if stable and any(kind not in comparable for kind, _ in stable):
        return "UNMAPPED"
    if usernames and not comparable and any(name not in aliases for name in usernames):
        return "UNMAPPED"
    return "NONE"


def tombstone(data):
    retained = {
        **{k: v for k, v in data.items() if k in SAFE_FIELDS},
        "source": "eBay",
        "raw_title": REMOVED,
        "raw_description": "",
        "resale_basis": (
            "Retained owner resale amounts and confirmation; original source prose removed"
            if data.get("resale_low") is not None
            else None
        ),
        "invoice_allocation_evidence": (
            "Retained owner-confirmed invoice allocation; original explanatory prose removed"
            if data.get("invoice_allocation_confirmed")
            else None
        ),
        "shipping_status": "UNKNOWN",
        "material_evidence": "unknown",
    }
    # Do not retain arbitrary keys, or claim confirmation of absent monetary values.
    retained["cost_status"] = {
        key: status if data.get(key) is not None else "UNKNOWN"
        for key, status in data.get("cost_status", {}).items()
        if key in FINANCIAL_AMOUNT_FIELDS or key == "buyer_profile"
    }
    for amount, status in (
        ("resale_buyer_shipping", "resale_shipping_status"),
        ("resale_buyer_tax", "resale_tax_status"),
    ):
        if data.get(amount) is None:
            retained[status] = "UNKNOWN"
    if data.get("invoice_fee_allocation") is None:
        retained["invoice_allocation_confirmed"] = False
        retained["invoice_allocation_evidence"] = None
    if data.get("resale_low") is None or data.get("resale_high") is None:
        retained["resale_confirmed"] = False
    return retained


class ReviewHold(ValueError):
    pass


def raw_ebay(value):
    return is_ebay(value) or (
        isinstance(value, dict)
        and value.get("provider") == "eBay Browse"
        and value.get("mode", "production") == "production"
    )


def redact_snapshots(value, identifiers, secret, aliases, context=None):
    """Change only source-identified nodes. An unseparated dependent copy needs review."""
    if isinstance(value, list):
        result = [redact_snapshots(v, identifiers, secret, aliases, context) for v in value]
        return [r[0] for r in result], any(r[1] for r in result), any(r[2] for r in result)
    if not isinstance(value, dict):
        return value, False, False
    if raw_ebay(value):
        state = identity_state([value], identifiers, secret, aliases)
        if state in ("UNMAPPED", "AMBIGUOUS") and context:
            # A snapshot's exact seller value must agree with the linked listing context.
            current, raw, parent_state = context
            if (
                parent_state == "MATCH"
                and value.get("seller_id")
                and value.get("seller_id") == current.get("seller_id")
            ):
                state = identity_state([value, raw], identifiers, secret, aliases)
        if state in ("UNMAPPED", "AMBIGUOUS"):
            raise ReviewHold("REVIEW_IDENTITY")
        if state == "MATCH":
            if any(
                p is not value and p.get("source") and not is_ebay(p) for p in dictionaries(value)
            ):
                raise ReviewHold("REVIEW_PROVENANCE")
            replacement = tombstone(value) if is_ebay(value) else {}
            if "title" in value:
                replacement.update(title=REMOVED, details="", listing_url=None)
            return replacement, True, True
        # A typed but identifier-free source record cannot be attributed by association.
        if (
            context
            and context[2] == "MATCH"
            and not any(p.get("seller") or p.get("seller_id") for p in dictionaries(value))
            and value.get("raw_title") != REMOVED
            and value.get("title") != REMOVED
        ):
            raise ReviewHold("REVIEW_PROVENANCE")
        return value, False, True
    result = {
        k: redact_snapshots(v, identifiers, secret, aliases, context) for k, v in value.items()
    }
    changed = any(r[1] for r in result.values())
    # Explicit independent provenance protects that record's own prose, but nested copies
    # are still traversed. Untyped prose beside a removed snapshot is not presumed independent.
    if (
        changed
        and not value.get("source")
        and any(isinstance(v, str) and v for k, v in value.items())
    ):
        raise ReviewHold("REVIEW_PROVENANCE")
    return (
        {k: r[0] for k, r in result.items()},
        changed,
        bool(value.get("source")) or any(r[2] for r in result.values()),
    )


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
        edits = list(db.execute("SELECT * FROM listing_edits"))
        matched_listings, matched_items = set(), set()
        contexts, original_updates, snapshot_updates, research_updates = {}, set(), [], []
        snapshot_cases, matching_comparables = set(), set()
        ambiguous = False
        for row in listings:
            current, raw = loads(row["data"]), loads(row["original_data"])
            history = [
                loads(e[k])
                for e in edits
                if e["listing_id"] == row["id"]
                for k in ("before_data", "after_data")
                if is_ebay(loads(e[k]))
            ]
            known_raw = raw_ebay(raw) or (is_ebay(current) and not raw.get("source"))
            evidence = ([current] if is_ebay(current) else []) + history
            if known_raw:
                evidence.append(raw)
            aggregate = identity_state(evidence, identifiers, secret, aliases)
            if aggregate in ("AMBIGUOUS", "UNMAPPED"):
                raise ReviewHold("REVIEW_IDENTITY")
            # Original payload has its own provenance, even after a current source-label edit.
            original_state = identity_state([raw], identifiers, secret, aliases)
            if known_raw and original_state == "MATCH":
                original_updates.add(row["id"])
            elif history and not known_raw and not raw.get("source") and raw:
                raise ReviewHold("REVIEW_PROVENANCE")
            state = (
                identity_state(
                    [current, raw] if known_raw else [current], identifiers, secret, aliases
                )
                if is_ebay(current)
                else "NONE"
            )
            contexts[row["id"]] = (current, raw if known_raw else {}, state)
            if state == "MATCH" and is_ebay(current):
                matched_listings.add(row["id"])
            elif state in ("AMBIGUOUS", "UNMAPPED"):
                raise ReviewHold("REVIEW_IDENTITY")
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
            if state in ("AMBIGUOUS", "UNMAPPED"):
                raise ReviewHold("REVIEW_IDENTITY")
            if state == "MATCH":
                matched_items.add(row["id"])
                if row["listing_id"] is not None and row["listing_id"] not in matched_listings:
                    raise ReviewHold("REVIEW_PROVENANCE")
        for edit in edits:
            for field in ("before_data", "after_data"):
                value, changed, _ = redact_snapshots(
                    loads(edit[field]),
                    identifiers,
                    secret,
                    aliases,
                    contexts.get(edit["listing_id"]),
                )
                if changed:
                    snapshot_updates.append((field, json.dumps(value), edit["id"]))
        cases = {r["id"]: r for r in db.execute("SELECT * FROM maker_cases")}
        # First identify affected records; no case-wide deletion follows a snapshot match.
        for table in ("maker_runs", "maker_scenarios", "maker_decisions", "maker_cases"):
            for row in db.execute("SELECT * FROM " + table):
                case_id = row["id"] if table == "maker_cases" else row["case_id"]
                context = contexts.get(cases[case_id]["listing_id"])
                value, changed, scoped = redact_snapshots(
                    loads(row["data"]), identifiers, secret, aliases, context
                )
                if changed:
                    research_updates.append((table, row["id"], json.dumps(value)))
                    snapshot_cases.add(case_id)
                elif context and context[2] == "MATCH" and row["data"] != "{}" and not scoped:
                    raise ReviewHold("REVIEW_PROVENANCE")
        # An untyped sibling's relationship to the affected history cannot be guessed.
        for case_id in snapshot_cases:
            for table in ("maker_runs", "maker_scenarios", "maker_decisions", "maker_cases"):
                query = (
                    "SELECT * FROM "
                    + table
                    + (" WHERE id=?" if table == "maker_cases" else " WHERE case_id=?")
                )
                for row in db.execute(query, (case_id,)):
                    _, _, scoped = redact_snapshots(
                        loads(row["data"]),
                        identifiers,
                        secret,
                        aliases,
                        contexts.get(cases[case_id]["listing_id"]),
                    )
                    if not scoped and loads(row["data"]):
                        raise ReviewHold("REVIEW_PROVENANCE")
        matched_cases = {
            case for case, row in cases.items() if row["listing_id"] in matched_listings
        }
        for row in db.execute("SELECT id,data FROM comparable_sales"):
            value = loads(row["data"])
            if is_ebay(value):
                state = identity_state([value], identifiers, secret, aliases)
                if state in ("AMBIGUOUS", "UNMAPPED"):
                    raise ReviewHold("REVIEW_IDENTITY")
                if state == "MATCH":
                    matching_comparables.add(row["id"])
        for item in matched_items:
            if db.execute("SELECT 1 FROM alert_observations WHERE item_id=?", (item,)).fetchone():
                raise ReviewHold("REVIEW_ALERT_COPIES")
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
        for table, row_id, value in research_updates:
            db.execute("UPDATE " + table + " SET data=? WHERE id=?", (value, row_id))
        for listing in original_updates:
            db.execute("UPDATE listings SET original_data='{}' WHERE id=?", (listing,))
        for listing in matched_listings:
            row = next(x for x in listings if x["id"] == listing)
            data = json.dumps(tombstone(loads(row["data"])))
            db.execute(
                "UPDATE listings SET data=?,dedup_key=? WHERE id=?",
                (data, "deleted-ebay-listing-" + str(listing), listing),
            )
            db.execute("UPDATE feedback SET note='' WHERE listing_id=?", (listing,))
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
            or research_updates
            or original_updates
            or matching_comparables
            else "NO_MATCH"
        ), (len(matched_listings) + len(matched_items))
    except ReviewHold as hold:
        db.rollback()
        return str(hold), 0
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
    queue = maintenance_ledger(queue_path, secret)
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
