"""Explicit, one-shot primary-image continuation over a frozen existing Browse cohort.

Preparing a preview is read-only. This module has no OAuth/Browse capability and
does not run on app startup. Private previews contain URLs; public receipts do not.
"""

import asyncio
import fcntl
import hashlib
import json
import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote, urlsplit
from uuid import uuid4

import httpx
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from radar.db import DiscoveredItem, VisionCase, VisionPhoto
from radar.jewelry.images import (
    MAX_BATCH_BYTES,
    MAX_FILE_BYTES,
    original_photo_metadata,
    photo_storage_metadata,
    validate_original_image,
    write_photo,
)
from radar.jewelry.schemas import OWNER

HOST = "i.ebayimg.com"
QUERIES = (
    "blue stone ring",
    "silver ring with blue stone",
    "sterling jewelry lot",
    "mixed jewelry lot",
)
PRESERVED_TABLES = (
    "discovered_items",
    "discovery_observations",
    "search_executions",
    "discovery_runs",
    "listings",
    "feedback",
)


class ImagePilotError(ValueError):
    """Fixed, non-secret messages only; upstream exceptions are never propagated."""


def _now():
    return datetime.now(UTC).isoformat()


def _digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _safe_path(path):
    path = Path(path).absolute()
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ImagePilotError("Private image paths may not contain symlinks")
    return path


def validate_primary_url(url):
    try:
        parsed = urlsplit(url)
        valid = (
            isinstance(url, str)
            and url.isascii()
            and not any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in url)
            and parsed.scheme == "https"
            and parsed.hostname == HOST
            and parsed.netloc in (HOST, HOST + ":443")
            and parsed.port in (None, 443)
            and not parsed.username
            and not parsed.password
            and not parsed.query
            and not parsed.fragment
            and parsed.path.startswith("/images/")
            and "\\" not in url
            and "%" not in parsed.path
            and all(p not in (".", "..") for p in parsed.path.split("/"))
        )
    except (ValueError, TypeError, AttributeError):
        valid = False
    if not valid:
        raise ImagePilotError("Primary reference is outside the exact approved image host")
    return url


