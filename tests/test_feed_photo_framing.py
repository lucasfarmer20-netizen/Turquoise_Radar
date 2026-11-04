"""Synthetic framing coordinates only; originals and owner databases are never changed."""

import io
import json
from copy import deepcopy
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from PIL import Image
from pydantic import ValidationError
from sqlalchemy import select

from radar.compact_feed import decorate_compact_cards, read_compact_photo
from radar.db import Feedback, Listing, ListingEdit, VisionCase, VisionPhoto, VisionRun, database
from radar.feed_photo_framing import (
    MANIFEST_NAME,
    MAX_MANIFEST_BYTES,
    PhotoFraming,
    framing_request,
    load_photo_framing,
    validated_photo_frame,
)
from radar.jewelry.images import clean_image, write_photo
from radar.schemas import ListingInput, ResearchAttachment, ResearchObservation
from radar.sourcing_feed import build_feed

NOW = datetime(2027, 3, 1, 18, tzinfo=UTC)


@pytest.fixture
def framed(tmp_path, item, monkeypatch):
    monkeypatch.setattr("radar.sourcing_feed.utcnow", lambda: NOW)
    root = tmp_path / "private" / "photos"
    root.mkdir(parents=True, mode=0o700)
    engine, sessions = database(f"sqlite:///{tmp_path / 'synthetic-framing.db'}")
    with sessions() as session:
        model = ListingInput.model_validate(
            item.model_dump(mode="json")
            | {
                "source": "ShopGoodwill",
                "source_listing_id": "930000001",
                "listing_url": "https://shopgoodwill.com/item/930000001",
                "raw_title": "SYNTHETIC full-item framing",
                "synthetic": True,
            }
        )
        row = Listing(
            dedup_key="synthetic-framing",
            data=model.model_dump(mode="json"),
            original_data={"synthetic": True},
            review_state="RESEARCH",
        )
        session.add(row)
        session.flush()
        case = VisionCase(listing_id=row.id, owner_id="local-owner")
        session.add(case)
        session.flush()
        stream = io.BytesIO()
        Image.new("RGB", (64, 48), "turquoise").save(stream, "PNG")
        clean = clean_image(stream.getvalue())
        photo = VisionPhoto(
            id="a" * 32,
            case_id=case.id,
            role="FRONT",
            original_hash=clean.original_hash,
            content_hash=clean.content_hash,
            data={"bytes": len(clean.data), "synthetic": True},
            active=True,
        )
        session.add(photo)
        write_photo(root, photo.id, clean.data)
        session.commit()
        entry = {
            "photo_id": photo.id,
            "source": "ShopGoodwill",
            "source_listing_id": "930000001",
            "original_sha256": photo.original_hash,
            "content_sha256": photo.content_hash,
            "source_width": 64,
            "source_height": 48,
            "classification": "full_item",
            "bbox": [8, 6, 32, 24],
            "review_note": "SYNTHETIC reviewed whole item; source overlay retained",
        }
        yield SimpleNamespace(
            session=session, root=root, row=row, case=case, photo=photo, raw=clean.data, entry=entry
        )
    engine.dispose()


def write_manifest(env, entries):
    path = env.root.parent / MANIFEST_NAME
    path.write_text(json.dumps({"schema": "feed-photo-framing-1", "frames": entries}))
    path.chmod(0o600)
    return path


def attach_screen(env):
    report = ResearchObservation(
        observation_id="synthetic-framing-report",
        source="ShopGoodwill",
        source_listing_id="930000001",
        source_url=env.row.data["listing_url"],
        recorded_at=NOW,
        observed_date=NOW.date(),
    )
    attachment = ResearchAttachment(
        artifact_id="b" * 32,
        source="ShopGoodwill",
        source_listing_id="930000001",
        source_url=env.row.data["listing_url"],
        observation_id=report.observation_id,
        received_at=NOW,
        original_sha256=env.photo.original_hash,
        bytes=len(env.raw),
        kind="SCREENSHOT",
        display_name="SYNTHETIC-source-screen.png",
        vision_photo_id=env.photo.id,
    )
    env.row.data = ListingInput.model_validate(
        env.row.data | {"research_observations": [report], "research_attachments": [attachment]}
    ).model_dump(mode="json")
    env.session.commit()


