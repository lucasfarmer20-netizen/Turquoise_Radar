"""Private, versioned factual maker/mark directory, separate from historical JRI SQLite."""

import argparse
import hashlib
import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import quote, unquote

from radar.references.directory_adapters import ADAPTERS
from radar.references.directory_sources import SOURCES
from radar.references.network import PoliteHTTP, StopCollection, valid_url
from radar.references.persistence import verify
from radar.references.recognition import GENERIC, normalize_text
from radar.references.store import dump, now, private_root

SCHEMA = "jri-maker-directory-1"
DDL = """
CREATE TABLE reviews(id TEXT PRIMARY KEY,source TEXT NOT NULL,data TEXT NOT NULL,created_at TEXT);
CREATE TABLE current_reviews(source TEXT PRIMARY KEY,review_id TEXT REFERENCES reviews(id));
CREATE TABLE pages(url TEXT PRIMARY KEY,source TEXT NOT NULL,offset INTEGER NOT NULL DEFAULT 0,
 done INTEGER NOT NULL DEFAULT 0,hash TEXT,body_path TEXT);
CREATE TABLE page_versions(url TEXT,hash TEXT,
 review_id TEXT REFERENCES reviews(id),observed_at TEXT,
 PRIMARY KEY(url,hash));
CREATE TABLE entities(id TEXT PRIMARY KEY,name TEXT NOT NULL,kind TEXT NOT NULL);
CREATE TABLE records(id TEXT PRIMARY KEY,source TEXT NOT NULL,
 entity_id TEXT REFERENCES entities(id),
 variant TEXT NOT NULL,data TEXT NOT NULL,
 review_id TEXT REFERENCES reviews(id),active INTEGER DEFAULT 1,
 UNIQUE(source,variant));
CREATE TABLE observations(record_id TEXT REFERENCES records(id),hash TEXT,data TEXT NOT NULL,
 observed_at TEXT,PRIMARY KEY(record_id,hash));
CREATE TABLE events(id INTEGER PRIMARY KEY,event TEXT,subject TEXT,data TEXT,created_at TEXT);
CREATE TABLE rate_state(host TEXT PRIMARY KEY,next_at REAL NOT NULL);
CREATE VIRTUAL TABLE lookup USING fts5(record_id UNINDEXED,body,
 tokenize='unicode61 remove_diacritics 2');
PRAGMA user_version=1;
"""


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


@contextmanager
def connect(root, *, write=False):
    root = Path(root).resolve()
    path = root / "directory.db"
    if not path.is_file() or path.is_symlink():
        raise ValueError("Existing private maker directory required; run explicit init")
    c = sqlite3.connect(f"file:{quote(str(path))}?mode={'rw' if write else 'ro'}", uri=True)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    c.execute("PRAGMA busy_timeout=5000")
    try:
        if c.execute("PRAGMA user_version").fetchone()[0] not in {1, 2}:
            raise ValueError("Unsupported directory schema")
        yield c
        if write:
            c.commit()
    except BaseException:
        c.rollback()
        raise
    finally:
        c.close()


def initialize(root, library):
    """Explicit companion creation only after verifying the complete existing library."""
    stats = verify(library)
    root = private_root(root)
    if root == Path(library).resolve() or root.is_relative_to(Path(library).resolve()):
        raise ValueError("Companion directory must be outside the historical library")
    path = root / "directory.db"
    if path.exists():
        raise ValueError("Refusing to replace an existing directory")
    with sqlite3.connect(path) as c:
        c.executescript(DDL)
    path.chmod(0o600)
    return {"schema": SCHEMA, "historical_library": stats}