def prepare_preview(database_path):
    """Read the original pilot in place; never create, migrate, restore or seed it."""
    path = _safe_path(database_path)
    if not path.is_file() or any(
        Path(str(path) + suffix).exists() for suffix in ("-wal", "-journal")
    ):
        raise ImagePilotError("A closed existing pilot database is required")
    database_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    try:
        with sqlite3.connect(f"file:{quote(str(path))}?mode=ro&immutable=1", uri=True) as db:
            db.row_factory = sqlite3.Row
            if (
                db.execute("PRAGMA integrity_check").fetchone()[0] != "ok"
                or db.execute("PRAGMA foreign_key_check").fetchone()
            ):
                raise ImagePilotError("Pilot database integrity failed")
            rows = db.execute("SELECT * FROM discovered_items ORDER BY id").fetchall()
            runs = db.execute("SELECT * FROM discovery_runs ORDER BY id").fetchall()
            executions = db.execute("SELECT * FROM search_executions ORDER BY id").fetchall()
            observations = db.execute("SELECT * FROM discovery_observations ORDER BY id").fetchall()
            if (
                len(rows) != 8
                or len(runs) != 1
                or runs[0]["mode"] != "production"
                or runs[0]["status"] != "COMPLETED"
                or len(observations) != 12
                or tuple(e["transmitted_query"] for e in executions) != QUERIES
                or any(
                    e["run_id"] != runs[0]["id"] or e["mode"] != "production" for e in executions
                )
                or any(o["execution_id"] not in {e["id"] for e in executions} for o in observations)
                or any(
                    db.execute(f"SELECT 1 FROM {t} LIMIT 1").fetchone()
                    for t in ("vision_photos", "vision_cases")
                )
            ):
                raise ImagePilotError(
                    "Existing pilot does not match the approved eight-item cohort"
                )
            references = []
            for row in rows:
                normalized, raw = json.loads(row["normalized"]), json.loads(row["original_data"])
                urls = normalized.get("image_urls", [])
                if (
                    row["source"] != "eBay"
                    or normalized.get("source") != "eBay"
                    or normalized.get("discovery_mode") != "production"
                    or normalized.get("synthetic") is not False
                    or len(urls) != 1
                    or urls[0] != raw.get("image", {}).get("imageUrl")
                    or normalized.get("source_listing_id") != row["ebay_item_id"]
                ):
                    raise ImagePilotError("Saved primary reference provenance differs")
                url = validate_primary_url(urls[0])
                matching = [
                    o
                    for o in observations
                    if o["item_id"] == row["id"]
                    and json.loads(o["raw_data"]).get("image", {}).get("imageUrl") == url
                ]
                if not matching:
                    raise ImagePilotError("Primary reference lacks a saved Browse observation")
                # SQLite ORM timestamps are written as UTC, then returned without an offset.
                stamps = [datetime.fromisoformat(o["observed_at"]) for o in matching]
                observed = max(
                    s.replace(tzinfo=UTC) if s.tzinfo is None else s.astimezone(UTC) for s in stamps
                )
                references.append(
                    {
                        "candidate_id": row["id"],
                        "source_listing_id": row["ebay_item_id"],
                        "url": url,
                        "source_url_sha256": hashlib.sha256(url.encode()).hexdigest(),
                        "listing_observed_at": observed.isoformat(),
                        "record_sha256": _digest(dict(row)),
                    }
                )
            if len({r["url"] for r in references}) != 8:
                raise ImagePilotError("Approved primary references must be distinct")
            tables = {
                t: _digest([dict(r) for r in db.execute(f"SELECT * FROM {t} ORDER BY rowid")])
                for t in PRESERVED_TABLES
            }
    except ImagePilotError:
        raise
    except (sqlite3.Error, KeyError, TypeError, ValueError, AttributeError, OverflowError):
        raise ImagePilotError("Pilot provenance could not be verified") from None
    if hashlib.sha256(path.read_bytes()).hexdigest() != database_hash:
        raise ImagePilotError("Pilot changed during preview")
    preview = {
        "schema": "jri019a-image-only-1",
        "database": str(path),
        "database_sha256": database_hash,
        "references": references,
        "preserved_tables": tables,
        "requests": {
            "method": "GET",
            "maximum": 8,
            "per_reference": 1,
            "retries": 0,
            "redirects": 0,
            "oauth": 0,
            "browse": 0,
        },
    }
    return {**preview, "sha256": _digest(preview)}


def _fsync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _write_json(path, value, *, exclusive=False):
    path = _safe_path(path)
    raw = (json.dumps(value, indent=2) + "\n").encode()
    target = path if exclusive else path.with_name("." + path.name + "." + uuid4().hex)
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        if not exclusive:
            os.replace(target, path)
        _fsync_directory(path.parent)
    finally:
        if not exclusive:
            target.unlink(missing_ok=True)


def _directory(path):
    path = _safe_path(path)
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.stat().st_mode & 0o077:
        raise ImagePilotError("Private image directory permissions must be 0700")
    return path


def _policy_allows_host():
    try:
        policy = json.loads(Path("/etc/codex/network-policy.json").read_text())
        return policy.get("version") == 1 and HOST in {
            r["host"] for r in policy["http_network_policy"]["egress_rules"]
        }
    except (OSError, ValueError, KeyError, TypeError):
        return False


def _preserved_tables_match(sessions, expected):
    with sessions() as session:
        actual = {
            table: _digest(
                [
                    dict(row)
                    for row in session.connection()
                    .exec_driver_sql(f"SELECT * FROM {table} ORDER BY rowid")
                    .mappings()
                ]
            )
            for table in PRESERVED_TABLES
        }
    return actual == expected


