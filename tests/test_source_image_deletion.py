"""Exact-source byte deletion uses synthetic accounts, temporary DBs and generated pixels."""

import io
import json
import sqlite3

import pytest
from PIL import Image

from radar.db import DiscoveredItem, VisionCase, VisionPhoto, database
from radar.ebay_deletion.processing import sanitize_database
from radar.ebay_deletion.protocol import digest
from radar.jewelry.images import original_photo_metadata, validate_original_image
from radar.schemas import ListingInput

SECRET = b"SYNTHETIC-exact-image-deletion-key-0000000"
ACCOUNT = "SYNTHETIC-IMAGE-ACCOUNT"
IDENTIFIERS = {"userId": digest(SECRET, "userId", ACCOUNT)}
PHOTO_ID = "f" * 32


def source_database(tmp_path, format_="JPEG"):
    path = tmp_path / "synthetic.db"
    output = io.BytesIO()
    Image.new("RGB", (40, 30), "turquoise").save(output, format=format_)
    mime = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}[format_]
    image = validate_original_image(output.getvalue(), mime)
    metadata = original_photo_metadata(image)
    # Deliberately stale: freshness cannot exempt source bytes from deletion.
    metadata.update(
        listing_observed_at="2020-01-01T00:00:00Z",
        fetched_at="2020-01-01T00:00:01Z",
    )
    photo = tmp_path / (PHOTO_ID + metadata["storage_suffix"])
    photo.write_bytes(image.data)
    engine, sessions = database("sqlite:///" + str(path))
    with sessions.begin() as session:
        item = ListingInput(
            source="eBay",
            source_listing_id="SYNTHETIC-ITEM",
            raw_title="SYNTHETIC jewelry",
            discovery_mode="production",
        )
        candidate = DiscoveredItem(
            identity="SYNTHETIC-ITEM",
            source="eBay",
            ebay_item_id="SYNTHETIC-ITEM",
            normalized=item.model_dump(mode="json"),
            original_data={"seller": {"userId": ACCOUNT}},
        )
        session.add(candidate)
        session.flush()
        case = VisionCase(candidate_id=candidate.id)
        session.add(case)
        session.flush()
        session.add(
            VisionPhoto(
                id=PHOTO_ID,
                case_id=case.id,
                role="FRONT",
                original_hash=image.original_hash,
                content_hash=image.content_hash,
                data=metadata,
            )
        )
    engine.dispose()
    reviews = {
        "vision_photos:" + PHOTO_ID: {
            "kind": "seller_copy",
            "evidence": "SYNTHETIC exact source photo review",
            "path": str(photo),
        }
    }
    return path, photo, reviews


def source_still_present(path):
    with sqlite3.connect(path) as db:
        return (
            db.execute("SELECT COUNT(*) FROM vision_photos").fetchone()[0] == 1
            and ACCOUNT in db.execute("SELECT original_data FROM discovered_items").fetchone()[0]
        )


@pytest.mark.parametrize("format_", ["JPEG", "PNG", "WEBP"])
def test_exact_source_bytes_are_removed_even_when_display_expired(tmp_path, format_):
    path, photo, reviews = source_database(tmp_path, format_)
    assert sanitize_database(path, IDENTIFIERS, SECRET, photo_reviews=reviews) == ("SANITIZED", 1)
    assert not photo.exists()
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT COUNT(*) FROM vision_photos").fetchone()[0] == 0
        assert db.execute("SELECT original_data FROM discovered_items").fetchone()[0] == "{}"
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert not db.execute("PRAGMA foreign_key_check").fetchall()


def test_missing_already_removed_exact_source_file_is_recoverable(tmp_path):
    path, photo, reviews = source_database(tmp_path)
    photo.unlink()
    assert sanitize_database(path, IDENTIFIERS, SECRET, photo_reviews=reviews) == ("SANITIZED", 1)


@pytest.mark.parametrize(
    "defect",
    ["missing_review", "independent", "wrong_suffix", "hash", "metadata", "parent_symlink"],
)
def test_exact_source_review_failures_preserve_database_and_file(tmp_path, defect):
    path, photo, reviews = source_database(tmp_path)
    review = reviews["vision_photos:" + PHOTO_ID]
    if defect == "missing_review":
        reviews = {}
    elif defect == "independent":
        review["kind"] = "independent"
    elif defect == "wrong_suffix":
        review["path"] = str(photo.with_suffix(".png"))
    elif defect == "hash":
        photo.write_bytes(b"SYNTHETIC changed bytes")
    elif defect == "metadata":
        with sqlite3.connect(path) as db:
            data = json.loads(db.execute("SELECT data FROM vision_photos").fetchone()[0])
            data["storage_suffix"] = ".png"
            db.execute("UPDATE vision_photos SET data=?", (json.dumps(data),))
    else:
        alias = tmp_path / "alias"
        alias.symlink_to(tmp_path, target_is_directory=True)
        review["path"] = str(alias / photo.name)
    before = photo.read_bytes()
    assert sanitize_database(path, IDENTIFIERS, SECRET, photo_reviews=reviews) == (
        "REVIEW_ARTIFACTS",
        0,
    )
    assert source_still_present(path)
    assert photo.read_bytes() == before
