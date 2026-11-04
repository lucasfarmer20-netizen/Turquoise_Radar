"""Synthetic private attachment UI and inert-download tests; no marketplace requests."""

import hashlib
import io
from datetime import UTC, date, datetime
from html.parser import HTMLParser

import pytest
from PIL import Image, PngImagePlugin
from sqlalchemy import select

from radar.db import Feedback, Listing, VisionPhoto
from radar.schemas import ListingInput, ResearchAttachment, ResearchObservation

RECORDED = datetime(2027, 1, 6, 12, tzinfo=UTC)
RECEIVED = datetime(2027, 1, 7, 12, tzinfo=UTC)
REVIEWED = datetime(2027, 1, 7, 13, tzinfo=UTC)
SOURCE_ID = "990000001"
SOURCE_URL = f"https://shopgoodwill.com/item/{SOURCE_ID}"


class Images(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.sources = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        if tag == "img":
            self.sources.append(dict(attrs).get("src"))


@pytest.fixture
def attachment_workspace(client, tmp_path, monkeypatch):
    originals = tmp_path / "private-originals"
    originals.mkdir(mode=0o700)
    photos = tmp_path / "private-normalized"
    monkeypatch.setenv("JRI_RESEARCH_EVIDENCE_DIR", str(originals))
    monkeypatch.setenv("VISION_IMAGE_DIR", str(photos))
    report = ResearchObservation(
        observation_id="synthetic-original-report",
        source="ShopGoodwill",
        source_listing_id=SOURCE_ID,
        source_url=SOURCE_URL,
        recorded_at=RECORDED,
        observed_date=date(2027, 1, 6),
    )
    item = ListingInput(
        source="ShopGoodwill", source_listing_id=SOURCE_ID, listing_url=SOURCE_URL,
        raw_title="SYNTHETIC later attachment", synthetic=True,
        research_observations=[report],
    )
    with client.app.state.sessions.begin() as session:
        row = Listing(
            dedup_key="synthetic-later-files", data=item.model_dump(mode="json"),
            original_data={"synthetic_original": "preserved"}, review_state="RESEARCH",
        )
        session.add(row)
        session.flush()
        listing_id = row.id
    return listing_id, originals, photos, report.model_dump(mode="json")


def attach(client, listing_id, originals, raw, *, screenshot=False, photo_id=None):
    artifact_id = ("b" if screenshot else "a") * 32
    attachment = ResearchAttachment(
        artifact_id=artifact_id, source="ShopGoodwill", source_listing_id=SOURCE_ID,
        source_url=SOURCE_URL, observation_id="synthetic-original-report",
        received_at=RECEIVED, reviewed_at=REVIEWED,
        original_sha256=hashlib.sha256(raw).hexdigest(), bytes=len(raw),
        kind="SCREENSHOT" if screenshot else "AUCTION_TERMS",
        display_name="SYNTHETIC screenshot.png" if screenshot else "SYNTHETIC terms.md",
        vision_photo_id=photo_id,
        review_note="SYNTHETIC review note <img src=x onerror=alert(1)>",
    )
    suffix = ".png" if screenshot else ".txt"
    path = originals / (artifact_id + suffix)
    path.write_bytes(raw)
    with client.app.state.sessions.begin() as session:
        row = session.get(Listing, listing_id)
        row.data = ListingInput.model_validate(
            row.data | {"research_attachments": [attachment]}
        ).model_dump(mode="json")
    return attachment, path


def test_terms_download_is_verified_plaintext_and_report_history_is_preserved(
    client, attachment_workspace
):
    listing_id, originals, _, report = attachment_workspace
    raw = b'# SYNTHETIC terms\n<script>alert(1)</script>\n![image](https://invalid.test/pixel)\n'
    attachment, _ = attach(client, listing_id, originals, raw)
    response = client.get(
        f"/listings/{listing_id}/research-attachments/{attachment.artifact_id}"
    )
    assert response.status_code == 200
    assert response.content == raw
    assert response.headers["content-type"] == "text/plain; charset=utf-8"
    assert response.headers["content-disposition"].startswith("attachment;")
    assert "no-store" in response.headers["cache-control"]
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["content-security-policy"] == "default-src 'none'; sandbox"
    feed = client.get("/feed?dataset=fixture")
    detail = client.get(f"/listings/{listing_id}")
    for page in (feed, detail):
        assert page.status_code == 200
        assert "1 supporting file received later" in page.text
        assert "2027-01-07 12:00:00 UTC" in page.text
        assert "2027-01-07 03:00:00 AKST Alaska" in page.text
        assert "2027-01-07 04:00:00 AKST Alaska" in page.text
        assert "Original report wording retained below" in page.text
        assert "2027-01-07 13:00:00 UTC" in page.text
        assert "&lt;img src=x onerror=alert(1)&gt;" in page.text
        assert "<script>alert(1)</script>" not in page.text
        assert not Images(page.text).sources
    card = feed.context["cards"][0]
    assert card["observation_count"] == 0
    assert card["research_observation_count"] == 1
    assert card["research_attachment_count"] == 1
    with client.app.state.sessions() as session:
        row = session.get(Listing, listing_id)
        assert row.data["research_observations"] == [report]
        assert row.original_data == {"synthetic_original": "preserved"}
        assert list(session.scalars(select(Feedback))) == []


@pytest.mark.parametrize("failure", ["absent", "corrupt", "symlink", "wrong_listing"])
def test_unavailable_or_wrong_listing_terms_fail_closed(client, attachment_workspace, failure):
    listing_id, originals, _, _ = attachment_workspace
    raw = b"SYNTHETIC private terms."
    attachment, path = attach(client, listing_id, originals, raw)
    if failure == "absent":
        path.unlink()
    elif failure == "corrupt":
        path.write_bytes(b"x" * len(raw))
    elif failure == "symlink":
        private = originals.parent / "SYNTHETIC-outside.txt"
        private.write_bytes(raw)
        path.unlink()
        path.symlink_to(private)
    else:
        with client.app.state.sessions.begin() as session:
            other = Listing(
                dedup_key="synthetic-unrelated", original_data={},
                data=ListingInput(source="manual", raw_title="SYNTHETIC unrelated").model_dump(
                    mode="json"
                ),
            )
            session.add(other)
            session.flush()
            listing_id = other.id
    response = client.get(
        f"/listings/{listing_id}/research-attachments/{attachment.artifact_id}"
    )
    assert response.status_code == 404
    assert "no-store" in response.headers["cache-control"]
    assert response.headers["x-content-type-options"] == "nosniff"
    assert raw not in response.content
    assert str(originals) not in response.text


def test_screenshot_uses_only_private_normalized_photo_path(client, attachment_workspace):
    listing_id, originals, _, _ = attachment_workspace
    metadata = PngImagePlugin.PngInfo()
    metadata.add_text("SYNTHETIC_PRIVATE_METADATA", "not served in normalized preview")
    image = io.BytesIO()
    Image.new("RGB", (100, 80), "turquoise").save(image, "PNG", pnginfo=metadata)
    raw = image.getvalue()
    case = client.get(f"/vision/for/listing/{listing_id}")
    assert case.status_code == 200
    case_id = int(case.url.path.rsplit("/", 1)[1])
    upload = client.post(
        f"/vision/cases/{case_id}/photos",
        data={"roles": "FRONT"}, files={"files": ("SYNTHETIC.png", raw, "image/png")},
    )
    assert upload.status_code == 200
    with client.app.state.sessions() as session:
        photo = session.scalar(select(VisionPhoto).where(VisionPhoto.case_id == case_id))
        photo_id = photo.id
    attachment, _ = attach(
        client, listing_id, originals, raw, screenshot=True, photo_id=photo_id
    )
    feed = client.get("/feed?dataset=fixture")
    assert Images(feed.text).sources == [f"/vision/photos/{photo_id}"]
    normalized = client.get(f"/vision/photos/{photo_id}")
    assert normalized.status_code == 200
    assert normalized.headers["content-type"] == "image/png"
    assert "no-store" in normalized.headers["cache-control"]
    assert normalized.headers["x-content-type-options"] == "nosniff"
    assert b"SYNTHETIC_PRIVATE_METADATA" not in normalized.content
    original = client.get(
        f"/listings/{listing_id}/research-attachments/{attachment.artifact_id}"
    )
    assert original.status_code == 404
    assert str(originals) not in feed.text