def _store_image(sessions, photo_root, inventory_path, reference, image):
    """Register source-copy removal provenance before bytes or DB rows can appear."""
    photo_id = uuid4().hex
    data = {
        **original_photo_metadata(image),
        "source_listing_id": reference["source_listing_id"],
        "source_url_sha256": reference["source_url_sha256"],
        "listing_observed_at": reference["listing_observed_at"],
        "fetched_at": _now(),
    }
    suffix, _ = photo_storage_metadata(data, image.original_hash, image.content_hash)
    destination = photo_root / (photo_id + suffix)
    try:
        with sessions.begin() as s:
            s.connection().exec_driver_sql("BEGIN IMMEDIATE")
            candidate = s.get(DiscoveredItem, reference["candidate_id"])
            raw_row = (
                s.connection()
                .exec_driver_sql(
                    "SELECT * FROM discovered_items WHERE id=?", (reference["candidate_id"],)
                )
                .mappings()
                .first()
            )
            if not candidate or not raw_row or _digest(dict(raw_row)) != reference["record_sha256"]:
                raise ImagePilotError("Candidate changed before image storage")
            inventory = json.loads(inventory_path.read_text())
            inventory["photo_reviews"]["vision_photos:" + photo_id] = {
                "kind": "seller_copy",
                "path": str(destination),
                "evidence": (
                    "Exact official primary image from the approved frozen Browse cohort; "
                    "original bytes retained and SHA-256 verified."
                ),
            }
            _write_json(inventory_path, inventory)
            write_photo(photo_root, photo_id, image.data, suffix=suffix)
            # write_photo is exclusive; ensure bytes reach durable storage before DB exposure.
            with destination.open("rb") as stream:
                os.fsync(stream.fileno())
            _fsync_directory(photo_root)
            case = s.scalar(select(VisionCase).where(VisionCase.candidate_id == candidate.id))
            if case is None:
                case = VisionCase(
                    candidate_id=candidate.id, listing_id=candidate.listing_id, owner_id=OWNER
                )
                s.add(case)
                s.flush()
            if case.owner_id != OWNER:
                raise ImagePilotError("Private image ownership differs")
            s.add(
                VisionPhoto(
                    id=photo_id,
                    case_id=case.id,
                    role="FRONT",
                    original_hash=image.original_hash,
                    content_hash=image.content_hash,
                    data=data,
                )
            )
    except BaseException:
        # An inventory entry may safely outlive a failed write. Unregistered bytes may not.
        destination.unlink(missing_ok=True)
        _fsync_directory(photo_root)
        raise
    return photo_id