def validate_review(data):
    if data.get("source_kind") == "MANUAL_BOOK":
        from radar.references.book_source import validate_review as validate_book_review

        return validate_book_review(data)
    required = {
        "source",
        "hosts",
        "paths",
        "terms_urls",
        "evidence",
        "rationale",
        "facts_allowed",
        "image_policy",
        "reviewed_at",
        "independence_group",
    }
    if not required <= data.keys() or data["image_policy"] != "EXTERNAL_LINK_ONLY":
        raise ValueError("Factual review with separate external-only image policy required")
    if not isinstance(data["facts_allowed"], bool) or not data["evidence"] or not data["rationale"]:
        raise ValueError("Explicit evidenced factual decision required")
    if not isinstance(data["hosts"], list) or not data["hosts"]:
        raise ValueError("Explicit host list required")
    if not isinstance(data["paths"], list) or not data["paths"]:
        raise ValueError("Explicit path list required")
    if not data["hosts"] or any(not re.fullmatch(r"[a-z0-9.-]+", h) for h in data["hosts"]):
        raise ValueError("Explicit normal HTTPS hosts required")
    if not data["paths"] or any(not p.startswith("/") for p in data["paths"]):
        raise ValueError("Reviewed paths required")
    date = datetime.fromisoformat(data["reviewed_at"])
    if date.tzinfo is None or date > datetime.now(UTC) + timedelta(minutes=5):
        raise ValueError("Timezone-aware nonfuture review required")
    if not 1 <= data.get("record_limit", 500) <= 100000:
        raise ValueError("Invalid reviewed record bound")
    return data


def put_review(root, data):
    data = validate_review(dict(data))
    key = digest(dump(data).encode())
    with connect(root, write=True) as c:
        c.execute(
            "INSERT OR IGNORE INTO reviews VALUES (?,?,?,?)",
            (key, data["source"], dump(data), now()),
        )
        c.execute(
            "INSERT INTO current_reviews VALUES (?,?) ON CONFLICT(source) DO UPDATE "
            "SET review_id=excluded.review_id",
            (data["source"], key),
        )
    return key


def current_review(c, source):
    r = c.execute(
        "SELECT r.* FROM reviews r JOIN current_reviews cr ON cr.review_id=r.id WHERE cr.source=?",
        (source,),
    ).fetchone()
    if not r:
        raise ValueError("Missing source review")
    data = validate_review(json.loads(r["data"]))
    # Offline, pinned editions do not acquire new terms/content every 30 days.
    # Revocation still applies. Each expansion needs its own bounded reviewed packet.
    if data.get("source_kind") != "MANUAL_BOOK" and (
        datetime.now(UTC) - datetime.fromisoformat(data["reviewed_at"]) > timedelta(days=30)
    ):
        raise ValueError("Source review expired; reassessment required")
    return r["id"], data


def approved_url(url, review):
    p = valid_url(url, review["hosts"])
    if review.get("selected_urls") and url not in review["selected_urls"]:
        raise ValueError("URL outside reviewed factual selection")
    decoded = unquote(p.path or "/")
    if "\\" in decoded or any(part in {".", ".."} for part in decoded.split("/")):
        raise ValueError("Noncanonical reviewed URL path")
    if not any((p.path or "/").startswith(prefix) for prefix in review["paths"]):
        raise ValueError("URL outside reviewed collection paths")
    return p


def enqueue(root, source, urls):
    with connect(root, write=True) as c:
        _, review = current_review(c, source)
        if not review["facts_allowed"] or source not in ADAPTERS:
            raise ValueError("No eligible factual directory adapter")
        for url in urls:
            approved_url(url, review)
            c.execute("INSERT OR IGNORE INTO pages(url,source) VALUES (?,?)", (url, source))


def validate_record(record):
    r = dict(record)
    if not r.get("maker_name", "").strip() or len(r["maker_name"]) > 500:
        raise ValueError("Named source-attributed maker/mark required")
    if r.get("transcription_status") not in {"NOT_REVIEWED", "REPORTED", "LEGIBLE", "PARTIAL"}:
        raise ValueError("Explicit transcription status required")
    if r.get("identity_kind") not in {
        "PERSON",
        "WORKSHOP",
        "TRADING_POST",
        "MANUFACTURER",
        "UNRESOLVED",
    }:
        raise ValueError("Explicit identity kind required")
    if r.get("transcription_status") == "LEGIBLE" and not r.get("transcription"):
        raise ValueError("Legible transcript must be present")
    r.update(
        attribution="SOURCE_ATTRIBUTED",
        independently_authenticated=False,
        training_eligible=False,
        image_policy="EXTERNAL_LINK_ONLY",
    )
    return r


