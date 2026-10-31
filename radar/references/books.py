"""Reviewed private book imports: dry run, additive resume, evidence and restore verification."""

import argparse
import json
import shutil
import sqlite3
import tempfile
from pathlib import Path
from urllib.parse import quote

from radar.references.book_source import checked_path, file_bytes, load_bundle, require, sha
from radar.references.directory import connect, digest, stats
from radar.references.store import dump, now, private_root

MIGRATION_2 = (
    "CREATE TABLE book_imports(id TEXT PRIMARY KEY, source TEXT NOT NULL, "
    "review_id TEXT REFERENCES reviews(id), manifest_path TEXT NOT NULL, "
    "complete INTEGER NOT NULL DEFAULT 0)",
    "CREATE TABLE book_evidence(import_id TEXT REFERENCES book_imports(id), name TEXT, "
    "path TEXT NOT NULL, sha256 TEXT NOT NULL, bytes INTEGER NOT NULL, "
    "PRIMARY KEY(import_id,name))",
    "CREATE TABLE book_groups(id TEXT PRIMARY KEY, import_id TEXT REFERENCES book_imports(id), "
    "kind TEXT NOT NULL, data TEXT NOT NULL)",
    "CREATE TABLE book_group_members(group_id TEXT REFERENCES book_groups(id), "
    "record_id TEXT REFERENCES records(id), PRIMARY KEY(group_id,record_id))",
)


def record_id(source, entry):
    return digest(f"{source}:page:{entry['printed_page']}:entry:{entry['entry_id']}".encode())


def snapshot_directory(root, destination):
    """Consistent private pre-write copy; existing destinations are never overwritten."""
    root, destination = Path(root).resolve(), Path(destination).resolve()
    require(
        not destination.exists() and not destination.is_relative_to(root),
        "New backup destination outside active directory required",
    )
    private_root(destination.parent)
    stage = Path(tempfile.mkdtemp(prefix=".book-backup-", dir=destination.parent))
    try:
        for path in root.rglob("*"):
            require(not path.is_symlink(), "Symlink in active private directory")
            if not path.is_file() or path.name.endswith((".db-wal", ".db-shm", ".db-journal")):
                continue
            target = stage / path.relative_to(root)
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            if path == root / "directory.db":
                with sqlite3.connect(f"file:{quote(str(path))}?mode=ro", uri=True) as src:
                    with sqlite3.connect(target) as dst:
                        src.backup(dst)
            else:
                shutil.copyfile(path, target)
            target.chmod(0o600)
        receipt = {
            "files": {
                p.relative_to(stage).as_posix(): sha(p.read_bytes())
                for p in stage.rglob("*")
                if p.is_file()
            },
            "directory": stats(stage),
            "book_evidence": verify(stage),
        }
        (stage / "book-backup-receipt.json").write_text(dump(receipt) + "\n")
        (stage / "book-backup-receipt.json").chmod(0o600)
        stage.rename(destination)
        return receipt
    finally:
        if stage.exists():
            shutil.rmtree(stage)


def stage_evidence(root, bundle):
    """Only immutable, fully checked payloads reach the self-contained directory tree."""
    relative = Path("book_sources") / bundle["manifest_sha256"]
    parent = root / "book_sources"
    require(not parent.is_symlink(), "Symlink in book storage")
    target = root / relative
    if target.exists():
        require(not target.is_symlink(), "Symlink in book storage")
        load_bundle(target / "book-review.json", bundle["manifest_sha256"])
        return relative
    private_root(parent)
    stage = Path(tempfile.mkdtemp(prefix=".staging-", dir=parent))
    try:
        for f in bundle["review"]["files"]:
            raw = file_bytes(bundle["root"], f)  # Recheck immediately before copying.
            p = stage / f["name"]
            p.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            p.write_bytes(raw)
            p.chmod(0o600)
        (stage / "book-review.json").write_bytes(bundle["raw_manifest"])
        (stage / "book-review.json").chmod(0o600)
        load_bundle(stage / "book-review.json", bundle["manifest_sha256"])
        stage.rename(target)
        return relative
    finally:
        if stage.exists():
            shutil.rmtree(stage)


