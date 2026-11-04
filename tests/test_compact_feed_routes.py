"""Compact local review uses synthetic records, synthetic photos and no source requests."""

import io
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
from PIL import Image
from sqlalchemy import select

from radar.db import DiscoveredItem, Feedback, Listing, VisionPhoto
from radar.ebay_deletion.processing import tombstone
from radar.jewelry.images import clean_image
from radar.jewelry.service import add_photos, open_case
from radar.research_scope import mutation_allowed


def save_listing(client, item, key, **changes):
    item = item.model_copy(update={"synthetic": True, "raw_title": f"SYNTHETIC {key}", **changes})
    with client.app.state.sessions.begin() as session:
        row = Listing(
            dedup_key=key,
            data=item.model_dump(mode="json"),
            original_data={"synthetic": key},
            review_state="RESEARCH",
        )
        session.add(row)
        session.flush()
        return row.id


def photo(client, root, listing_id):
    image = io.BytesIO()
    Image.new("RGB", (200, 160), "turquoise").save(image, "PNG")
    case_id = open_case(client.app.state.sessions, "listing", listing_id)
    add_photos(client.app.state.sessions, root, case_id, [(clean_image(image.getvalue()), "FRONT")])
    with client.app.state.sessions() as session:
        return session.scalar(select(VisionPhoto.id).where(VisionPhoto.case_id == case_id))


def decision(client, kind, id_, value, expected=None, **kwargs):
    return client.post(
        f"/feed/review/{kind}/{id_}",
        data={"decision": value, "expected_feedback_id": expected or ""},
        headers={"accept": "application/json", **kwargs},
    )


def test_photo_first_tabs_preserve_records_and_hide_verbose_research(
    client, item, tmp_path, monkeypatch
):
    root = tmp_path / "photos"
    root.mkdir()
    monkeypatch.setenv("VISION_IMAGE_DIR", str(root))
    first = save_listing(client, item, "open-photo-one")
    second = save_listing(client, item, "open-photo-two")
    missing = save_listing(client, item, "missing-photo")
    ended = save_listing(
        client, item, "ended-photo", item_end_time=datetime.now(UTC) - timedelta(hours=1)
    )
    photo(client, root, first)
    photo(client, root, second)
    photo(client, root, ended)
    page = client.get("/feed?dataset=fixture")
    assert page.status_code == 200
    assert {c["listing_id"] for c in page.context["cards"]} == {first, second}
    assert page.context["view_counts"]["active"] == 2
    assert page.context["view_counts"]["needs_photos"] == 1
    assert page.context["view_counts"]["ended"] == 1
    assert "FIXTURE · NON-LIVE" in page.text
    assert '<script src="/static/compact-feed.js" defer>' in page.text
    assert "<table" not in page.text and "<dl" not in page.text
    assert "Known quoted subtotal" not in page.text
    assert "Costs and query provenance" not in page.text
    for card in page.context["cards"]:
        assert card["compact"]["photo_url"].startswith("/feed/photos/")
        assert card["compact"]["assessment"] in {
            "NEEDS INFO",
            "POTENTIAL DEAL",
            "FAIR PRICE",
            "TOO EXPENSIVE",
        }
    needs = client.get("/feed?dataset=fixture&scope=needs_photos")
    assert [c["listing_id"] for c in needs.context["cards"]] == [missing]
    archive = client.get("/feed?dataset=fixture&scope=ended")
    assert [c["listing_id"] for c in archive.context["cards"]] == [ended]
    assert ">ENDED</p>" in archive.text
    with client.app.state.sessions() as session:
        assert len(list(session.scalars(select(Listing)))) == 4
        assert list(session.scalars(select(Feedback))) == []


def test_go_no_go_are_immediate_idempotent_and_reversible_without_erasing_history(client, item):
    id_ = save_listing(client, item, "decision")
    go = decision(client, "listing", id_, "GO")
    assert go.status_code == 200
    receipt = go.json()
    assert receipt["action"] == "Interested" and receipt["state"] == "WATCH"
    repeated = decision(client, "listing", id_, "GO")
    assert repeated.json()["feedback_id"] == receipt["feedback_id"]
    stale = decision(client, "listing", id_, "NO_GO")
    assert stale.status_code == 409
    no_go = decision(client, "listing", id_, "NO_GO", receipt["feedback_id"])
    assert no_go.status_code == 200
    assert no_go.json()["state"] == "PASSED"
    viewed = client.get("/feed?dataset=fixture&scope=reviewed")
    assert viewed.context["cards"][0]["latest_feedback"]["action"] == "Passed"
    assert f'href="/listings/{id_}"' in viewed.text
    assert '<article class="compact-photo-row"' in viewed.text
    undo = decision(client, "listing", id_, "GO", no_go.json()["feedback_id"])
    assert undo.status_code == 200
    assert undo.json()["state"] == "WATCH"
    with client.app.state.sessions() as session:
        row = session.get(Listing, id_)
        assert row.original_data == {"synthetic": "decision"}
        assert row.review_state == "WATCH"
        assert [f.action for f in session.scalars(select(Feedback).order_by(Feedback.id))] == [
            "Interested",
            "Passed",
            "Interested",
        ]