def upsert(c, record, review_id, source):
    r = validate_record(record)
    current_id, current = current_review(c, source)
    if current.get("source_kind") == "MANUAL_BOOK":
        raise ValueError("Book entries require the offline reviewed-packet importer")
    if current_id != review_id or not current["facts_allowed"]:
        raise ValueError("Current eligible factual source review required")
    approved_url(r["source_url"], current)
    for image_url in r.get("image_urls", []):
        valid_url(image_url, current["hosts"])
    entity = digest(
        (source + ":" + normalize_text(r["maker_name"]) + ":" + r["identity_kind"]).encode()
    )
    # Do not globally merge homonyms or silently infer artist/shop aliases.
    c.execute(
        "INSERT OR IGNORE INTO entities VALUES (?,?,?)",
        (entity, r["maker_name"], r["identity_kind"]),
    )
    variant = digest(r["locator"].encode())
    if current.get("selected_variants") and variant not in current["selected_variants"]:
        raise ValueError("Variant outside reviewed factual selection")
    key = digest((source + ":" + variant).encode())
    r.update(record_id=key, source=source, entity_id=entity, review_id=review_id)
    r["attributed_names"] = [r["maker_name"]]
    previous = c.execute("SELECT data FROM records WHERE id=?", (key,)).fetchone()
    total = c.execute("SELECT count(*) FROM records WHERE source=?", (source,)).fetchone()[0]
    if previous is None and total >= current.get("record_limit", 500):
        raise ValueError("Reviewed source record limit reached")
    if previous:
        old = json.loads(previous["data"])
        if old.get("review_annotation") and not r.get("review_annotation"):
            r["pending_source_refresh"] = r.copy()
            for field in [
                "aliases",
                "identity_kind",
                "transcription",
                "transcription_status",
                "description",
                "materials",
                "period",
                "review_annotation",
            ]:
                r[field] = old.get(field)
            r["contradictions"] = old.get("contradictions", [])
        names = set(old.get("attributed_names", [old["maker_name"]])) | {r["maker_name"]}
        r["attributed_names"] = sorted(names)
        if len({normalize_text(n) for n in names}) > 1:
            r["contradictions"] = list(
                dict.fromkeys(
                    old.get("contradictions", [])
                    + ["Different captions attribute this variant to: " + "; ".join(sorted(names))]
                )
            )
            r["uncertainty"] = "SOURCE_CONFLICT"
    payload = dump(r)
    fingerprint = digest(payload.encode())
    c.execute(
        "INSERT OR IGNORE INTO records VALUES (?,?,?,?,?,?,1)",
        (key, source, entity, variant, payload, review_id),
    )
    c.execute(
        "UPDATE records SET entity_id=?,data=?,review_id=?,active=1 WHERE id=?",
        (entity, payload, review_id, key),
    )
    c.execute(
        "INSERT OR IGNORE INTO observations VALUES (?,?,?,?)", (key, fingerprint, payload, now())
    )
    if c.execute(
        "SELECT 1 FROM events WHERE event='QUALITY_EXCLUDED' AND subject=?", (key,)
    ).fetchone():
        c.execute("UPDATE records SET active=0 WHERE id=?", (key,))
    c.execute("DELETE FROM lookup WHERE record_id=?", (key,))
    body = " ".join(
        [
            r["maker_name"],
            " ".join(r.get("aliases", [])),
            r.get("transcription") or "",
            r.get("description") or "",
            r.get("source_caption") or "",
            " ".join(r.get("attributed_names", [])),
            " ".join(r.get("image_urls", [])),
        ]
    )
    c.execute("INSERT INTO lookup VALUES (?,?)", (key, body))
    return key