def make_record(bundle, entry, review_id, relative):
    review = bundle["review"]
    source, book = review["source"], review["book"]
    key = record_id(source, entry)
    variants = entry["source_reported_text_variants"]
    page = entry["printed_page"]
    citation = {
        **book,
        "printed_page": page,
        "entry_id": entry["entry_id"],
        "printed_page_number_visible": entry["printed_page_number_visible"],
        "photos": [
            {**p, "private_path": (relative / p["path"]).as_posix()}
            for p in entry["evidence_photos"]
        ],
    }
    ambiguities = [
        r
        for r in bundle["relationships"]
        if entry["entry_id"] in r["entries"]
        and r["type"]
        in {
            "SOURCE_REPORTED_STAMP_REUSE",
            "SOURCE_REPORTED_SHARED_STAMPS",
            "SOURCE_REPORTED_SHARED_STAMP",
            "SOURCE_REPORTED_CO_STAMPING",
        }
    ]
    return {
        "record_id": key,
        "source": source,
        "source_kind": "MANUAL_BOOK",
        "review_id": review_id,
        "entity_id": digest((source + ":source-entry:" + entry["entry_id"]).encode()),
        "maker_name": entry["reviewed_source_name"],
        "identity_kind": "UNRESOLVED",
        "aliases": [],
        "attributed_names": [entry["reviewed_source_name"]],
        "source_caption": entry["reviewed_source_name"],
        "source_url": None,
        "locator": f"{source}:p{page}:{entry['entry_id']}",
        "citation": citation,
        "transcription": "; ".join(variants) or None,
        "transcription_variants": variants,
        "transcription_status": "REPORTED" if variants else "NOT_REVIEWED",
        "mark_claim_status": entry["mark_claim_status"],
        "companion_text": entry.get("source_reported_companion_text", []),
        "description": entry["review_note"],
        "illustrated_mark": entry["illustrated_mark_confirmed"],
        "original_observation": entry["original_csv"],
        "reviewed_correction": entry,
        "review_acceptance": {k: review[k] for k in ("reviewer", "authorization", "reviewed_at")},
        "import_manifest_sha256": bundle["manifest_sha256"],
        "image_urls": [],
        "attribution": "PUBLISHED_REFERENCE_ATTRIBUTION",
        "independently_authenticated": False,
        "source_independence": "NOT_ESTABLISHED",
        "shared_stamp_claims": ambiguities,
        "uncertainty": "SOURCE_ATTRIBUTED",
        "contradictions": [],
        "materials": [],
        "period": None,
        "image_policy": "PRIVATE_REFERENCE_ONLY",
        "permitted_use": review["rights"],
        "training_eligible": False,
        "public_repo_eligible": False,
        "third_party_inference_eligible": False,
        "historical_index_eligible": False,
    }


def groups(bundle, records):
    keys = {e["entry_id"]: r["record_id"] for e, r in zip(bundle["entries"], records, strict=True)}
    result = []
    for rel in bundle["relationships"]:
        members = [keys[e] for e in rel["entries"]]
        if rel.get("existing_record_id"):
            members.append(rel["existing_record_id"])
        result.append((rel["type"], members, rel))
    for group in bundle["collisions"]:
        result.append(("TEXT_COLLISION", [keys[e["entry_id"]] for e in group["entries"]], group))
    for e in bundle["entries"]:
        for match in e.get("directory_matches", []):
            result.append(
                (
                    "RESEARCH_IDENTITY_OVERLAP",
                    [keys[e["entry_id"]], match["record_id"]],
                    {
                        "status": e["directory_crosswalk_status"],
                        "evidence": e["crosswalk_conclusion"],
                        "original_directory_match": match,
                    },
                )
            )
    return [
        (kind, members, dict(data, independently_assessed=False, authenticated=False))
        for kind, members, data in result
    ]