def projected(env, prefs):
    return decorate_compact_cards(
        env.session, build_feed(env.session, prefs), now=NOW, photo_root=env.root
    )[0]["compact"]


def test_missing_manifest_allows_only_unattached_front_at_native_size(framed, prefs):
    env = framed
    assert load_photo_framing(env.root) == {}
    result = projected(env, prefs)
    assert result["photo_id"] == env.photo.id
    assert result["photo_frame"] == {
        "kind": "direct",
        "source_width": 64,
        "source_height": 48,
        "x": 0,
        "y": 0,
        "width": 64,
        "height": 48,
        "max_width": 64,
        "max_height": 48,
        "review_note": None,
    }
    attach_screen(env)
    assert projected(env, prefs)["photo_id"] is None


@pytest.mark.parametrize("role", ["BACK_HALLMARK", "STONE", "SIDE_DETAIL"])
def test_detail_roles_have_no_automatic_full_item_thumbnail(framed, prefs, role):
    env = framed
    env.photo.role = role
    env.session.commit()
    assert projected(env, prefs)["photo_id"] is None
    with pytest.raises(ValueError):
        read_compact_photo(env.session, prefs, env.photo.id, photo_root=env.root)


def test_reviewed_source_crop_returns_same_bytes_and_preserves_records(framed, prefs):
    env = framed
    attach_screen(env)
    env.photo.role = "SIDE_DETAIL"
    env.session.commit()
    write_manifest(env, [env.entry])
    before = deepcopy(env.row.data)
    original = deepcopy(env.row.original_data)
    pixels = (env.root / (env.photo.id + ".png")).read_bytes()
    result = projected(env, prefs)
    assert result["photo_id"] == env.photo.id
    assert result["photo_url"] == f"/feed/photos/{env.photo.id}"
    frame = result["photo_frame"]
    assert frame["kind"] == "full_item"
    assert (frame["x"], frame["y"], frame["width"], frame["height"]) == (8, 6, 32, 24)
    assert (frame["max_width"], frame["max_height"]) == (32, 24)
    assert (frame["source_width"], frame["source_height"]) == (64, 48)
    assert "overlay retained" in frame["review_note"]
    assert read_compact_photo(env.session, prefs, env.photo.id, photo_root=env.root) == pixels
    assert (env.root / (env.photo.id + ".png")).read_bytes() == pixels
    assert env.row.data == before and env.row.original_data == original
    for model in (Feedback, ListingEdit, VisionRun):
        assert env.session.scalars(select(model)).all() == []


def test_explicit_exclusion_wins_over_automatic_front(framed, prefs):
    env = framed
    write_manifest(env, [env.entry | {"classification": "excluded", "bbox": None}])
    assert projected(env, prefs)["photo_id"] is None


@pytest.mark.parametrize(
    "change",
    [
        {"source": "HiBid"},
        {"source_listing_id": "930000099"},
        {"original_sha256": "c" * 64},
        {"content_sha256": "d" * 64},
        {"source_width": 65},
        {"source_height": 49},
    ],
)
def test_stale_or_wrong_source_binding_fails_closed_without_direct_fallback(framed, prefs, change):
    env = framed
    write_manifest(env, [env.entry | change])
    assert projected(env, prefs)["photo_id"] is None


@pytest.mark.parametrize(
    "change",
    [
        {"source": "eBay"},
        {"bbox": [-1, 0, 10, 10]},
        {"bbox": [0, 0, 0, 10]},
        {"bbox": [50, 0, 20, 10]},
        {"bbox": [0, 40, 10, 10]},
        {"bbox": [0.0, 0, 10, 10]},
        {"bbox": [False, 0, 10, 10]},
        {"bbox": ["0", 0, 10, 10]},
        {"bbox": None},
        {"classification": "detail"},
        {"source_width": 10001},
        {"photo_id": "../unsafe"},
    ],
)
def test_schema_rejects_unsupported_sources_and_invalid_pixel_bounds(framed, change):
    with pytest.raises(ValidationError):
        PhotoFraming.model_validate(
            {"schema": "feed-photo-framing-1", "frames": [framed.entry | change]}
        )