def revise(root, record_id, patch, evidence):
    """Append a reviewed factual correction; never edit source observations in place."""
    allowed = {
        "aliases",
        "identity_kind",
        "transcription",
        "transcription_status",
        "description",
        "materials",
        "period",
        "contradictions",
    }
    if not patch or set(patch) - allowed or not evidence.strip():
        raise ValueError("Bounded factual patch and source evidence required")
    with connect(root, write=True) as c:
        row = c.execute("SELECT * FROM records WHERE id=?", (record_id,)).fetchone()
        if not row:
            raise ValueError("Unknown directory record")
        review_id, review = current_review(c, row["source"])
        if not review["facts_allowed"]:
            raise ValueError("Source factual use revoked")
        r = json.loads(row["data"])
        r.update(patch)
        r["review_annotation"] = {"evidence": evidence, "reviewed_at": now()}
        return upsert(c, r, review_id, row["source"])


def relate(root, left, right, kind, evidence, *, independently_assessed=False):
    if (
        kind not in {"SOURCE_AGREEMENT", "POSSIBLE_VARIANT", "DIFFICULT_NEGATIVE", "CONTRADICTION"}
        or not evidence.strip()
    ):
        raise ValueError("Documented relationship kind and evidence required")
    with connect(root, write=True) as c:
        rows = [
            c.execute("SELECT source FROM records WHERE id=?", (key,)).fetchone()
            for key in [left, right]
        ]
        if not all(rows) or left == right:
            raise ValueError("Two existing distinct records required")
        reviews = [current_review(c, row["source"])[1] for row in rows]
        if (
            independently_assessed
            and reviews[0]["independence_group"] == reviews[1]["independence_group"]
        ):
            raise ValueError("Same source family cannot supply independent corroboration")
        if independently_assessed:
            raise ValueError(
                "Use the cross-source review with attribution provenance on both sides"
            )
        for a, b in [(left, right), (right, left)]:
            value = {
                "related_record": b,
                "kind": kind,
                "evidence": evidence,
                "independently_assessed": independently_assessed,
                "authenticated": False,
            }
            c.execute(
                "INSERT INTO events(event,subject,data,created_at) VALUES (?,?,?,?)",
                ("RELATIONSHIP_REVIEW", a, dump(value), now()),
            )


