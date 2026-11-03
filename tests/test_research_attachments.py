"""Synthetic private attachments only; no owner files, production DB, or network."""

import hashlib
import io
import json
import os
from copy import deepcopy
from datetime import UTC, date, datetime
from types import SimpleNamespace

import httpx
import httpx2
import pytest
from PIL import Image
from PIL.PngImagePlugin import PngInfo
from pydantic import ValidationError
from sqlalchemy import select

from radar.db import (
    AlertMessage,
    AlertObservation,
    ComparableSale,
    DiscoveredItem,
    DiscoveryRun,
    Feedback,
    Listing,
    ListingEdit,
    SearchExecution,
    VisionCase,
    VisionPhoto,
    VisionRun,
    database,
)
from radar.jewelry.images import clean_image, write_photo
from radar.research_evidence import (
    append_research_attachments,
    append_research_observation,
    read_research_asset,
)
from radar.schemas import ListingInput, ResearchAttachment, ResearchObservation
from radar.sources import dedup_key, import_rows

NOW = datetime(2027, 2, 3, 15, tzinfo=UTC)
EARLIER = datetime(2027, 2, 2, 10, tzinfo=UTC)
EMPTY_TABLES = (
    AlertMessage,
    AlertObservation,
    DiscoveredItem,
    SearchExecution,
    DiscoveryRun,
    Feedback,
    ComparableSale,
    VisionRun,
)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def reject(*args, **kwargs):
        raise AssertionError("Synthetic attachment tests authorize no network or inference")

    async def reject_async(*args, **kwargs):
        raise AssertionError("Synthetic attachment tests authorize no network or inference")

    for library in (httpx, httpx2):
        monkeypatch.setattr(library.HTTPTransport, "handle_request", reject)
        monkeypatch.setattr(library.AsyncHTTPTransport, "handle_async_request", reject_async)


def snapshot(session, models):
    session.flush()
    return {
        model.__tablename__: [
            {column.key: deepcopy(getattr(row, column.key)) for column in model.__table__.columns}
            for row in session.scalars(select(model).order_by(model.id))
        ]
        for model in models
    }


def synthetic_png():
    output = io.BytesIO()
    metadata = PngInfo()
    metadata.add_text("Comment", "SYNTHETIC original metadata, removed from processing copy")
    Image.new("RGB", (32, 32), "turquoise").save(output, "PNG", pnginfo=metadata)
    return output.getvalue()


