"""Synthetic source-image bytes and records only; no remote request is permitted."""

import asyncio
import hashlib
import io
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from PIL import Image, PngImagePlugin
from sqlalchemy import select

from radar.db import (
    DiscoveredItem,
    DiscoveryObservation,
    DiscoveryRun,
    SearchExecution,
    VisionCase,
    VisionPhoto,
    VisionRun,
)
from radar.ebay_deletion.processing import tombstone
from radar.jewelry.config import VisionConfig
from radar.jewelry.images import (
    original_photo_metadata,
    photo_storage_metadata,
    private_path,
    read_photo,
    validate_original_image,
    write_photo,
)
from radar.jewelry.service import analyze
from radar.schemas import ListingInput


def pixels(format_, *, animated=False):
    image = Image.new("RGB", (80, 60), "turquoise")
    target = io.BytesIO()
    options = {}
    if format_ == "PNG":
        info = PngImagePlugin.PngInfo()
        info.add_text("Copyright", "SYNTHETIC preserved notice")
        options["pnginfo"] = info
    if format_ == "JPEG":
        exif = Image.Exif()
        exif[0x8298] = "SYNTHETIC preserved notice"
        options["exif"] = exif
    if animated:
        options.update(save_all=True, append_images=[Image.new("RGB", (80, 60), "red")])
    image.save(target, format_, **options)
    return target.getvalue()


@pytest.mark.parametrize(
    "format_,mime,suffix",
    [("JPEG", "image/jpeg", ".jpg"), ("PNG", "image/png", ".png"), ("WEBP", "image/webp", ".webp")],
)
def test_original_bytes_and_metadata_are_preserved_exactly(tmp_path, format_, mime, suffix):
    raw = pixels(format_)
    original = validate_original_image(raw, mime)
    metadata = original_photo_metadata(original)
    assert original.data == raw
    assert original.original_hash == original.content_hash == hashlib.sha256(raw).hexdigest()
    assert metadata["metadata_removed"] is False
    assert metadata["width"] == metadata["original_width"] == 80
    assert photo_storage_metadata(metadata, original.original_hash, original.content_hash) == (
        suffix,
        mime,
    )
    id_ = uuid4().hex
    write_photo(tmp_path, id_, raw, suffix=suffix)
    path = private_path(tmp_path, id_, suffix)
    assert path.read_bytes() == raw
    assert path.stat().st_mode & 0o777 == 0o600
    if format_ in {"PNG", "JPEG"}:
        assert b"SYNTHETIC preserved notice" in path.read_bytes()


@pytest.mark.parametrize(
    "mime", ["text/html", "image/svg+xml", "application/octet-stream", "image/png", None]
)
def test_original_content_type_must_match_decoded_format(mime):
    with pytest.raises(ValueError):
        validate_original_image(pixels("JPEG"), mime)


@pytest.mark.parametrize(
    "format_,mime", [("JPEG", "image/jpeg"), ("PNG", "image/png"), ("WEBP", "image/webp")]
)
def test_original_trailing_payload_is_rejected_not_sanitized(format_, mime):
    with pytest.raises(ValueError):
        validate_original_image(pixels(format_) + b"<script>SYNTHETIC</script>", mime)


@pytest.mark.parametrize("format_,mime", [("PNG", "image/png"), ("WEBP", "image/webp")])
def test_original_animation_is_rejected(format_, mime):
    with pytest.raises(ValueError):
        validate_original_image(pixels(format_, animated=True), mime)


@pytest.mark.parametrize(
    "change",
    [
        {"format": []},
        {"storage_suffix": ".txt"},
        {"media_type": "text/html"},
        {"metadata_removed": True},
        {"original_width": 81},
        {"width": True},
        {"processing_version": "unknown"},
        {"bytes": 10_000_001},
    ],
)
def test_original_storage_contract_is_closed(change):
    original = validate_original_image(pixels("JPEG"), "image/jpeg")
    metadata = original_photo_metadata(original) | change
    with pytest.raises(ValueError):
        photo_storage_metadata(metadata, original.original_hash, original.content_hash)


def test_original_write_rejects_symlinked_parent_and_overwrite(tmp_path):
    actual = tmp_path / "actual"
    actual.mkdir()
    linked = tmp_path / "linked"
    linked.symlink_to(actual, target_is_directory=True)
    id_ = uuid4().hex
    with pytest.raises(ValueError):
        write_photo(linked, id_, pixels("PNG"))
    write_photo(actual, id_, pixels("PNG"))
    with pytest.raises(FileExistsError):
        write_photo(actual, id_, pixels("JPEG"))
    assert (actual / (id_ + ".png")).read_bytes() == pixels("PNG")


def test_legacy_maker_photo_without_original_hash_remains_readable(tmp_path):
    raw = pixels("PNG")
    photo = SimpleNamespace(
        id=uuid4().hex, data={"bytes": len(raw)}, content_hash=hashlib.sha256(raw).hexdigest()
    )
    write_photo(tmp_path, photo.id, raw)
    assert read_photo(tmp_path, photo) == raw


def test_missing_original_hash_cannot_opt_into_exact_source_storage(tmp_path):
    original = validate_original_image(pixels("PNG"), "image/png")
    photo = SimpleNamespace(
        id=uuid4().hex, data=original_photo_metadata(original), content_hash=original.content_hash
    )
    write_photo(tmp_path, photo.id, original.data)
    with pytest.raises(ValueError, match="metadata"):
        read_photo(tmp_path, photo)