def import_bundle(
    root, manifest, manifest_sha256, *, apply=False, backup_destination=None, max_records=1000
):
    """No database is created implicitly. Committed prefixes resume by stable record IDs."""
    require(type(max_records) is int and 1 <= max_records <= 1000, "Invalid import record bound")
    root = Path(root).resolve()
    bundle = load_bundle(manifest, manifest_sha256)
    review = bundle["review"]
    review_id = digest(dump(review).encode())
    relative = Path("book_sources") / manifest_sha256
    records = [make_record(bundle, e, review_id, relative) for e in bundle["entries"]]
    links = groups(bundle, records)
    with connect(root, write=apply) as c:
        if apply:
            c.execute("BEGIN IMMEDIATE")
        require(
            c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            and not c.execute("PRAGMA foreign_key_check").fetchall(),
            "Directory integrity failed",
        )
        existing = {r["id"]: r for r in c.execute("SELECT * FROM records")}
        proposed = {r["record_id"] for r in records}
        for _, members, _ in links:
            require(set(members) <= proposed | existing.keys(), "Research link target is absent")
        pending = []
        for r in records:
            old = existing.get(r["record_id"])
            require(
                old is None or (old["data"] == dump(r) and old["active"] == 1),
                "Conflicting existing book entry; explicit correction review required",
            )
            if old is None:
                pending.append(r)
        prior_review = c.execute(
            "SELECT r.data FROM reviews r JOIN current_reviews cr ON cr.review_id=r.id "
            "WHERE cr.source=?",
            (review["source"],),
        ).fetchone()
        if prior_review:
            previous = json.loads(prior_review[0])
            require(
                previous.get("source_kind") == "MANUAL_BOOK" and previous["book"] == review["book"],
                "Source ID already identifies different material",
            )
            require(
                previous["facts_allowed"], "Source use revoked; a new explicit review is required"
            )
        result = {
            "source": review["source"],
            "manifest_sha256": manifest_sha256,
            "reviewed_records": len(records),
            "already_present": len(records) - len(pending),
            "pending": len(pending),
            "relationship_groups": len(links),
            "photos": sum(f["role"] == "PHOTO" for f in review["files"]),
            "pages": len(review["pages"]),
            "imported": 0,
            "dry_run": not apply,
        }
        if not apply:
            return result
        if not pending:
            verify(root)
            result["complete"] = True
            return result
        require(backup_destination is not None, "Pre-import private backup destination required")
        snapshot_directory(root, backup_destination)
        private_root(root)
        stage_evidence(root, bundle)
        if c.execute("PRAGMA user_version").fetchone()[0] == 1:
            for sql in MIGRATION_2:
                c.execute(sql)
            c.execute("PRAGMA user_version=2")
        c.execute(
            "INSERT OR IGNORE INTO reviews VALUES (?,?,?,?)",
            (review_id, review["source"], dump(review), now()),
        )
        c.execute(
            "INSERT INTO current_reviews VALUES (?,?) ON CONFLICT(source) DO UPDATE "
            "SET review_id=excluded.review_id",
            (review["source"], review_id),
        )
        c.execute(
            "INSERT OR IGNORE INTO book_imports VALUES (?,?,?,?,0)",
            (
                manifest_sha256,
                review["source"],
                review_id,
                (relative / "book-review.json").as_posix(),
            ),
        )
        files = review["files"] + [
            {
                "name": "book-review.json",
                "sha256": manifest_sha256,
                "bytes": len(bundle["raw_manifest"]),
            }
        ]
        for f in files:
            c.execute(
                "INSERT OR IGNORE INTO book_evidence VALUES (?,?,?,?,?)",
                (
                    manifest_sha256,
                    f["name"],
                    (relative / f["name"]).as_posix(),
                    f["sha256"],
                    f["bytes"],
                ),
            )
        for r in pending[:max_records]:
            key = r["record_id"]
            c.execute(
                "INSERT INTO entities VALUES (?,?,?)",
                (r["entity_id"], r["maker_name"], r["identity_kind"]),
            )
            c.execute(
                "INSERT INTO records VALUES (?,?,?,?,?,?,1)",
                (
                    key,
                    r["source"],
                    r["entity_id"],
                    digest(r["locator"].encode()),
                    dump(r),
                    review_id,
                ),
            )
            for data in (
                {"kind": "ORIGINAL_BOOK_OBSERVATION", "data": r["original_observation"]},
                r,
            ):
                payload = dump(data)
                c.execute(
                    "INSERT INTO observations VALUES (?,?,?,?)",
                    (key, digest(payload.encode()), payload, now()),
                )
            # Raw errors remain in history, not in literal mark fields.
            body = " ".join([r["maker_name"], r["transcription"] or "", r["description"]])
            c.execute("INSERT INTO lookup VALUES (?,?)", (key, body))
        present = {r[0] for r in c.execute("SELECT id FROM records")}
        for kind, members, data in links:
            if not set(members) <= present:
                continue
            group_id = digest(dump([manifest_sha256, kind, sorted(members), data]).encode())
            c.execute(
                "INSERT OR IGNORE INTO book_groups VALUES (?,?,?,?)",
                (group_id, manifest_sha256, kind, dump(data)),
            )
            for member in members:
                c.execute(
                    "INSERT OR IGNORE INTO book_group_members VALUES (?,?)", (group_id, member)
                )
        complete = proposed <= present
        c.execute("UPDATE book_imports SET complete=? WHERE id=?", (int(complete), manifest_sha256))
        c.execute(
            "INSERT INTO events(event,subject,data,created_at) VALUES (?,?,?,?)",
            (
                "BOOK_IMPORT",
                manifest_sha256,
                dump(
                    {
                        "imported": min(len(pending), max_records),
                        "complete": complete,
                        "backup": str(Path(backup_destination).resolve()),
                    }
                ),
                now(),
            ),
        )
        require(not c.execute("PRAGMA foreign_key_check").fetchall(), "Book foreign key failure")
        result.update(imported=min(len(pending), max_records), complete=complete)
    verify(root)
    return result