@pytest.fixture
def evidence(tmp_path):
    engine, sessions = database(f"sqlite:///{tmp_path / 'synthetic-attachments.db'}")
    root = tmp_path / "original-assets"
    photo_root = tmp_path / "normalized-images"
    root.mkdir(mode=0o700)
    photo_root.mkdir(mode=0o700)
    with sessions() as session:
        listings = []
        for index, source in enumerate(("ShopGoodwill", "ShopGoodwill", "HiBid"), 1):
            item_id = str(910000000 + index)
            url = (
                f"https://shopgoodwill.com/item/{item_id}"
                if source == "ShopGoodwill"
                else f"https://hibid.com/lot/{item_id}/synthetic-attachment-lot"
            )
            item = ListingInput(
                source=source,
                source_listing_id=item_id,
                listing_url=url,
                raw_title=f"SYNTHETIC attachment research listing {index}",
                currency="XXX",
                synthetic=True,
                original_search_phrase="  SYNTHETIC old search phrase  ",
            )
            row = Listing(
                dedup_key=dedup_key(item),
                data=item.model_dump(mode="json"),
                original_data={"synthetic": True, "immutable_original": index},
                review_state="RESEARCH",
                first_seen=EARLIER,
                last_seen=EARLIER,
            )
            session.add(row)
            session.flush()
            observation = ResearchObservation(
                observation_id=f"synthetic-prior-observation-{index}",
                source=source,
                source_listing_id=item_id,
                source_url=url,
                recorded_at=EARLIER,
                observed_date=date(2027, 2, 1),
                note="SYNTHETIC prior report; no source artifact received at that time",
            )
            append_research_observation(session, row.id, observation)
            listings.append(row)
        target = listings[0]
        case = VisionCase(listing_id=target.id, owner_id="local-owner")
        session.add(case)
        session.flush()
        raw = synthetic_png()
        clean = clean_image(raw)
        photo = VisionPhoto(
            id="1" * 32,
            case_id=case.id,
            role="FRONT",
            original_hash=clean.original_hash,
            content_hash=clean.content_hash,
            data={"synthetic": True, "metadata_removed": True, "bytes": len(clean.data)},
            active=True,
        )
        session.add(photo)
        write_photo(photo_root, photo.id, clean.data)
        raw_path = root / ("a" * 32 + ".png")
        raw_path.write_bytes(raw)
        raw_path.chmod(0o600)
        terms_raw = b"# SYNTHETIC auction terms\nUnconfirmed fees; no real listing evidence.\n"
        terms_path = root / ("b" * 32 + ".txt")
        terms_path.write_bytes(terms_raw)
        terms_path.chmod(0o600)
        common = dict(
            source=target.data["source"],
            source_listing_id=target.data["source_listing_id"],
            source_url=target.data["listing_url"],
            observation_id="synthetic-prior-observation-1",
            received_at=NOW,
        )
        screenshot = ResearchAttachment(
            **common,
            artifact_id="a" * 32,
            original_sha256=hashlib.sha256(raw).hexdigest(),
            bytes=len(raw),
            kind="SCREENSHOT",
            display_name="SYNTHETIC-screenshot.png",
            vision_photo_id=photo.id,
        )
        terms = ResearchAttachment(
            **common,
            artifact_id="b" * 32,
            original_sha256=hashlib.sha256(terms_raw).hexdigest(),
            bytes=len(terms_raw),
            kind="AUCTION_TERMS",
            display_name="SYNTHETIC-terms.md",
        )
        session.commit()
        yield SimpleNamespace(
            session=session,
            root=root,
            photo_root=photo_root,
            listings=listings,
            target=target,
            case=case,
            photo=photo,
            screenshot=screenshot,
            terms=terms,
            raw=raw,
            raw_path=raw_path,
            terms_raw=terms_raw,
            terms_path=terms_path,
        )
    engine.dispose()


def append(env, attachments):
    return append_research_attachments(
        env.session,
        env.target.id,
        attachments,
        env.root,
        photo_root=env.photo_root,
    )


def test_batch_receipt_is_one_audit_and_preserves_all_prior_reports_and_economics(evidence):
    env = evidence
    session = env.session
    before = snapshot(session, (Listing, ListingEdit, VisionCase, VisionPhoto, *EMPTY_TABLES))
    assert append(env, [env.screenshot, env.terms]) == 2
    session.commit()
    after = snapshot(session, (Listing, ListingEdit, VisionCase, VisionPhoto, *EMPTY_TABLES))
    assert len(after["listing_edits"]) == len(before["listing_edits"]) + 1
    assert after["listing_edits"][:-1] == before["listing_edits"]
    for old, new in zip(before["listings"], after["listings"], strict=True):
        if old["id"] != env.target.id:
            assert old == new
            continue
        assert new["original_data"] == old["original_data"]
        assert new["review_state"] == old["review_state"]
        assert {
            key: value for key, value in new["data"].items() if key != "research_attachments"
        } == {key: value for key, value in old["data"].items() if key != "research_attachments"}
        assert len(new["data"]["research_attachments"]) == 2
        assert new["data"]["research_observations"][0]["artifacts_received"] is False
    for model in (VisionCase, VisionPhoto, *EMPTY_TABLES):
        assert before[model.__tablename__] == after[model.__tablename__]
    audit = after["listing_edits"][-1]
    assert audit["before_data"] == before["listings"][0]["data"]
    assert audit["after_data"] == env.target.data
    assert read_research_asset(env.root, env.screenshot) == env.raw
    assert read_research_asset(env.root, env.terms) == env.terms_raw
    assert env.photo.original_hash != env.photo.content_hash
    assert append(env, [env.screenshot, env.terms]) == 0
    session.commit()
    assert (
        snapshot(session, (Listing, ListingEdit, VisionCase, VisionPhoto, *EMPTY_TABLES)) == after
    )


