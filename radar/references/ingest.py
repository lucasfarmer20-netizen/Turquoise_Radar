"""Bounded batches over durable streams and item queues; restart never loses a page."""

import hashlib
import json
import re
from datetime import UTC, datetime

from pydantic import ValidationError

from radar.references.adapters import ADAPTERS, Manifest, RejectRecord
from radar.references.media import MAX_BYTES, InvalidImage, normalize, write_blob
from radar.references.network import PoliteHTTP, StopCollection
from radar.references.store import audit, connect, dump, exclusive, get_source, now

JEWELRY = re.compile(
    r"\b(jewel(?:ry|lery)?|bracelets?|cuffs?|finger[- ]?rings?|rings?|"
    r"earrings?|necklaces?|pendants?|brooch(?:es)?|bolo|concho|silversmith|"
    r"hallmarks?|bangles?|diadem|ornaments?|belt buckle|conchas?|ketoh|watchbands?|naja)\b",
    re.I,
)
NOT_OBJECT = re.compile(
    r"\b(painting|photograph|print|drawing|engraving|poster|book|portrait)\b", re.I
)


def identity(*parts):
    return hashlib.sha256(dump(parts).encode()).hexdigest()


def eligible(source):
    age = (datetime.now(UTC).date() - source.reviewed_on).days
    return (
        source.enabled
        and source.classification in {"OPEN_ACCESS", "INTERNAL_FAIR_USE"}
        and 0 <= age <= 90
    )


def relevant(item):
    # Do not collect a portrait merely because its sitter wears jewelry.
    if NOT_OBJECT.search(item.object_type):
        return False
    return bool(JEWELRY.search(item.title + " " + item.object_type))


def assertion(c, item_id, value):
    data = value.model_dump(mode="json")
    if value.supersedes:
        old = c.execute("SELECT item_id FROM assertions WHERE id=?", (value.supersedes,)).fetchone()
        if not old or old["item_id"] != item_id:
            raise ValueError("Superseded assertion belongs to another item")
    if value.evidence_photo_id:
        photo = c.execute(
            "SELECT item_id FROM assets WHERE id=? AND removed=0", (value.evidence_photo_id,)
        ).fetchone()
        if not photo or photo["item_id"] != item_id:
            raise ValueError("Evidence photo belongs to another item")
    c.execute(
        "INSERT OR IGNORE INTO assertions(item_id,data,fingerprint,created_at) VALUES (?,?,?,?)",
        (item_id, dump(data), identity(data), now()),
    )


def save_asset(c, root, source, item_id, photo, raw):
    normalized, info = normalize(raw)
    from radar.references.holdout import assert_reference_allowed

    assert_reference_allowed(root, info["original_hash"], info["normalized_hash"])
    id_ = identity(item_id, photo.url)
    original = write_blob(root, "originals", info["original_hash"], raw)
    norm = write_blob(root, "normalized", info["normalized_hash"], normalized)
    exact = c.execute(
        "SELECT id FROM assets WHERE removed=0 AND (original_hash=? OR normalized_hash=?) LIMIT 1",
        (info["original_hash"], info["normalized_hash"]),
    ).fetchall()
    candidates = set()
    for n in () if exact else range(8):
        candidates.update(
            r[0]
            for r in c.execute(
                "SELECT asset_id FROM hash_bands WHERE band=? AND value=?",
                (n, info["dhash"][n * 2 : n * 2 + 2]),
            )
        )
    near = []
    for candidate in candidates - {r["id"] for r in exact}:
        row = c.execute(
            "SELECT dhash FROM assets WHERE id=? AND removed=0", (candidate,)
        ).fetchone()
        if row:
            distance = (int(row["dhash"], 16) ^ int(info["dhash"], 16)).bit_count()
            if distance <= 4:
                near.append((candidate, distance))
    data = {
        **info,
        "original_path": original,
        "normalized_path": norm,
        "rights": photo.model_dump(mode="json"),
        "source_review": source.model_dump(mode="json"),
        "retrieved_at": now(),
        "near_duplicates_need_review": bool(near),
    }
    with c:
        c.execute(
            "INSERT INTO assets(id,item_id,url,data,original_hash,normalized_hash,dhash) "
            "VALUES (?,?,?,?,?,?,?)",
            (
                id_,
                item_id,
                photo.url,
                dump(data),
                info["original_hash"],
                info["normalized_hash"],
                info["dhash"],
            ),
        )
        for n in range(8):
            c.execute(
                "INSERT INTO hash_bands VALUES (?,?,?)", (n, info["dhash"][n * 2 : n * 2 + 2], id_)
            )
        for row in exact:
            c.execute(
                "INSERT OR IGNORE INTO duplicates VALUES (?,?,?,?)", (row["id"], id_, "EXACT", 0)
            )
        for other, distance in near:
            c.execute(
                "INSERT OR IGNORE INTO duplicates VALUES (?,?,?,?)",
                (other, id_, "NEAR_REVIEW", distance),
            )
    return bool(exact), bool(near)


