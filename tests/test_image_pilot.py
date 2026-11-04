"""Mock-only qualification of one-shot image continuation; all records are synthetic."""

import asyncio
import io
import json
import sqlite3
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from PIL import Image

from radar.db import DiscoveredItem, DiscoveryObservation, DiscoveryRun, SearchExecution, database
from radar.discovery import image_pilot as pilot
from radar.schemas import ListingInput


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    async def deny(*args, **kwargs):
        raise AssertionError("No real image or API traffic in qualification tests")

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", deny)


@pytest.fixture
def cohort(tmp_path):
    path = tmp_path / "synthetic-pilot.db"
    engine, sessions = database("sqlite:///" + str(path))
    observed = datetime.now(UTC) - timedelta(hours=15)
    with sessions.begin() as s:
        run = DiscoveryRun(
            mode="production", options={}, status="COMPLETED", new_count=8, duplicate_count=4
        )
        s.add(run)
        s.flush()
        items = []
        for index in range(8):
            url = f"https://i.ebayimg.com/images/g/SYNTHETIC-{index}/s-l225.jpg"
            raw = {
                "itemId": f"SYNTHETIC-{index}",
                "title": "SYNTHETIC offline test item",
                "image": {"imageUrl": url},
            }
            normalized = ListingInput(
                source="eBay",
                source_listing_id=raw["itemId"],
                raw_title=raw["title"],
                image_urls=[url],
                discovery_mode="production",
                synthetic=False,
            ).model_dump(mode="json")
            item = DiscoveredItem(
                identity=f"SYNTHETIC-{index}",
                source="eBay",
                ebay_item_id=raw["itemId"],
                normalized=normalized,
                original_data=raw,
            )
            s.add(item)
            s.flush()
            items.append(item)
        for query, indices in zip(
            pilot.QUERIES, ((0, 1, 2), (0, 1, 2), (3, 4, 5), (5, 6, 7)), strict=True
        ):
            execution = SearchExecution(
                run_id=run.id,
                source="eBay",
                mode="production",
                group_ids=[],
                original_query=query,
                transmitted_query=query,
                parameters={},
                status="COMPLETED",
                results_returned=3,
                executed_at=observed,
            )
            s.add(execution)
            s.flush()
            for index in indices:
                s.add(
                    DiscoveryObservation(
                        execution_id=execution.id,
                        item_id=items[index].id,
                        raw_data=items[index].original_data,
                        correction_state="UNKNOWN",
                        observed_at=observed,
                    )
                )
    engine.dispose()
    inventory = tmp_path / "inventory.json"
    inventory.write_text(
        json.dumps(
            {
                "version": 1,
                "inventory_complete": True,
                "assets": [{"kind": "sqlite", "path": str(path)}],
                "photo_reviews": {},
            }
        )
    )
    return path, inventory, tmp_path / "photos", tmp_path / "receipts"


class Chunks(httpx.AsyncByteStream):
    def __init__(self, chunks):
        self.chunks = chunks

    async def __aiter__(self):
        for chunk in self.chunks:
            yield chunk


def jpeg():
    out = io.BytesIO()
    Image.new("RGB", (32, 48), "blue").save(out, format="JPEG")
    return out.getvalue()


async def no_wait(_):
    pass


def execute(cohort, handler, **kwargs):
    db, inventory, photos, receipts = cohort
    preview = pilot.prepare_preview(db)
    return asyncio.run(
        pilot.execute_preview(
            preview,
            approved_preview_sha256=preview["sha256"],
            inventory_path=inventory,
            photo_root=photos,
            receipt_dir=receipts,
            network_policy_ready=True,
            transport=httpx.MockTransport(handler),
            sleep=no_wait,
            **kwargs,
        )
    )


def test_readonly_preview_and_exact_cohort(cohort):
    path, *_ = cohort
    original = path.read_bytes()
    preview = pilot.prepare_preview(path)
    assert path.read_bytes() == original
    assert preview == pilot.prepare_preview(path)
    assert len(preview["references"]) == 8 and preview["requests"]["maximum"] == 8
    assert preview["requests"]["oauth"] == preview["requests"]["browse"] == 0
    with sqlite3.connect(path) as db:
        db.execute("DELETE FROM discovery_observations WHERE id=1")
    with pytest.raises(pilot.ImagePilotError, match="cohort"):
        pilot.prepare_preview(path)


@pytest.mark.parametrize(
    "url",
    [
        "http://i.ebayimg.com/images/x.jpg",
        "https://i.ebayimg.com.attacker.test/images/x.jpg",
        "https://user:pass@i.ebayimg.com/images/x.jpg",
        "https://i.ebayimg.com:444/images/x.jpg",
        "https://i.ebayimg.com/images/x.jpg?url=other",
        "https://i.ebayimg.com/images/x.jpg#fragment",
        "https://i.ebayimg.com/images/../x.jpg",
        "https://i.ebayimg.com/images/%2e/x.jpg",
        "https://api.ebay.com/images/x.jpg",
        "https://i.ebayimg.com/other/x.jpg",
        "https://i.ebayimg.com/images/x\x00.jpg",
        "https://i.ebayimg.com/images/x\x7f.jpg",
    ],
)
def test_reject_unapproved_urls(url):
    with pytest.raises(pilot.ImagePilotError):
        pilot.validate_primary_url(url)