def collect(
    root, source, *, live=False, max_pages=5, max_records=500, refresh=False, transport=None
):
    if not live or not 1 <= max_pages <= 50 or not 1 <= max_records <= 100000:
        raise ValueError("Explicit live and bounded pages/records required")
    root = Path(root).resolve()
    count = pages = 0
    with connect(root, write=True) as c:
        review_id, review = current_review(c, source)
        if not review["facts_allowed"] or source not in ADAPTERS:
            raise ValueError("Source not eligible for factual ingestion")
        known = {r[0] for r in c.execute("SELECT variant FROM records WHERE source=?", (source,))}
        if (
            not refresh
            and review.get("selected_variants")
            and set(review["selected_variants"]) <= known
        ):
            return {"records_processed": 0, "pages": 0, "stop": None, "selection_complete": True}
        http = PoliteHTTP(
            c,
            SimpleNamespace(hosts=review["hosts"], interval_seconds=3),
            transport=transport,
            max_requests=max_pages * 5 + 5,
            max_bytes=max_pages * 2_000_000 + 512000,
        )
        try:
            pending = c.execute(
                "SELECT * FROM pages WHERE source=? AND (done=0 OR ?=1) ORDER BY rowid LIMIT ?",
                (source, int(refresh), max_pages),
            ).fetchall()
            for page in pending:
                approved_url(page["url"], review)
                try:
                    raw = http.get(page["url"], cap=2_000_000)
                except StopCollection as exc:
                    c.execute(
                        "INSERT INTO events(event,subject,data,created_at) VALUES (?,?,?,?)",
                        (
                            "COLLECTION_STOP",
                            page["url"],
                            dump({"reason": exc.category, "status": exc.status}),
                            now(),
                        ),
                    )
                    c.commit()
                    return {"records_processed": count, "pages": pages, "stop": exc.category}
                fingerprint = digest(raw)
                cache = root / "pages" / (fingerprint + ".html")
                cache.parent.mkdir(exist_ok=True, mode=0o700)
                cache.write_bytes(raw)
                cache.chmod(0o600)
                parsed = ADAPTERS[source](raw, page["url"])
                if review.get("selected_variants"):
                    selected = set(review["selected_variants"])
                    parsed = [r for r in parsed if digest(r["locator"].encode()) in selected]
                if not parsed:
                    raise ValueError("Adapter produced no records; refusing exhausted checkpoint")
                offset = page["offset"] if page["hash"] == fingerprint and not refresh else 0
                for r in parsed[offset:]:
                    if count >= max_records:
                        break
                    source_count = c.execute(
                        "SELECT count(*) FROM records WHERE source=?", (source,)
                    ).fetchone()[0]
                    key = digest((source + ":" + digest(r["locator"].encode())).encode())
                    if (
                        source_count >= review.get("record_limit", 500)
                        and not c.execute("SELECT 1 FROM records WHERE id=?", (key,)).fetchone()
                    ):
                        break
                    approved_url(r["source_url"], review)
                    upsert(c, r, review_id, source)
                    offset += 1
                    count += 1
                c.execute(
                    "INSERT OR IGNORE INTO page_versions VALUES (?,?,?,?)",
                    (page["url"], fingerprint, review_id, now()),
                )
                c.execute(
                    "UPDATE pages SET offset=?,done=?,hash=?,body_path=? WHERE url=?",
                    (
                        offset,
                        int(offset == len(parsed)),
                        fingerprint,
                        str(cache.relative_to(root)),
                        page["url"],
                    ),
                )
                c.commit()
                pages += 1
                if count >= max_records or offset < len(parsed):
                    break
        finally:
            http.close()
    return {"records_processed": count, "pages": pages, "stop": None}


def search(root, query, limit=20, *, partial=False):
    from radar.references.books import relationships as book_relationships

    terms = normalize_text(query).split()
    if not terms or not 1 <= limit <= 100:
        raise ValueError("Nonempty bounded search required")
    # Parameterized FTS expression built from normalized alphanumerics only.
    expression = " AND ".join('"' + t + '"' + ("*" if partial else "") for t in terms)
    with connect(root) as c:
        rows = c.execute(
            "SELECT r.*,bm25(lookup) AS rank FROM lookup JOIN records r "
            "ON r.id=lookup.record_id WHERE lookup MATCH ? AND r.active=1 "
            "ORDER BY rank,r.id LIMIT ?",
            (expression, limit * 5),
        ).fetchall()
        result = []
        for row in rows:
            _, review = current_review(c, row["source"])
            if review["facts_allowed"]:
                record = json.loads(row["data"])
                record["first_collected_at"] = c.execute(
                    "SELECT min(observed_at) FROM observations WHERE record_id=?", (row["id"],)
                ).fetchone()[0]
                record["last_source_reviewed_at"] = review["reviewed_at"]
                record["confidence"] = "SOURCE_ATTRIBUTION_UNCALIBRATED"
                record["relationships"] = [
                    json.loads(e[0])
                    for e in c.execute(
                        "SELECT data FROM events WHERE event='RELATIONSHIP_REVIEW' AND subject=?",
                        (row["id"],),
                    )
                ] + book_relationships(c, row["id"])
                result.append(record)
            if len(result) == limit:
                break
        return result