def collect(
    root,
    source_id,
    query,
    *,
    max_items=300,
    max_images=400,
    page_size=25,
    max_pages=30,
    max_requests=2000,
    max_bytes=1024 * 1024 * 1024,
    manifest_path=None,
    transport=None,
    clock=None,
    sleep=None,
):
    if not (
        1 <= max_items <= 100000
        and 1 <= max_images <= 100000
        and 1 <= page_size <= 50
        and 1 <= max_pages <= 10000
        and 1 <= max_requests <= 100000
        and max_bytes > 0
    ):
        raise ValueError("Invalid batch bounds")
    with exclusive(root):
        c = connect(root)
        try:
            source = get_source(c, source_id)
            if not eligible(source):
                raise ValueError("Source is excluded or requires a current review")
            if manifest_path is not None:
                if source.access_method != "REVIEWED_MANIFEST":
                    raise ValueError("Source must approve reviewed manifest collection")
                adapter = Manifest(manifest_path, source)
                query = "manifest:" + adapter.digest
            else:
                if source_id not in ADAPTERS or source.access_method != "PUBLIC_API":
                    raise ValueError("Use reviewed manifest import for this source")
                adapter = ADAPTERS[source_id]()
            stream = identity(source_id, query)
            c.execute(
                "INSERT OR IGNORE INTO streams(id,source,query,cursor) VALUES (?,?,?,?)",
                (stream, source_id, query, "{}"),
            )
            # Crash recovery leaves queued items/checkpoints intact.
            c.execute(
                "UPDATE batches SET status='INTERRUPTED',finished_at=? WHERE source=? "
                "AND status='RUNNING'",
                (now(), source_id),
            )
            batch = c.execute(
                "INSERT INTO batches(source,stream,started_at,status,data) "
                "VALUES (?,?,?,'RUNNING','{}')",
                (source_id, stream, now()),
            ).lastrowid
            c.commit()
            kwargs = {k: v for k, v in {"clock": clock, "sleep": sleep}.items() if v is not None}
            http = PoliteHTTP(
                c,
                source,
                transport=transport,
                max_requests=max_requests,
                max_bytes=max_bytes,
                **kwargs,
            )
            stats = dict(
                discovered=0,
                items_processed=0,
                items_stored=0,
                images_stored=0,
                rejected_records=0,
                rejected_images=0,
                duplicate_images=0,
                near_duplicate_images=0,
                existing_images=0,
                pages=0,
            )
            status, error, http_status = "BOUNDED", None, None
            try:
                while stats["items_processed"] < max_items and stats["images_stored"] < max_images:
                    queued = c.execute(
                        "SELECT * FROM queue WHERE stream=? AND state='PENDING' "
                        "ORDER BY id LIMIT 1",
                        (stream,),
                    ).fetchone()
                    if not queued:
                        state = c.execute("SELECT * FROM streams WHERE id=?", (stream,)).fetchone()
                        if state["exhausted"]:
                            status = "EXHAUSTED"
                            break
                        if stats["pages"] >= max_pages:
                            break
                        rows, cursor, exhausted = adapter.page(
                            http, query, json.loads(state["cursor"]), page_size
                        )
                        if not rows and not exhausted:
                            raise StopCollection("PAGINATION_STALLED")
                        if cursor == json.loads(state["cursor"]) and not exhausted:
                            raise StopCollection("PAGINATION_STALLED")
                        with c:
                            for raw in rows:
                                external = str(
                                    raw.get("id", raw.get("pageid", raw.get("external_id", "")))
                                )
                                if not external:
                                    raise StopCollection("INVALID_METADATA")
                                inserted = c.execute(
                                    "INSERT OR IGNORE INTO queue(stream,external_id,payload) "
                                    "VALUES (?,?,?)",
                                    (stream, external, dump(raw)),
                                ).rowcount
                                stats["discovered"] += inserted
                            c.execute(
                                "UPDATE streams SET cursor=?,exhausted=? WHERE id=?",
                                (dump(cursor), int(exhausted), stream),
                            )
                        stats["pages"] += 1
                        continue
                    rejection = None
                    try:
                        item = adapter.item(http, json.loads(queued["payload"]))
                    except RejectRecord as exc:
                        item, rejection = None, exc.reason
                    except (ValidationError, KeyError, TypeError):
                        item = None
                    if item is None or not relevant(item) or not item.photos:
                        reason = rejection or (
                            "INVALID_METADATA"
                            if item is None
                            else "NOT_JEWELRY"
                            if not relevant(item)
                            else "NO_ELIGIBLE_IMAGES"
                        )
                        with c:
                            c.execute(
                                "UPDATE queue SET state='REJECTED',reason=? WHERE id=?",
                                (reason, queued["id"]),
                            )
                        stats["rejected_records"] += 1
                        stats["items_processed"] += 1
                        continue
                    complete = ingest_item(c, root, source, item, http, stats, max_images)
                    if not complete:
                        break
                    with c:
                        c.execute("UPDATE queue SET state='DONE' WHERE id=?", (queued["id"],))
                    stats["items_processed"] += 1
            except StopCollection as exc:
                status, error, http_status = "PAUSED", exc.category, exc.status
            except (ValueError, KeyError, TypeError):
                status, error = "PAUSED", "INVALID_METADATA"
            except BaseException:
                status, error = "INTERRUPTED", "INTERRUPTED"
                c.rollback()
                raise
            finally:
                http.close()
                stats.update(requests=http.requests, downloaded_bytes=http.bytes)
                with c:
                    c.execute(
                        "UPDATE batches SET status=?,finished_at=?,data=? WHERE id=?",
                        (
                            status,
                            now(),
                            dump({**stats, "error": error, "http_status": http_status}),
                            batch,
                        ),
                    )
                    audit(
                        c,
                        error or "BATCH_FINISHED",
                        source_id,
                        batch_id=batch,
                        images=stats["images_stored"],
                        http_status=http_status,
                    )
            return {
                "batch_id": batch,
                "source": source_id,
                "status": status,
                "error": error,
                "http_status": http_status,
                **stats,
            }
        finally:
            c.close()


