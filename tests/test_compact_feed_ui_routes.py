"""Synthetic compact-row, native-frame and clear-decision presentation checks."""

import io
import re
from datetime import UTC, date, datetime

from PIL import Image
from sqlalchemy import select

from radar.db import Feedback, Listing, VisionCase
from radar.jewelry.images import clean_image
from radar.jewelry.service import add_photos, open_case
from radar.schemas import ListingInput, ResearchObservation


def listing(client, item, key, *, priced=True):
    source_item = ListingInput.model_validate(
        item.model_dump(mode="json")
        | {
            "source": "ShopGoodwill",
            "source_listing_id": key,
            "listing_url": f"https://shopgoodwill.com/item/{key}",
            "raw_title": f"SYNTHETIC {key} jewelry",
            "synthetic": True,
            "item_price": item.item_price if priced else None,
        }
    )
    with client.app.state.sessions.begin() as session:
        row = Listing(
            dedup_key=key,
            data=source_item.model_dump(mode="json"),
            original_data={"synthetic": key},
            review_state="RESEARCH",
        )
        session.add(row)
        session.flush()
        return row.id


def upload(client, root, id_):
    image = io.BytesIO()
    Image.new("RGB", (160, 120), "turquoise").save(image, "PNG")
    case_id = open_case(client.app.state.sessions, "listing", id_)
    add_photos(client.app.state.sessions, root, case_id, [(clean_image(image.getvalue()), "FRONT")])


def clear_button(page):
    match = re.search(r'<button[^>]*value="CLEAR"[^>]*>', page.text)
    assert match is not None
    return match.group(0)


def test_needs_photos_is_short_rows_with_optional_known_price_and_no_empty_cards(client, item):
    known = listing(client, item, "synthetic-known")
    unknown = listing(client, item, "synthetic-unpriced", priced=False)
    with client.app.state.sessions.begin() as session:
        row = session.get(Listing, known)
        report = ResearchObservation(
            observation_id="synthetic-dated-price",
            source="ShopGoodwill",
            source_listing_id="synthetic-known",
            source_url="https://shopgoodwill.com/item/synthetic-known",
            observed_date=date(2027, 1, 6),
            recorded_at=datetime(2027, 1, 7, tzinfo=UTC),
            currency="USD",
            item_quote="15",
            quote_kind="CURRENT_BID",
        )
        row.data = ListingInput.model_validate(
            row.data | {"research_observations": [report]}
        ).model_dump(mode="json")
    page = client.get("/feed?dataset=fixture&scope=needs_photos")
    assert page.status_code == 200
    assert {card["listing_id"] for card in page.context["cards"]} == {known, unknown}
    assert page.context["view_counts"]["all"] == 2
    assert page.text.count('<article class="compact-photo-row"') == 2
    assert '<article class="listing-card' not in page.text
    assert "compact-photo-missing" not in page.text
    assert "UNKNOWN" not in page.text
    assert "NEEDS INFO" not in page.text
    assert "15.00 USD" in page.text
    assert '<time datetime="2027-01-06">2027-01-06</time>' in page.text
    assert "ShopGoodwill · FIXTURE · NON-LIVE" in page.text
    assert f'href="/vision/for/listing/{known}"' in page.text
    assert f'href="/vision/for/listing/{unknown}"' in page.text
    assert f'href="/listings/{known}"' in page.text
    assert 'href="https://shopgoodwill.com/item/synthetic-known"' in page.text
    assert 'value="GO"' not in page.text
    assert "<svg" not in page.text and "<img" not in page.text
    with client.app.state.sessions() as session:
        assert list(session.scalars(select(Feedback))) == []
        assert list(session.scalars(select(VisionCase))) == []