def source_registry(root):
    result = []
    with connect(root) as c:
        entries = dict(SOURCES)
        for row in c.execute("SELECT source FROM current_reviews"):
            if row[0] not in entries:
                _, reviewed = current_review(c, row[0])
                entries[row[0]] = (
                    {"source_kind": "MANUAL_BOOK", "book": reviewed["book"]}
                    if reviewed.get("source_kind") == "MANUAL_BOOK"
                    else {
                        "directory_url": reviewed["terms_urls"][0],
                        "terms_url": reviewed["terms_urls"][0],
                    }
                )
        for source, links in entries.items():
            try:
                _, review = current_review(c, source)
            except ValueError as exc:
                review = {"facts_allowed": False, "review_problem": str(exc)}
            if review.get("selected_variants"):
                selected = review.pop("selected_variants")
                review["selected_variant_count"] = len(selected)
                review["selected_variants_sha256"] = digest(dump(selected).encode())
            result.append(dict(links, source=source, review=review))
    return result


def stats(root):
    with connect(root) as c:
        if (
            c.execute("PRAGMA integrity_check").fetchone()[0] != "ok"
            or c.execute("PRAGMA foreign_key_check").fetchall()
        ):
            raise ValueError("Directory integrity failure")
        return {
            "records": c.execute("SELECT count(*) FROM records WHERE active=1").fetchone()[0],
            "entities": c.execute(
                "SELECT count(DISTINCT entity_id) FROM records WHERE active=1"
            ).fetchone()[0],
            "sources": [
                dict(r)
                for r in c.execute(
                    "SELECT source,count(*) AS records FROM records WHERE active=1 GROUP BY source"
                )
            ],
            "pages": [dict(r) for r in c.execute("SELECT url,offset,done FROM pages")],
            "downloaded_images": 0,
            "independently_authenticated": 0,
        }


def distinct_tokens(text):
    return {
        t
        for t in normalize_text(text or "").split()
        if t not in GENERIC and not t.isdigit() and not re.fullmatch(r"\d+K(?:T)?", t)
    }