@pytest.mark.parametrize(
    "fault",
    ["malformed", "oversized", "duplicate_id", "duplicate_json_key", "symlink", "directory"],
)
def test_invalid_existing_manifest_disables_automatic_front(framed, prefs, tmp_path, fault):
    env = framed
    path = env.root.parent / MANIFEST_NAME
    if fault == "malformed":
        path.write_bytes(b"{SYNTHETIC-invalid-json")
    elif fault == "oversized":
        path.write_bytes(b" " * (MAX_MANIFEST_BYTES + 1))
    elif fault == "duplicate_id":
        write_manifest(env, [env.entry, env.entry])
    elif fault == "duplicate_json_key":
        path.write_text('{"schema":"feed-photo-framing-1","frames":[],"frames":[]}')
    elif fault == "symlink":
        target = tmp_path / "SYNTHETIC-manifest-target.json"
        target.write_text(json.dumps({"schema": "feed-photo-framing-1", "frames": []}))
        path.symlink_to(target)
    else:
        path.mkdir()
    assert load_photo_framing(env.root) is None
    assert projected(env, prefs)["photo_id"] is None


def test_manifest_parent_symlink_is_not_followed(framed, tmp_path):
    env = framed
    write_manifest(env, [env.entry])
    alias = tmp_path / "SYNTHETIC-private-alias"
    alias.symlink_to(env.root.parent, target_is_directory=True)
    assert load_photo_framing(alias / "photos") is None


def test_native_crop_limits_cannot_be_overridden_by_manifest_fields(framed):
    env = framed
    with pytest.raises(ValidationError):
        PhotoFraming.model_validate(
            {"schema": "feed-photo-framing-1", "frames": [env.entry | {"max_width": 1000}]}
        )
    write_manifest(env, [env.entry | {"bbox": [2, 2, 8, 6]}])
    manifest = load_photo_framing(env.root)
    request = framing_request(
        manifest, ListingInput.model_validate(env.row.data), env.photo, attached=True
    )
    frame = validated_photo_frame(request, env.raw)
    assert (frame["max_width"], frame["max_height"]) == (8, 6)


def test_unattached_front_is_preferred_over_reviewed_screen_crop(framed, prefs):
    env = framed
    attach_screen(env)
    write_manifest(env, [env.entry])
    stream = io.BytesIO()
    Image.new("RGB", (64, 48), "silver").save(stream, "PNG")
    clean = clean_image(stream.getvalue())
    direct = VisionPhoto(
        id="e" * 32,
        case_id=env.case.id,
        role="FRONT",
        original_hash=clean.original_hash,
        content_hash=clean.content_hash,
        data={"bytes": len(clean.data), "synthetic": True},
        active=True,
    )
    env.session.add(direct)
    write_photo(env.root, direct.id, clean.data)
    env.session.commit()
    result = projected(env, prefs)
    assert result["photo_id"] == direct.id
    assert result["photo_frame"]["kind"] == "direct"


def test_reuploading_a_source_document_as_front_does_not_bypass_review(framed, prefs):
    env = framed
    attach_screen(env)
    env.photo.role = "SIDE_DETAIL"
    duplicate = VisionPhoto(
        id="f" * 32,
        case_id=env.case.id,
        role="FRONT",
        original_hash=env.photo.original_hash,
        content_hash=env.photo.content_hash,
        data=deepcopy(env.photo.data),
        active=True,
    )
    env.session.add(duplicate)
    write_photo(env.root, duplicate.id, env.raw)
    env.session.commit()
    assert projected(env, prefs)["photo_id"] is None
    with pytest.raises(ValueError):
        read_compact_photo(env.session, prefs, duplicate.id, photo_root=env.root)


@pytest.mark.parametrize("fault", ["wrong_owner", "inactive", "corrupt", "deleted_target"])
def test_reviewed_crop_does_not_bypass_existing_photo_security(framed, prefs, fault):
    from radar.ebay_deletion.processing import tombstone

    env = framed
    attach_screen(env)
    write_manifest(env, [env.entry])
    if fault == "wrong_owner":
        env.case.owner_id = "SYNTHETIC-another-owner"
    elif fault == "inactive":
        env.photo.active = False
    elif fault == "corrupt":
        (env.root / (env.photo.id + ".png")).write_bytes(b"SYNTHETIC-corrupt")
    else:
        env.row.data = tombstone(env.row.data | {"source": "eBay"})
    env.session.commit()
    with pytest.raises(ValueError):
        read_compact_photo(env.session, prefs, env.photo.id, photo_root=env.root)