def test_candidate_double_click_promotes_once_and_preserves_original_record(client, item):
    normalized = item.model_copy(update={"synthetic": True}).model_dump(mode="json")
    original = {"synthetic_private_original": "unchanged"}
    with client.app.state.sessions.begin() as session:
        row = DiscoveredItem(
            identity="synthetic-promotion",
            source=item.source,
            ebay_item_id="synthetic-id",
            normalized=normalized,
            original_data=original,
        )
        session.add(row)
        session.flush()
        id_ = row.id
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: decision(client, "candidate", id_, "GO"), range(2)))
    assert all(r.status_code == 200 for r in results)
    assert len({r.json()["feedback_id"] for r in results}) == 1
    with client.app.state.sessions() as session:
        candidate = session.get(DiscoveredItem, id_)
        listing = session.get(Listing, candidate.listing_id)
        assert candidate.normalized == normalized
        assert candidate.original_data == original
        assert listing.original_data == original
        assert len(list(session.scalars(select(Listing)))) == 1
        assert len(list(session.scalars(select(Feedback)))) == 1


def test_deleted_targets_and_purchased_records_cannot_be_reopened(client, item):
    removed = save_listing(client, item, "removed", source="eBay")
    purchased = save_listing(client, item, "purchased")
    with client.app.state.sessions.begin() as session:
        session.get(Listing, removed).data = tombstone({})
        session.get(Listing, purchased).review_state = "PURCHASED"
    assert decision(client, "listing", removed, "GO").status_code == 404
    assert decision(client, "listing", purchased, "GO").status_code == 409
    with client.app.state.sessions() as session:
        assert list(session.scalars(select(Feedback))) == []
        assert session.get(Listing, purchased).review_state == "PURCHASED"


def test_photo_route_rejects_removed_photo_and_deleted_target(client, item, tmp_path, monkeypatch):
    root = tmp_path / "photos"
    root.mkdir()
    monkeypatch.setenv("VISION_IMAGE_DIR", str(root))
    id_ = save_listing(client, item, "private-photo", source="eBay")
    photo_id = photo(client, root, id_)
    response = client.get(f"/feed/photos/{photo_id}")
    assert response.status_code == 200 and response.headers["content-type"] == "image/png"
    assert "no-store" in response.headers["cache-control"]
    with client.app.state.sessions.begin() as session:
        session.get(VisionPhoto, photo_id).active = False
    assert client.get(f"/feed/photos/{photo_id}").status_code == 404
    with client.app.state.sessions.begin() as session:
        session.get(VisionPhoto, photo_id).active = True
        session.get(Listing, id_).data = tombstone({})
    assert client.get(f"/feed/photos/{photo_id}").status_code == 404


@pytest.mark.parametrize(
    "headers", [{"origin": "https://invalid.test"}, {"sec-fetch-site": "cross-site"}]
)
def test_cross_origin_quick_feedback_is_rejected(client, item, headers):
    id_ = save_listing(client, item, "cross-origin")
    assert decision(client, "listing", id_, "GO", **headers).status_code == 403
    with client.app.state.sessions() as session:
        assert list(session.scalars(select(Feedback))) == []


def test_new_only_scope_allows_only_exact_review_paths_and_no_open_redirect(client, item):
    assert mutation_allowed("/feed/review/listing/1")
    assert mutation_allowed("/feed/review/candidate/2")
    assert not mutation_allowed("/feed/review/ebay/1")
    assert not mutation_allowed("/feed/run")
    id_ = save_listing(client, item, "html-fallback")
    response = client.post(
        f"/feed/review/listing/{id_}",
        data={"decision": "GO", "return_to": "https://invalid.test/"},
        follow_redirects=False,
    )
    assert response.status_code == 303 and response.headers["location"] == "/feed"