def test_same_artifact_identity_cannot_rewrite_its_receipt(evidence):
    env = evidence
    assert append(env, [env.screenshot]) == 1
    env.session.commit()
    before = snapshot(env.session, (Listing, ListingEdit))
    changed = env.screenshot.model_copy(update={"display_name": "SYNTHETIC-replacement.png"})
    with pytest.raises(ValueError):
        append(env, [changed])
    env.session.commit()
    assert snapshot(env.session, (Listing, ListingEdit)) == before


@pytest.mark.parametrize(
    "changes",
    [
        {"source_listing_id": "910000099", "source_url": "https://shopgoodwill.com/item/910000099"},
        {"source_url": "https://shopgoodwill.com/item/910000099"},
        {
            "source": "HiBid",
            "source_listing_id": "910000099",
            "source_url": "https://hibid.com/lot/910000099/synthetic-other",
        },
        {"observation_id": "synthetic-nonexistent-observation"},
        {"source_url": "https://www.ebay.com/itm/910000001"},
    ],
)
def test_attachment_identity_must_bind_the_existing_marketplace_report(evidence, changes):
    env = evidence
    before = snapshot(env.session, (Listing, ListingEdit))
    with pytest.raises(ValueError):
        invalid = ResearchAttachment.model_validate(env.screenshot.model_dump() | changes)
        append(env, [invalid])
    env.session.commit()
    assert snapshot(env.session, (Listing, ListingEdit)) == before


@pytest.mark.parametrize(
    "changes",
    [
        {"artifact_id": "../escape"},
        {"display_name": "../SYNTHETIC.png"},
        {"display_name": "..\\SYNTHETIC.png"},
        {"received_at": datetime(2027, 2, 3, 15)},
        {"review_note": "SYNTHETIC review without timestamp"},
        {"reviewed_at": NOW},
        {"kind": "AUCTION_TERMS"},
        {"vision_photo_id": None},
    ],
)
def test_receipt_schema_rejects_unsafe_paths_uncertain_times_and_inconsistent_review(
    evidence, changes
):
    with pytest.raises(ValidationError):
        ResearchAttachment.model_validate(evidence.screenshot.model_dump() | changes)


@pytest.mark.parametrize("corruption", ["hash", "bytes", "file_bytes"])
def test_any_invalid_asset_rejects_the_whole_batch_before_an_audit(evidence, corruption):
    env = evidence
    before = snapshot(env.session, (Listing, ListingEdit))
    bad = env.terms
    if corruption == "hash":
        bad = bad.model_copy(update={"original_sha256": "f" * 64})
    elif corruption == "bytes":
        bad = bad.model_copy(update={"bytes": bad.bytes + 1})
    else:
        env.terms_path.write_bytes(b"X" * len(env.terms_raw))
    with pytest.raises(ValueError):
        append(env, [env.screenshot, bad])
    env.session.commit()
    assert snapshot(env.session, (Listing, ListingEdit)) == before


@pytest.mark.parametrize("kind", ["file", "root", "ancestor", "nonregular"])
def test_asset_reader_rejects_symlinks_and_nonregular_paths(evidence, tmp_path, kind):
    env = evidence
    root = env.root
    if kind == "file":
        outside = tmp_path / "SYNTHETIC-outside.png"
        outside.write_bytes(env.raw)
        env.raw_path.unlink()
        env.raw_path.symlink_to(outside)
    elif kind == "root":
        root = tmp_path / "SYNTHETIC-root-link"
        root.symlink_to(env.root, target_is_directory=True)
    elif kind == "ancestor":
        real_parent = tmp_path / "real-parent"
        real_parent.mkdir()
        moved = real_parent / "assets"
        env.root.rename(moved)
        linked_parent = tmp_path / "SYNTHETIC-parent-link"
        linked_parent.symlink_to(real_parent, target_is_directory=True)
        root = linked_parent / "assets"
    else:
        env.raw_path.unlink()
        os.mkfifo(env.raw_path)
    with pytest.raises(ValueError):
        read_research_asset(root, env.screenshot)


