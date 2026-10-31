"""Bounded, hash-pinned offline book packets. No networking or image inference."""

import csv
import hashlib
import io
import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

from PIL import Image

from radar.references.persistence import safe_name

SCHEMA = "jri-reviewed-book-1"
MAX_FILE = 32 * 1024 * 1024
MAX_TOTAL = 512 * 1024 * 1024
DOCUMENTS = {"provisional_csv", "validation_json", "validation_csv", "relationships", "collisions"}
PROHIBITED = {"public_repo", "training", "third_party_inference", "historical_index"}


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def validate_review(data):
    require(data.get("schema") == SCHEMA, "Unsupported book review schema")
    require(data.get("source_kind") == "MANUAL_BOOK", "Offline book source required")
    require(
        re.fullmatch(r"book:[a-z0-9][a-z0-9-]{1,120}", data.get("source", "")),
        "Stable book source ID required",
    )
    for field in ("title", "author", "edition", "bibliographic_status"):
        require(
            isinstance(data.get("book", {}).get(field), str) and data["book"][field].strip(),
            "Complete book citation required",
        )
    require(
        data.get("image_policy") == "PRIVATE_REFERENCE_ONLY", "Private book image policy required"
    )
    require(type(data.get("facts_allowed")) is bool, "Explicit factual-use decision required")
    require(
        data.get("rights", {}).get("local_reference_use") is True
        and all(data["rights"].get(k) is False for k in PROHIBITED),
        "Private reference use only; no training, publication, inference or historical index",
    )
    for k in ("reviewer", "authorization", "independence_group", "rationale"):
        require(
            isinstance(data.get(k), str) and data[k].strip(), "Documented owner review required"
        )
    date = datetime.fromisoformat(data["reviewed_at"])
    require(
        date.tzinfo is not None and date <= datetime.now(UTC) + timedelta(minutes=5),
        "Timezone-aware nonfuture review required",
    )
    ids, pages = data["entry_ids"], data["pages"]
    require(
        isinstance(ids, list)
        and 1 <= len(ids) <= 1000
        and len(set(ids)) == len(ids)
        and all(isinstance(x, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,100}", x) for x in ids),
        "Explicit unique bounded entry IDs required",
    )
    require(
        isinstance(pages, list)
        and 1 <= len(pages) <= 100
        and len(set(pages)) == len(pages)
        and all(type(x) is int and 1 <= x <= 10000 for x in pages),
        "Explicit printed page scope required",
    )
    files = data["files"]
    require(isinstance(files, list) and 1 <= len(files) <= 1000, "Bounded evidence files required")
    names = set()
    for f in files:
        name = safe_name(f["name"])
        require(
            name not in names and name != "book-review.json", "Duplicate/reserved evidence path"
        )
        names.add(name)
        require(
            re.fullmatch(r"[a-f0-9]{64}", f["sha256"])
            and type(f["bytes"]) is int
            and 0 < f["bytes"] <= MAX_FILE,
            "Invalid evidence hash or size",
        )
        require(
            f["role"] in {"PHOTO", "DOCUMENT", "TRANSFER_PART", "PACKAGING"},
            "Unknown evidence role",
        )
        if f["role"] == "PHOTO":
            require(
                f.get("page") in pages and type(f.get("printed_number_visible")) is bool,
                "Photo must cite a scoped printed page and numeral visibility",
            )
    require(sum(f["bytes"] for f in files) <= MAX_TOTAL, "Evidence packet too large")
    require(
        {f["page"] for f in files if f["role"] == "PHOTO"} == set(pages),
        "Every scoped page requires a photograph",
    )
    require(
        DOCUMENTS == set(data["documents"]) and set(data["documents"].values()) <= names,
        "Missing reviewed documents",
    )
    require(
        len(set(data["documents"].values())) == len(DOCUMENTS), "Distinct review documents required"
    )
    return data


def checked_path(root, name):
    """Reject symlink parents as well as traversal; never follow an attachment path out."""
    safe_name(name)
    path = root
    for part in Path(name).parts:
        path = path / part
        require(not path.is_symlink(), "Symlink in book evidence")
    require(
        path.is_file() and path.resolve().is_relative_to(root.resolve()), "Missing book evidence"
    )
    return path


def file_bytes(root, f):
    path = checked_path(root, f["name"])
    require(path.stat().st_size == f["bytes"], "Book evidence size mismatch")
    raw = path.read_bytes()
    require(sha(raw) == f["sha256"], "Book evidence digest mismatch")
    return raw


def load_bundle(manifest, expected_sha256):
    manifest = Path(manifest)
    require(
        not manifest.is_symlink() and manifest.stat().st_size <= MAX_FILE, "Invalid book manifest"
    )
    raw = manifest.read_bytes()
    require(sha(raw) == expected_sha256, "Book manifest digest mismatch")
    review = validate_review(json.loads(raw))
    require(review["facts_allowed"], "Book factual use is not authorized")
    root = manifest.parent
    verified_documents = {}
    for f in review["files"]:
        payload = file_bytes(root, f)
        if f["name"] in review["documents"].values():
            verified_documents[f["name"]] = payload
        if f["role"] == "PHOTO":
            with Image.open(io.BytesIO(payload)) as im:
                im.verify()
    if transport := review.get("archive_reconstruction"):
        files = {f["name"]: f for f in review["files"]}
        parts = transport["parts"]
        require(
            parts
            and len(set(parts)) == len(parts)
            and all(n in files and files[n]["role"] == "TRANSFER_PART" for n in parts),
            "Invalid ordered reconstruction parts",
        )
        h, size = hashlib.sha256(), 0
        for name in parts:
            payload = file_bytes(root, files[name])
            h.update(payload)
            size += len(payload)
        require(
            h.hexdigest() == transport["sha256"] and size == transport["bytes"],
            "Reconstructed archive digest/size mismatch",
        )
    docs = {k: verified_documents[n] for k, n in review["documents"].items()}
    original = list(csv.DictReader(io.StringIO(docs["provisional_csv"].decode("utf-8-sig"))))
    corrected = list(csv.DictReader(io.StringIO(docs["validation_csv"].decode("utf-8-sig"))))
    entries = json.loads(docs["validation_json"])["entries"]
    ids = set(review["entry_ids"])
    for rows in (original, corrected, entries):
        require(
            len(rows) == len(ids) and {r["entry_id"] for r in rows} == ids,
            "Reviewed entry count/IDs differ from bounded scope",
        )
    originals = {r["entry_id"]: r for r in original}
    corrections = {r["entry_id"]: r for r in corrected}
    photos = {f["name"]: f for f in review["files"] if f["role"] == "PHOTO"}
    for e in entries:
        require(e["original_csv"] == originals[e["entry_id"]], "Original observation was changed")
        page = e["printed_page"]
        require(
            type(page) is int
            and page in review["pages"]
            and str(page) == e["original_csv"]["page"],
            "Entry outside reviewed page scope",
        )
        require(
            isinstance(e["reviewed_source_name"], str)
            and 0 < len(e["reviewed_source_name"]) <= 500,
            "Source-attributed name required",
        )
        require(
            type(e["illustrated_mark_confirmed"]) is bool, "Explicit illustration status required"
        )
        require(
            type(e.get("printed_page_number_visible")) is bool,
            "Printed page-number visibility must remain explicit",
        )
        require(
            e["mark_claim_status"]
            in {"REPORTED_BY_BOOK", "UNKNOWN_IN_SOURCE", "PICTORIAL_DESCRIPTION_ONLY"},
            "Unknown mark claim status",
        )
        variants = e["source_reported_text_variants"]
        require(
            isinstance(variants, list)
            and len(variants) <= 50
            and all(isinstance(t, str) and 0 < len(t) <= 1000 for t in variants),
            "Bounded literal variants required",
        )
        require(
            e["mark_claim_status"] == "REPORTED_BY_BOOK" or not variants,
            "Unknown/pictorial-only marks cannot acquire literal lettering",
        )
        require(
            e.get("independently_authenticated") is False
            and all(e.get(k + "_eligible") is False for k in PROHIBITED),
            "Entry cannot expand permitted use or authenticate a maker",
        )
        evidence = e["evidence_photos"]
        expected = {n for n, f in photos.items() if f["page"] == page}
        require(
            len(evidence) == len(expected) and {p["path"] for p in evidence} == expected,
            "Entry must retain every photograph of its page",
        )
        for p in evidence:
            require(
                p["sha256"] == photos[p["path"]]["sha256"] and p["page"] == page,
                "Entry photo citation/hash mismatch",
            )
        c = corrections[e["entry_id"]]
        require(
            c["reviewed_source_name"] == e["reviewed_source_name"]
            and json.loads(c["reviewed_reported_variants"]) == variants
            and c["review_note"] == e["review_note"]
            and c["review_status"] == e["review_status"],
            "Validation CSV and correction authority disagree",
        )
    relations = json.loads(docs["relationships"])
    collisions = json.loads(docs["collisions"])
    require(len(relations) + len(collisions) <= 10000, "Too many reviewed relationships")
    for rel in relations:
        require(
            rel.get("type")
            and rel.get("claim")
            and rel["entries"]
            and set(rel["entries"]) <= ids
            and len(set(rel["entries"])) == len(rel["entries"]),
            "Invalid relationship entry references",
        )
    for group in collisions:
        members = [e["entry_id"] for e in group["entries"]]
        require(
            group.get("normalized_text")
            and 2 <= len(members)
            and set(members) <= ids
            and len(set(members)) == len(members),
            "Invalid text-collision entry references",
        )
    return {
        "review": review,
        "raw_manifest": raw,
        "manifest_sha256": expected_sha256,
        "root": root,
        "entries": entries,
        "relationships": relations,
        "collisions": collisions,
    }