def test_native_photo_frame_retains_original_viewer_and_neutral_clear_reload(
    client, item, tmp_path, monkeypatch
):
    root = tmp_path / "photos"
    root.mkdir()
    monkeypatch.setenv("VISION_IMAGE_DIR", str(root))
    id_ = listing(client, item, "synthetic-photo-clear")
    upload(client, root, id_)
    initial = client.get("/feed?dataset=fixture")
    assert initial.status_code == 200
    frame = initial.context["cards"][0]["compact"]["photo_frame"]
    assert frame["source_width"] == 160 and frame["source_height"] == 120
    assert 'viewBox="0 0 160 120"' in initial.text
    assert '<svg class="compact-photo-frame"' in initial.text
    assert 'width="160" height="120"' in initial.text
    photo_url = initial.context["cards"][0]["compact"]["photo_url"]
    assert f'href="{photo_url}" aria-label="Open original photo:' in initial.text
    assert " hidden" in clear_button(initial)
    go = client.post(
        f"/feed/review/listing/{id_}",
        data={"decision": "GO"},
        headers={"accept": "application/json"},
    )
    assert go.status_code == 200
    saved = client.get("/feed?dataset=fixture")
    assert " hidden" not in clear_button(saved)
    assert ">Clear decision</button>" in saved.text
    assert 'data-can-clear="true"' in saved.text
    clear = client.post(
        f"/feed/review/listing/{id_}",
        data={"decision": "CLEAR", "expected_feedback_id": go.json()["feedback_id"]},
        headers={"accept": "application/json"},
    )
    assert clear.status_code == 200 and clear.json()["state"] == "RESEARCH"
    assert clear.json()["action"] == "Cleared"
    neutral = client.get("/feed?dataset=fixture")
    assert " hidden" in clear_button(neutral)
    assert "GO ✓" not in neutral.text and "NO GO ✓" not in neutral.text
    assert 'data-can-clear="false"' in neutral.text
    assert neutral.context["cards"][0]["latest_feedback"]["action"] == "Cleared"
    with client.app.state.sessions() as session:
        assert [f.action for f in session.scalars(select(Feedback).order_by(Feedback.id))] == [
            "Interested",
            "Cleared",
        ]


def test_unpictured_passed_record_has_clear_action_in_full_research(client, item):
    id_ = listing(client, item, "synthetic-unpictured-clear")
    passed = client.post(
        f"/feed/review/listing/{id_}",
        data={"decision": "NO_GO"},
        headers={"accept": "application/json"},
    )
    assert passed.status_code == 200
    reviewed = client.get("/feed?dataset=fixture&scope=reviewed")
    assert '<article class="compact-photo-row"' in reviewed.text
    detail = client.get(f"/listings/{id_}")
    assert detail.status_code == 200
    assert f'action="/feed/review/listing/{id_}"' in detail.text
    assert 'name="decision" value="CLEAR"' in detail.text
    assert "Clear decision / Return to review" in detail.text
    cleared = client.post(
        f"/feed/review/listing/{id_}",
        data={
            "decision": "CLEAR",
            "expected_feedback_id": passed.json()["feedback_id"],
            "return_to": f"/listings/{id_}",
        },
        follow_redirects=False,
    )
    assert cleared.status_code == 303
    assert cleared.headers["location"] == f"/listings/{id_}"
    assert client.get("/feed?dataset=fixture&scope=reviewed").context["total"] == 0
    assert client.get("/feed?dataset=fixture&scope=needs_photos").context["total"] == 1


def test_source_title_entities_decode_once_for_display_without_rendering_markup(client, item):
    id_ = listing(client, item, "synthetic-entities")
    raw_title = "SYNTHETIC silver &amp; gold &lt;img src=x onerror=alert(1)&gt; &amp;amp;"
    with client.app.state.sessions.begin() as session:
        row = session.get(Listing, id_)
        row.data = row.data | {"raw_title": raw_title}
    page = client.get("/feed?dataset=fixture&scope=needs_photos")
    assert page.status_code == 200
    assert "SYNTHETIC silver &amp; gold &lt;img src=x onerror=alert(1)&gt; &amp;amp;" in page.text
    assert "<img src=x" not in page.text
    assert "silver &amp;amp; gold" not in page.text
    with client.app.state.sessions() as session:
        assert session.get(Listing, id_).data["raw_title"] == raw_title