def verify(root):
    """Verify all retained book payloads and pinned manifests, including incomplete imports."""
    root = Path(root).resolve()
    with connect(root) as c:
        if c.execute("PRAGMA user_version").fetchone()[0] == 1:
            return {"imports": 0, "evidence_files": 0, "records": 0}
        imports = c.execute("SELECT * FROM book_imports").fetchall()
        count = 0
        for item in imports:
            bundle = load_bundle(checked_path(root, item["manifest_path"]), item["id"])
            require(
                digest(dump(bundle["review"]).encode()) == item["review_id"],
                "Retained book review differs from pinned manifest",
            )
            files = c.execute(
                "SELECT * FROM book_evidence WHERE import_id=?", (item["id"],)
            ).fetchall()
            require(
                {f["name"] for f in files}
                == {f["name"] for f in bundle["review"]["files"]} | {"book-review.json"},
                "Retained book evidence inventory is incomplete",
            )
            for f in files:
                file_bytes(root, {"name": f["path"], "bytes": f["bytes"], "sha256": f["sha256"]})
                count += 1
            expected_records = []
            for e in bundle["entries"]:
                expected = make_record(
                    bundle, e, item["review_id"], Path(item["manifest_path"]).parent
                )
                expected_records.append(expected)
                row = c.execute(
                    "SELECT * FROM records WHERE id=?", (record_id(item["source"], e),)
                ).fetchone()
                require(
                    row is not None or not item["complete"], "Completed book import lost a record"
                )
                if row:
                    require(
                        row["data"] == dump(expected)
                        and row["source"] == item["source"]
                        and row["review_id"] == item["review_id"]
                        and row["active"] == 1
                        and row["entity_id"] == expected["entity_id"],
                        "Book record differs from reviewed evidence",
                    )
            all_present = {r[0] for r in c.execute("SELECT id FROM records")}
            for kind, members, data in groups(bundle, expected_records):
                if not set(members) <= all_present:
                    require(not item["complete"], "Completed book import lost a link target")
                    continue
                group_id = digest(dump([item["id"], kind, sorted(members), data]).encode())
                group = c.execute("SELECT data FROM book_groups WHERE id=?", (group_id,)).fetchone()
                require(
                    group is not None and group[0] == dump(data),
                    "Missing/changed book relationship",
                )
                actual = {
                    r[0]
                    for r in c.execute(
                        "SELECT record_id FROM book_group_members WHERE group_id=?", (group_id,)
                    )
                }
                require(actual == set(members), "Book relationship members differ")
        return {
            "imports": len(imports),
            "evidence_files": count,
            "records": c.execute(
                "SELECT count(*) FROM records WHERE source LIKE 'book:%'"
            ).fetchone()[0],
        }


def relationships(c, record):
    if c.execute("PRAGMA user_version").fetchone()[0] < 2:
        return []
    return [
        dict(
            json.loads(r["data"]),
            kind=r["kind"],
            group_id=r["id"],
            related_records=[
                x[0]
                for x in c.execute(
                    "SELECT record_id FROM book_group_members WHERE group_id=? AND record_id<>?",
                    (r["id"], record),
                )
            ],
        )
        for r in c.execute(
            "SELECT g.* FROM book_groups g JOIN book_group_members m "
            "ON m.group_id=g.id WHERE m.record_id=?",
            (record,),
        )
    ]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("data/jri-directory"))
    sub = parser.add_subparsers(dest="command", required=True)
    q = sub.add_parser("import")
    q.add_argument("manifest", type=Path)
    q.add_argument("--manifest-sha256", required=True)
    q.add_argument("--apply", action="store_true")
    q.add_argument("--backup", type=Path)
    q.add_argument("--max-records", type=int, default=1000)
    sub.add_parser("verify")
    args = parser.parse_args()
    result = (
        verify(args.root)
        if args.command == "verify"
        else import_bundle(
            args.root,
            args.manifest,
            args.manifest_sha256,
            apply=args.apply,
            backup_destination=args.backup,
            max_records=args.max_records,
        )
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