def ingest_item(c, root, source, item, http, stats, max_images):
    id_ = identity(source.id, item.external_id)
    existing = c.execute("SELECT excluded FROM items WHERE id=?", (id_,)).fetchone()
    if existing and existing["excluded"]:
        return True
    with c:
        inserted = c.execute(
            "INSERT OR IGNORE INTO items(id,source,external_id,data,created_at) VALUES (?,?,?,?,?)",
            (id_, source.id, item.external_id, dump(item.model_dump(mode="json")), now()),
        ).rowcount
        stats["items_stored"] += inserted
        for value in item.assertions:
            assertion(c, id_, value)
    for photo in item.photos:
        if c.execute("SELECT 1 FROM assets WHERE item_id=? AND url=?", (id_, photo.url)).fetchone():
            stats["existing_images"] += 1
            continue
        if stats["images_stored"] >= max_images:
            return False
        try:
            raw = http.get(photo.url, MAX_BYTES)
            exact, near = save_asset(c, root, source, id_, photo, raw)
        except InvalidImage:
            # Rejection is durable, but no bytes or metadata from the invalid image are logged.
            with c:
                audit(c, "IMAGE_REJECTED", identity(id_, photo.url))
            stats["rejected_images"] += 1
            continue
        stats["images_stored"] += 1
        stats["duplicate_images"] += int(exact)
        stats["near_duplicate_images"] += int(near)
    return True