@pytest.mark.parametrize("raw", [b"SYNTHETIC\x00terms", b"SYNTHETIC \xff terms"])
def test_terms_require_plain_valid_utf8_without_nul(evidence, raw):
    env = evidence
    env.terms_path.write_bytes(raw)
    terms = env.terms.model_copy(
        update={"bytes": len(raw), "original_sha256": hashlib.sha256(raw).hexdigest()}
    )
    with pytest.raises(ValueError):
        read_research_asset(env.root, terms)


@pytest.mark.parametrize(
    "mismatch",
    [
        "listing",
        "owner",
        "original_hash",
        "content_hash",
        "inactive",
        "normalized_file",
        "different_normalized_pixels",
    ],
)
def test_screenshot_requires_its_active_owned_and_intact_normalized_photo(evidence, mismatch):
    env = evidence
    if mismatch == "listing":
        env.case.listing_id = env.listings[1].id
    elif mismatch == "owner":
        env.case.owner_id = "SYNTHETIC-other-owner"
    elif mismatch == "original_hash":
        env.photo.original_hash = "e" * 64
    elif mismatch == "content_hash":
        env.photo.content_hash = "d" * 64
    elif mismatch == "inactive":
        env.photo.active = False
    elif mismatch == "normalized_file":
        (env.photo_root / (env.photo.id + ".png")).write_bytes(
            b"SYNTHETIC invalid normalized image"
        )
    else:
        output = io.BytesIO()
        Image.new("RGB", (32, 32), "yellow").save(output, "PNG")
        different = clean_image(output.getvalue())
        (env.photo_root / (env.photo.id + ".png")).write_bytes(different.data)
        env.photo.content_hash = different.content_hash
    env.session.commit()
    before = snapshot(env.session, (Listing, ListingEdit))
    with pytest.raises(ValueError):
        append(env, [env.screenshot])
    env.session.commit()
    assert snapshot(env.session, (Listing, ListingEdit)) == before


@pytest.mark.parametrize("kind", ["root", "ancestor"])
def test_normalized_photo_root_must_not_follow_symlinks(evidence, tmp_path, kind):
    env = evidence
    if kind == "root":
        linked = tmp_path / "SYNTHETIC-image-root-link"
        linked.symlink_to(env.photo_root, target_is_directory=True)
        env.photo_root = linked
    else:
        parent = tmp_path / "real-image-parent"
        parent.mkdir()
        env.photo_root.rename(parent / "images")
        linked = tmp_path / "SYNTHETIC-image-parent-link"
        linked.symlink_to(parent, target_is_directory=True)
        env.photo_root = linked / "images"
    before = snapshot(env.session, (Listing, ListingEdit))
    with pytest.raises(ValueError):
        append(env, [env.screenshot])
    env.session.commit()
    assert snapshot(env.session, (Listing, ListingEdit)) == before


def test_duplicate_original_content_cannot_be_counted_as_another_attachment(evidence):
    env = evidence
    duplicate = env.terms.model_copy(update={"artifact_id": "c" * 32})
    (env.root / (duplicate.artifact_id + ".txt")).write_bytes(env.terms_raw)
    before = snapshot(env.session, (Listing, ListingEdit))
    with pytest.raises(ValueError):
        append(env, [env.terms, duplicate])
    env.session.commit()
    assert snapshot(env.session, (Listing, ListingEdit)) == before


@pytest.mark.parametrize("replacement", ["clear", "alter", "append"])
def test_generic_reimport_cannot_rewrite_attachment_receipts(evidence, replacement):
    env = evidence
    append(env, [env.screenshot])
    env.session.commit()
    before = snapshot(env.session, (Listing, ListingEdit))
    incoming = deepcopy(env.target.data)
    if replacement == "clear":
        incoming["research_attachments"] = []
    elif replacement == "alter":
        incoming["research_attachments"][0]["display_name"] = "SYNTHETIC-replaced.png"
    else:
        incoming["research_attachments"].append(env.terms.model_dump(mode="json"))
    with pytest.raises(ValueError):
        import_rows(env.session, json.dumps([incoming]), "json")
    env.session.commit()
    assert snapshot(env.session, (Listing, ListingEdit)) == before


