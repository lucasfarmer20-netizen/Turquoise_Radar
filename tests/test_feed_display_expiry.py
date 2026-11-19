"""Synthetic read-only expiry metadata; no live marketplace records or requests."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from test_original_source_images import source_photo

from radar.db import DiscoveryObservation, VisionPhoto
from radar.discovery.image_freshness import display_expires_at

NOW = datetime(2026, 1, 2, 12, tzinfo=UTC)


def test_browser_deadline_preserves_independent_listing_and_image_limits():
    assert display_expires_at(NOW) == "2026-01-02T18:00:00+00:00"
    assert (
        display_expires_at(
            NOW,
            photo_data={"listing_observed_at": NOW, "fetched_at": NOW - timedelta(hours=23)},
        )
        == "2026-01-02T13:00:00+00:00"
    )
    assert (
        display_expires_at(
            NOW,
            photo_data={"listing_observed_at": NOW - timedelta(hours=5), "fetched_at": NOW},
        )
        == "2026-01-02T13:00:00+00:00"
    )


@pytest.mark.parametrize("stamp", [None, "", "invalid", NOW.replace(tzinfo=None)])
def test_missing_or_unqualified_deadline_is_not_invented(stamp):
    assert display_expires_at(stamp) is None
    assert (
        display_expires_at(NOW, photo_data={"listing_observed_at": NOW, "fetched_at": stamp})
        is None
    )


@pytest.mark.parametrize("with_photo", [True, False])
def test_feed_emits_expiry_on_cards_and_needs_photo_rows(client, tmp_path, monkeypatch, with_photo):
    _, _, photo_id, _, _ = source_photo(client, tmp_path, monkeypatch)
    with client.app.state.sessions.begin() as session:
        if not with_photo:
            session.get(VisionPhoto, photo_id).active = False
        observed = session.scalar(select(DiscoveryObservation)).observed_at.replace(tzinfo=UTC)
    response = client.get("/feed?scope=all")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "private, no-store"
    card = response.context["cards"][0]["compact"]
    assert card["source_expiry_required"] is True
    assert datetime.fromisoformat(card["source_expires_at"]) == observed + timedelta(hours=6)
    assert " hidden data-source-expiry " in response.text
    assert "data-source-checked-at=" in response.text
    assert ('class="compact-photo-row"' in response.text) is not with_photo