async def execute_preview(
    preview,
    *,
    approved_preview_sha256,
    inventory_path,
    photo_root,
    receipt_dir,
    network_policy_ready=False,
    transport=None,
    sleep=asyncio.sleep,
):
    """Execute only after an owner approves the exact preview and runtime readiness.

    transport exists solely for offline MockTransport qualification; normal calls
    retain the managed proxy, CA trust and TLS verification and check local policy.
    """
    if transport is not None and not isinstance(transport, httpx.MockTransport):
        raise ImagePilotError("Only offline mock transport may replace managed networking")
    if not network_policy_ready or (transport is None and not _policy_allows_host()):
        raise ImagePilotError("Current published image-host policy is not verified ready")
    if not isinstance(preview, dict) or preview.get("sha256") != approved_preview_sha256:
        raise ImagePilotError("Explicit matching image-only preview approval required")
    current = prepare_preview(preview["database"])
    if current != preview:
        raise ImagePilotError("Approved image preview changed; no requests sent")
    receipts, photos = _directory(receipt_dir), _directory(photo_root)
    inventory_path = _safe_path(inventory_path)
    if not inventory_path.is_file():
        raise ImagePilotError("Existing deletion inventory required")
    lock_fd = os.open(
        inventory_path.with_name(".jri019a-image-lock"),
        os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW,
        0o600,
    )
    engine = None
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        inventory = json.loads(inventory_path.read_text())
        if (
            inventory.get("version") != 1
            or inventory.get("inventory_complete") is not True
            or not isinstance(inventory.get("photo_reviews"), dict)
            or not any(
                a.get("kind") == "sqlite" and a.get("path") == preview["database"]
                for a in inventory.get("assets", [])
            )
        ):
            raise ImagePilotError("Deletion inventory does not cover this pilot")
        marker = {"preview_sha256": approved_preview_sha256, "started_at": _now()}
        try:
            # A fixed DB-adjacent marker cannot be bypassed by choosing a new receipt folder.
            _write_json(
                Path(preview["database"]).with_name("JRI-019A-AUTHORIZATION-CONSUMED.json"),
                marker,
                exclusive=True,
            )
        except FileExistsError:
            raise ImagePilotError("Image-only authorization already consumed") from None
        engine = create_engine("sqlite:///" + preview["database"])
        sessions = sessionmaker(engine, expire_on_commit=False)
        receipt = {**marker, "attempts": [], "successful_images": 0, "received_bytes": 0}
        receipt_path = receipts / "JRI-019A-image-receipt.json"
        for reference in preview["references"]:
            if not _preserved_tables_match(sessions, preview["preserved_tables"]):
                receipt["stopped_reason"] = "SOURCE_RECORDS_CHANGED"
                break
            if receipt["received_bytes"] >= MAX_BATCH_BYTES:
                break
            if receipt["attempts"]:
                await sleep(1)
            attempt = {
                "candidate_id": reference["candidate_id"],
                "started_at": _now(),
                "status": "DISPATCH_RESERVED",
                "http_status": None,
                "bytes": 0,
            }
            receipt["attempts"].append(attempt)
            _write_json(receipt_path, receipt)  # durable before any socket operation
            try:
                async with asyncio.timeout(30):
                    # A new client per attempt prevents Set-Cookie propagation.
                    async with httpx.AsyncClient(
                        timeout=15, follow_redirects=False, trust_env=True, transport=transport
                    ) as client:
                        async with client.stream(
                            "GET",
                            validate_primary_url(reference["url"]),
                            headers={
                                "Accept": "image/jpeg,image/png,image/webp",
                                "Accept-Encoding": "identity",
                            },
                        ) as response:
                            attempt["http_status"] = response.status_code
                            if response.status_code != 200:
                                attempt["status"] = "HTTP_REJECTED"
                            elif (
                                response.headers.get("Content-Encoding", "identity").strip().lower()
                                != "identity"
                            ):
                                attempt["status"] = "CONTENT_ENCODING_REJECTED"
                            elif response.headers.get("Content-Type", "").split(";", 1)[
                                0
                            ].strip().lower() not in {"image/jpeg", "image/png", "image/webp"}:
                                attempt["status"] = "CONTENT_TYPE_REJECTED"
                            elif response.headers.get("Content-Length") is not None and (
                                not response.headers["Content-Length"].isascii()
                                or not response.headers["Content-Length"].isdigit()
                                or not 1
                                <= int(response.headers["Content-Length"])
                                <= min(MAX_FILE_BYTES, MAX_BATCH_BYTES - receipt["received_bytes"])
                            ):
                                attempt["status"] = "CONTENT_LENGTH_REJECTED"
                            else:
                                raw = bytearray()
                                async for chunk in response.aiter_raw(chunk_size=64 * 1024):
                                    attempt["bytes"] += len(chunk)
                                    receipt["received_bytes"] += len(chunk)
                                    if (
                                        attempt["bytes"] > MAX_FILE_BYTES
                                        or receipt["received_bytes"] > MAX_BATCH_BYTES
                                    ):
                                        raise ImagePilotError("Image transfer budget exceeded")
                                    raw.extend(chunk)
                                image = validate_original_image(
                                    bytes(raw), response.headers.get("Content-Type")
                                )
                                _store_image(sessions, photos, inventory_path, reference, image)
                                receipt["successful_images"] += 1
                                attempt["status"] = "STORED_EXACT_BYTES"
            except httpx.ProxyError:
                attempt["status"] = "PROXY_REJECTED"
            except (httpx.RequestError, TimeoutError):
                attempt["status"] = "NETWORK_OR_TIMEOUT"
            except (ValueError, OSError):
                attempt["status"] = "VALIDATION_OR_STORAGE_REJECTED"
            except Exception:
                attempt["status"] = "STORAGE_FAILED"
            attempt["completed_at"] = _now()
            _write_json(receipt_path, receipt)
            if attempt["status"] == "PROXY_REJECTED" or attempt["http_status"] == 403:
                break
        receipt["completed_at"] = _now()
        receipt["source_records_unchanged"] = _preserved_tables_match(
            sessions, preview["preserved_tables"]
        )
        receipt["failed_attempts"] = len(receipt["attempts"]) - receipt["successful_images"]
        receipt["unattempted"] = 8 - len(receipt["attempts"])
        _write_json(receipt_path, receipt)
        return receipt
    finally:
        if engine is not None:
            engine.dispose()
        os.close(lock_fd)