@pytest.mark.parametrize("include_receipts", [False, True])
def test_ordinary_reimport_preserves_attachment_history(evidence, include_receipts):
    env = evidence
    append(env, [env.screenshot])
    env.session.commit()
    receipts = deepcopy(env.target.data["research_attachments"])
    audits = snapshot(env.session, (ListingEdit,))
    original = deepcopy(env.target.original_data)
    incoming = deepcopy(env.target.data)
    incoming["condition"] = "SYNTHETIC later observed condition"
    if not include_receipts:
        incoming.pop("research_attachments")
    assert import_rows(env.session, json.dumps([incoming]), "json") == {
        "created": 0,
        "updated": 1,
        "rows": 1,
    }
    env.session.commit()
    assert env.target.data["research_attachments"] == receipts
    assert env.target.original_data == original
    assert snapshot(env.session, (ListingEdit,)) == audits


def test_new_generic_import_cannot_claim_file_verified_attachments(evidence):
    env = evidence
    before = snapshot(env.session, (Listing, ListingEdit, VisionCase, VisionPhoto))
    incoming = deepcopy(env.target.data)
    incoming["source_listing_id"] = "910000099"
    incoming["listing_url"] = "https://shopgoodwill.com/item/910000099"
    incoming["research_attachments"] = [env.screenshot.model_dump(mode="json")]
    for entry in incoming["research_observations"] + incoming["research_attachments"]:
        entry["source_listing_id"] = incoming["source_listing_id"]
        entry["source_url"] = incoming["listing_url"]
    # Structural binding alone cannot replace private-file and photo ownership verification.
    ListingInput.model_validate(incoming)
    with pytest.raises(ValueError):
        import_rows(env.session, json.dumps([incoming]), "json")
    env.session.commit()
    assert snapshot(env.session, (Listing, ListingEdit, VisionCase, VisionPhoto)) == before


def test_unrelated_ebay_deletion_preserves_received_assets_and_attachment_audits(evidence):
    from radar.ebay_deletion.processing import sanitize_database
    from radar.ebay_deletion.protocol import digest

    env = evidence
    append(env, [env.screenshot])
    env.session.commit()
    append(env, [env.terms])
    env.session.commit()
    env.session.expire_all()
    protected = snapshot(env.session, (ListingEdit, VisionCase, VisionPhoto))
    data_before = deepcopy(env.target.data)
    original_before = deepcopy(env.target.original_data)
    item = ListingInput(
        source="eBay",
        raw_title="SYNTHETIC unrelated eBay target",
        source_listing_id="910000098",
        synthetic=True,
    )
    target = Listing(
        dedup_key=dedup_key(item),
        data=item.model_dump(mode="json"),
        original_data={"seller": {"userId": "SYNTHETIC-UNRELATED-DELETED-ACCOUNT"}},
    )
    env.session.add(target)
    env.session.commit()
    secret = b"SYNTHETIC-ATTACHMENT-DELETION-KEY"
    identifiers = {"userId": digest(secret, "userId", "SYNTHETIC-UNRELATED-DELETED-ACCOUNT")}
    assert sanitize_database(env.session.bind.url.database, identifiers, secret) == ("SANITIZED", 1)
    env.session.expire_all()
    assert env.target.data == data_before
    assert env.target.original_data == original_before
    assert snapshot(env.session, (ListingEdit, VisionCase, VisionPhoto)) == protected
    assert read_research_asset(env.root, env.screenshot) == env.raw
    assert read_research_asset(env.root, env.terms) == env.terms_raw


def test_ebay_cannot_embed_attachment_provenance_and_tombstone_never_retains_it(evidence):
    from radar.ebay_deletion.processing import SAFE_FIELDS, tombstone

    data = deepcopy(evidence.target.data)
    data.update(source="eBay", listing_url="https://www.ebay.com/itm/910000001")
    data["research_attachments"] = [evidence.screenshot.model_dump(mode="json")]
    with pytest.raises(ValidationError):
        ListingInput.model_validate(data)
    assert "research_attachments" not in SAFE_FIELDS
    removed = tombstone({"source": "eBay", "research_attachments": data["research_attachments"]})
    assert ListingInput.model_validate(removed).research_attachments == []