def source_photo(client, tmp_path, monkeypatch, *, format_="JPEG", observed_hours=1):
    root = tmp_path / "source-photos"
    root.mkdir(mode=0o700)
    monkeypatch.setenv("VISION_IMAGE_DIR", str(root))
    now = datetime.now(UTC)
    observed = now - timedelta(hours=observed_hours)
    source_id = "SYNTHETIC-source-original"
    url = "https://i.ebayimg.com/images/g/SYNTHETIC/s-l225.jpg"
    normalized = ListingInput(
        source="eBay",
        source_listing_id=source_id,
        raw_title="SYNTHETIC official source ring",
        listing_url="https://www.ebay.com/itm/12345",
        item_price="12.99",
        currency="USD",
        image_urls=[url],
        discovery_mode="production",
    )
    image = validate_original_image(
        pixels(format_), {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}[format_]
    )
    metadata = original_photo_metadata(image) | {
        "listing_observed_at": observed.isoformat(),
        "fetched_at": now.isoformat(),
        "source_listing_id": source_id,
        "source_url_sha256": hashlib.sha256(url.encode()).hexdigest(),
    }
    with client.app.state.sessions.begin() as s:
        run = DiscoveryRun(mode="production", options={})
        s.add(run)
        s.flush()
        execution = SearchExecution(
            run_id=run.id,
            source="eBay",
            mode="production",
            group_ids=[],
            original_query="SYNTHETIC ring",
            transmitted_query="SYNTHETIC ring",
            parameters={},
        )
        s.add(execution)
        s.flush()
        item = DiscoveredItem(
            identity=source_id,
            source="eBay",
            ebay_item_id=source_id,
            normalized=normalized.model_dump(mode="json"),
            original_data={"image": {"imageUrl": url}},
        )
        s.add(item)
        s.flush()
        s.add(
            DiscoveryObservation(
                execution_id=execution.id,
                item_id=item.id,
                raw_data={"price": {"value": "12.99", "currency": "USD"}},
                correction_state="UNKNOWN",
                observed_at=observed,
            )
        )
        case = VisionCase(candidate_id=item.id)
        s.add(case)
        s.flush()
        photo = VisionPhoto(
            id=uuid4().hex,
            case_id=case.id,
            role="FRONT",
            original_hash=image.original_hash,
            content_hash=image.content_hash,
            data=metadata,
        )
        s.add(photo)
        suffix, _ = photo_storage_metadata(metadata, photo.original_hash, photo.content_hash)
        write_photo(root, photo.id, image.data, suffix=suffix)
    return item.id, case.id, photo.id, root, image


@pytest.mark.parametrize("format_", ["JPEG", "PNG", "WEBP"])
def test_source_original_feed_and_full_viewer_return_original_bytes(
    client, tmp_path, monkeypatch, format_
):
    _, _, id_, _, original = source_photo(client, tmp_path, monkeypatch, format_=format_)
    feed = client.get("/feed")
    assert feed.status_code == 200 and len(feed.context["cards"]) == 1
    compact = feed.context["cards"][0]["compact"]
    assert compact["photo_frame"]["kind"] == "direct"
    assert compact["assessment"] == "NEEDS INFO"
    for url in (f"/feed/photos/{id_}", f"/vision/photos/{id_}"):
        response = client.get(url)
        assert response.status_code == 200
        assert response.headers["content-type"] == original.media_type
        assert response.content == original.data
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["cache-control"] == "private, no-store"


@pytest.mark.parametrize(
    "damage", ["hash", "symlink", "source_binding", "deleted", "expired", "timestamp_relabel"]
)
def test_source_original_reads_fail_closed(client, tmp_path, monkeypatch, damage):
    item_id, _, id_, root, original = source_photo(client, tmp_path, monkeypatch)
    path = root / (id_ + ".jpg")
    with client.app.state.sessions.begin() as s:
        photo = s.get(VisionPhoto, id_)
        if damage == "hash":
            path.write_bytes(original.data[:-2] + b"xx")
        elif damage == "symlink":
            other = tmp_path / "other.jpg"
            other.write_bytes(original.data)
            path.unlink()
            path.symlink_to(other)
        elif damage == "source_binding":
            photo.data = photo.data | {"source_url_sha256": "f" * 64}
        elif damage == "deleted":
            item = s.get(DiscoveredItem, item_id)
            item.normalized = tombstone(item.normalized)
        elif damage == "expired":
            photo.data = photo.data | {
                "fetched_at": (datetime.now(UTC) - timedelta(hours=25)).isoformat()
            }
        else:
            observation = s.scalar(
                select(DiscoveryObservation).where(DiscoveryObservation.item_id == item_id)
            )
            observation.observed_at = datetime.now(UTC) - timedelta(hours=7)
    assert client.get(f"/feed/photos/{id_}").status_code == 404
    assert client.get(f"/vision/photos/{id_}").status_code == 404


def test_stale_source_is_omitted_from_every_feed_scope_without_mutating_history(
    client, tmp_path, monkeypatch
):
    item_id, _, id_, _, _ = source_photo(client, tmp_path, monkeypatch, observed_hours=7)
    for scope in ("active", "needs_photos", "ended", "reviewed", "archive", "all"):
        response = client.get("/feed?scope=" + scope)
        assert response.status_code == 200 and response.context["cards"] == []
        assert response.context["stale_source_count"] == 1
        assert "SYNTHETIC official source ring" not in response.text
    with client.app.state.sessions() as s:
        assert (
            s.get(DiscoveredItem, item_id).normalized["raw_title"]
            == "SYNTHETIC official source ring"
        )
        assert s.get(VisionPhoto, id_) is not None


def test_source_originals_cannot_enter_inference(client, tmp_path, monkeypatch):
    _, case_id, photo_id, root, _ = source_photo(client, tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="display-only"):
        asyncio.run(
            analyze(
                client.app.state.sessions, root, case_id, [photo_id], VisionConfig(), initiate=True
            )
        )
    with client.app.state.sessions() as s:
        assert list(s.scalars(select(VisionRun))) == []
