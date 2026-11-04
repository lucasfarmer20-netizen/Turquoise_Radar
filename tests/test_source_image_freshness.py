"""Synthetic timestamps only; no requests or private source records."""

from datetime import UTC, datetime, timedelta

import pytest

from radar.discovery.image_freshness import (
    image_is_fresh,
    listing_is_fresh,
    photo_display_is_fresh,
    source_image_is_fresh,
)

NOW = datetime(2026, 1, 2, 12, tzinfo=UTC)


def test_fresh_image_does_not_revive_stale_listing():
    assert image_is_fresh(NOW, now=NOW)
    assert not photo_display_is_fresh(NOW - timedelta(hours=7), NOW, now=NOW)
    assert not source_image_is_fresh(
        {
            "listing_observed_at": (NOW - timedelta(hours=7)).isoformat(),
            "fetched_at": NOW.isoformat(),
        },
        now=NOW,
    )


def test_freshness_boundaries_and_original_timestamps():
    listing = NOW - timedelta(hours=6)
    image = NOW - timedelta(hours=24)
    assert listing_is_fresh(listing, now=NOW)
    assert image_is_fresh(image, now=NOW)
    assert photo_display_is_fresh(listing, image, now=NOW)
    assert not listing_is_fresh(listing - timedelta(microseconds=1), now=NOW)
    assert not image_is_fresh(image - timedelta(microseconds=1), now=NOW)
    assert source_image_is_fresh(
        {"listing_observed_at": "2026-01-02T03:00:00-09:00", "fetched_at": "2026-01-02T12:00:00Z"},
        now=NOW,
    )


@pytest.mark.parametrize(
    "value",
    [
        None,
        "",
        "2026-01-02",
        "broken",
        123,
        [],
        NOW.replace(tzinfo=None),
        NOW + timedelta(seconds=1),
    ],
)
def test_unqualified_or_future_timestamps_fail_closed(value):
    assert not listing_is_fresh(value, now=NOW)
    assert not image_is_fresh(value, now=NOW)


def test_missing_source_times_and_invalid_clocks_fail_closed():
    assert not source_image_is_fresh({"created_at": NOW.isoformat()}, now=NOW)
    assert not source_image_is_fresh(None, now=NOW)
    assert not listing_is_fresh(NOW, now=NOW.replace(tzinfo=None))
    assert not image_is_fresh(NOW, now="not-a-clock")