@pytest.mark.parametrize(
    "table,column,value",
    [
        ("discovered_items", "normalized", json.dumps(["SYNTHETIC_PRIVATE_VALUE"])),
        ("discovered_items", "original_data", json.dumps({"image": ["SYNTHETIC_PRIVATE_VALUE"]})),
        ("discovery_observations", "observed_at", "SYNTHETIC_PRIVATE_VALUE"),
        ("discovery_observations", "raw_data", "SYNTHETIC_PRIVATE_VALUE"),
    ],
)
def test_malformed_provenance_uses_fixed_error(cohort, table, column, value):
    with sqlite3.connect(cohort[0]) as db:
        db.execute(f"UPDATE {table} SET {column}=? WHERE id=1", (value,))
    with pytest.raises(pilot.ImagePilotError) as error:
        pilot.prepare_preview(cohort[0])
    assert str(error.value) == "Pilot provenance could not be verified"
    assert "SYNTHETIC_PRIVATE_VALUE" not in str(error.value)


def test_exact_bytes_eight_requests_no_headers_and_source_tables_preserved(cohort):
    db, inventory, photos, receipts = cohort
    baseline = pilot.prepare_preview(db)
    raw = jpeg()
    requests = []

    def handler(request):
        requests.append(request)
        assert request.method == "GET" and request.url.host == "i.ebayimg.com"
        assert "authorization" not in request.headers and "cookie" not in request.headers
        assert request.headers["accept-encoding"] == "identity"
        reserved = json.loads((receipts / "JRI-019A-image-receipt.json").read_text())
        assert reserved["attempts"][-1]["status"] == "DISPATCH_RESERVED"
        return httpx.Response(
            200,
            headers={"Content-Type": "image/jpeg", "Set-Cookie": "MUST_NOT_PROPAGATE=1"},
            stream=Chunks([raw]),
        )

    result = execute(cohort, handler)
    assert result["successful_images"] == 8 and len(requests) == 8
    assert result["source_records_unchanged"] is True
    assert len(list(photos.glob("*.jpg"))) == 8
    assert all(p.read_bytes() == raw for p in photos.glob("*.jpg"))
    assert len(json.loads(inventory.read_text())["photo_reviews"]) == 8
    with sqlite3.connect(db) as c:
        c.row_factory = sqlite3.Row
        for table, digest in baseline["preserved_tables"].items():
            assert (
                pilot._digest([dict(r) for r in c.execute(f"SELECT * FROM {table} ORDER BY rowid")])
                == digest
            )
        data = json.loads(c.execute("SELECT data FROM vision_photos LIMIT 1").fetchone()[0])
        assert (
            data["metadata_removed"] is False and data["listing_observed_at"] != data["fetched_at"]
        )
    assert (db.parent / "JRI-019A-AUTHORIZATION-CONSUMED.json").exists()


def test_redirects_are_not_followed_and_marker_cannot_move(cohort):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(302, headers={"Location": "https://unrelated.test/image.jpg"})

    result = execute(cohort, handler)
    assert len(requests) == 8 and result["successful_images"] == 0
    assert all(a["status"] == "HTTP_REJECTED" for a in result["attempts"])
    db, inventory, photos, receipts = cohort
    with pytest.raises(pilot.ImagePilotError, match="consumed"):
        execute((db, inventory, photos, receipts.with_name("other-receipts")), handler)
    assert len(requests) == 8


@pytest.mark.parametrize(
    "headers",
    [
        {"Content-Type": "text/html"},
        {"Content-Type": "image/png"},
        {"Content-Type": "image/jpeg", "Content-Encoding": "gzip"},
    ],
)
def test_mime_and_encoding_fail_without_storage(cohort, headers):
    raw = jpeg()
    result = execute(cohort, lambda _: httpx.Response(200, headers=headers, stream=Chunks([raw])))
    assert result["successful_images"] == 0
    assert not list(cohort[2].iterdir())


def test_transfer_caps_count_failed_body_without_retaining_it(cohort, monkeypatch):
    monkeypatch.setattr(pilot, "MAX_FILE_BYTES", 10)
    monkeypatch.setattr(pilot, "MAX_BATCH_BYTES", 15)
    result = execute(
        cohort,
        lambda _: httpx.Response(
            200, headers={"Content-Type": "image/jpeg"}, stream=Chunks([b"x" * 10, b"y" * 10])
        ),
    )
    assert len(result["attempts"]) == 1 and result["received_bytes"] == 20
    assert result["successful_images"] == 0 and result["unattempted"] == 7
    assert not list(cohort[2].iterdir())