def hybrid(root, transcript="", *, legibility="PARTIAL", description="", visual_matches=()):
    """Name/description research leads are separate from literal photographed-mark support."""
    tokens = distinct_tokens(transcript)
    distinctive = {t for t in tokens if len(t) >= 3}
    leads = {}
    initials = "".join(t for t in normalize_text(transcript).split() if t in tokens)
    search_terms = (distinctive or tokens) | (
        {initials} if tokens and len(initials) <= 4 else set()
    )
    for t in sorted(search_terms):
        for r in search(root, t, limit=100, partial=legibility == "PARTIAL"):
            leads[r["record_id"]] = r
    if description.strip():
        for t in sorted(distinct_tokens(description)):
            if len(t) >= 3:
                for r in search(root, t, limit=20):
                    leads[r["record_id"]] = r
    candidates = []
    for r in leads.values():
        # A book may report multiple marks for one person. Their union is not one
        # literal inscription, and unknown/pictorial prose is never a text variant.
        variants = r.get("transcription_variants", [r.get("transcription")])
        exact = bool(
            distinctive
            and any(tokens == distinct_tokens(v) for v in variants)
            and r["transcription_status"] in {"LEGIBLE", "REPORTED"}
            and legibility == "LEGIBLE"
            and not r.get("contradictions")
        )
        overlap = len(tokens & distinct_tokens(r["maker_name"] + " " + r["description"]))
        candidates.append(
            dict(
                r,
                evidence="SOURCE_MARK_TEXT_SUPPORT" if exact else "RESEARCH_LEAD_ONLY",
                textual_support=exact,
                score=2 * int(exact) + overlap,
            )
        )
    candidates.sort(key=lambda r: (-r["score"], r["record_id"]))
    filtered_visual = []
    for m in visual_matches:
        ref_tokens = distinct_tokens(m.get("transcription"))
        # Only complete, reviewed, distinctive lettering can establish a contradiction.
        contradiction = bool(
            legibility == "LEGIBLE"
            and distinctive
            and m.get("legibility") == "LEGIBLE"
            and {t for t in ref_tokens if len(t) >= 3}
            and not distinctive & ref_tokens
        )
        filtered_visual.append(
            dict(
                m,
                text_consistency="CONTRADICTORY" if contradiction else "UNRESOLVED",
                supported=False,
                adjusted_similarity=m["similarity"] - int(contradiction),
            )
        )
    filtered_visual.sort(key=lambda m: (-m["adjusted_similarity"], m["reference_id"]))
    supported = [c for c in candidates if c["textual_support"]]
    identities = {c["entity_id"] for c in supported}
    unambiguous = len(identities) == 1 and not any(c.get("shared_stamp_claims") for c in supported)
    return {
        "decision": "CANDIDATE" if unambiguous else "UNKNOWN",
        "reason": "SOURCE_MARK_TEXT_SUPPORT_NOT_AUTHENTICATION"
        if unambiguous
        else "NO_UNAMBIGUOUS_SUPPORTED_MATCH",
        "candidates": candidates[:10],
        "visual_candidates": filtered_visual,
        "warning": (
            "Research candidates, not authentication. Cosine is not a probability. "
            "Missing letters remain unknown."
        ),
    }


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root", type=Path, default=Path("data/jri-directory"))
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("init").add_argument("--library", type=Path, default=Path("data/jri"))
    sub.add_parser("review").add_argument("file", type=Path)
    q = sub.add_parser("enqueue")
    q.add_argument("source")
    q.add_argument("urls", nargs="+")
    q = sub.add_parser("collect")
    q.add_argument("source")
    q.add_argument("--live", action="store_true")
    q.add_argument("--max-pages", type=int, default=5)
    q.add_argument("--max-records", type=int, default=500)
    q.add_argument("--refresh", action="store_true")
    q = sub.add_parser("search")
    q.add_argument("query")
    q.add_argument("--limit", type=int, default=20)
    q.add_argument("--partial", action="store_true")
    q = sub.add_parser("revise")
    q.add_argument("record_id")
    q.add_argument("patch", type=Path)
    q.add_argument("--evidence", required=True)
    q = sub.add_parser("import-library")
    q.add_argument("--library", type=Path, default=Path("data/jri"))
    q.add_argument("--include-internal-fair-use", action="store_true")
    q = sub.add_parser("hybrid")
    q.add_argument("--transcription", default="")
    q.add_argument("--legibility", choices=["LEGIBLE", "PARTIAL", "UNREADABLE"], default="PARTIAL")
    q.add_argument("--description", default="")
    q.add_argument("--visual-results", type=Path)
    sub.add_parser("stats")
    sub.add_parser("sources")
    a = p.parse_args()
    if a.command == "init":
        result = initialize(a.root, a.library)
    elif a.command == "review":
        result = put_review(a.root, json.loads(a.file.read_text()))
    elif a.command == "enqueue":
        result = enqueue(a.root, a.source, a.urls)
    elif a.command == "collect":
        result = collect(
            a.root,
            a.source,
            live=a.live,
            max_pages=a.max_pages,
            max_records=a.max_records,
            refresh=a.refresh,
        )
    elif a.command == "search":
        result = search(a.root, a.query, a.limit, partial=a.partial)
    elif a.command == "revise":
        result = revise(a.root, a.record_id, json.loads(a.patch.read_text()), a.evidence)
    elif a.command == "import-library":
        from radar.references.directory_library import import_library

        result = import_library(a.root, a.library, include_fair_use=a.include_internal_fair_use)
    elif a.command == "hybrid":
        matches = json.loads(a.visual_results.read_text())["matches"] if a.visual_results else []
        result = hybrid(
            a.root,
            a.transcription,
            legibility=a.legibility,
            description=a.description,
            visual_matches=matches,
        )
    elif a.command == "sources":
        result = source_registry(a.root)
    else:
        result = stats(a.root)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