def test_inventory_failure_never_leaves_image_or_photo_row(cohort, monkeypatch):
    original = pilot._write_json

    def fail_inventory(path, value, **kwargs):
        if path == cohort[1] and value.get("photo_reviews"):
            raise OSError("SYNTHETIC failure")
        return original(path, value, **kwargs)

    monkeypatch.setattr(pilot, "_write_json", fail_inventory)
    result = execute(
        cohort,
        lambda _: httpx.Response(
            200, headers={"Content-Type": "image/jpeg"}, stream=Chunks([jpeg()])
        ),
    )
    assert result["successful_images"] == 0
    assert not list(cohort[2].iterdir())
    with sqlite3.connect(cohort[0]) as db:
        assert db.execute("SELECT COUNT(*) FROM vision_photos").fetchone()[0] == 0


def test_requires_matching_authorization_and_runtime_readiness(cohort):
    db, inventory, photos, receipts = cohort
    preview = pilot.prepare_preview(db)
    args = dict(
        inventory_path=inventory,
        photo_root=photos,
        receipt_dir=receipts,
        transport=httpx.MockTransport(lambda _: pytest.fail("unauthorized call")),
    )
    with pytest.raises(pilot.ImagePilotError, match="policy"):
        asyncio.run(
            pilot.execute_preview(preview, approved_preview_sha256=preview["sha256"], **args)
        )
    with pytest.raises(pilot.ImagePilotError, match="approval"):
        asyncio.run(
            pilot.execute_preview(
                preview, approved_preview_sha256="incorrect", network_policy_ready=True, **args
            )
        )


def test_byte_write_failure_keeps_registered_cleanup_path(cohort, monkeypatch):
    def fail_write(*args, **kwargs):
        raise OSError("SYNTHETIC private disk failure")

    monkeypatch.setattr(pilot, "write_photo", fail_write)
    result = execute(
        cohort,
        lambda _: httpx.Response(
            200, headers={"Content-Type": "image/jpeg"}, stream=Chunks([jpeg()])
        ),
    )
    assert result["successful_images"] == 0
    assert not list(cohort[2].iterdir())
    registrations = json.loads(cohort[1].read_text())["photo_reviews"]
    assert len(registrations) == 8
    assert all(r["kind"] == "seller_copy" for r in registrations.values())
    with sqlite3.connect(cohort[0]) as db:
        assert db.execute("SELECT COUNT(*) FROM vision_photos").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM vision_cases").fetchone()[0] == 0


def test_preview_change_stops_before_any_dispatch(cohort):
    db, inventory, photos, receipts = cohort
    preview = pilot.prepare_preview(db)
    with sqlite3.connect(db) as c:
        c.execute("UPDATE discovered_items SET inbox_status='RESEARCH' WHERE id=1")
    with pytest.raises(pilot.ImagePilotError, match="changed"):
        asyncio.run(
            pilot.execute_preview(
                preview,
                approved_preview_sha256=preview["sha256"],
                inventory_path=inventory,
                photo_root=photos,
                receipt_dir=receipts,
                network_policy_ready=True,
                transport=httpx.MockTransport(lambda _: pytest.fail("Unexpected image dispatch")),
            )
        )
    assert not receipts.exists()


def test_symlink_photo_directory_rejected_before_dispatch(cohort):
    _, _, photos, _ = cohort
    target = photos.with_name("unsafe-target")
    target.mkdir(mode=0o700)
    photos.symlink_to(target, target_is_directory=True)
    with pytest.raises(pilot.ImagePilotError, match="symlink"):
        execute(cohort, lambda _: pytest.fail("Unexpected image dispatch"))


def test_content_length_is_checked_before_body(cohort):
    class NoBody(httpx.AsyncByteStream):
        async def __aiter__(self):
            pytest.fail("Oversized declared body must not be read")
            yield b""  # pragma: no cover

    result = execute(
        cohort,
        lambda _: httpx.Response(
            200,
            headers={"Content-Type": "image/jpeg", "Content-Length": "10000001"},
            stream=NoBody(),
        ),
    )
    assert result["successful_images"] == 0 and result["received_bytes"] == 0
    assert all(a["status"] == "CONTENT_LENGTH_REJECTED" for a in result["attempts"])


def test_source_change_stops_remaining_dispatches(cohort):
    requests = []

    def handler(request):
        requests.append(request)
        with sqlite3.connect(cohort[0]) as db:
            db.execute("UPDATE discovered_items SET inbox_status='PASSED' WHERE id=8")
        return httpx.Response(200, headers={"Content-Type": "image/jpeg"}, stream=Chunks([jpeg()]))

    result = execute(cohort, handler)
    assert len(requests) == 1 and result["successful_images"] == 1
    assert result["unattempted"] == 7 and result["source_records_unchanged"] is False
    assert result["stopped_reason"] == "SOURCE_RECORDS_CHANGED"
