"""Display freshness only: image retrieval never refreshes a listing observation.

These checks do not delete evidence, make source requests or certify availability.
Callers must retain the actual listing observation and image-fetch timestamps.
"""

from datetime import UTC, datetime, timedelta

LISTING_DISPLAY_AGE = timedelta(hours=6)
IMAGE_DISPLAY_AGE = timedelta(hours=24)


def _aware(value):
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return None
    if not isinstance(value, datetime) or value.utcoffset() is None:
        return None
    return value.astimezone(UTC)


def _fresh(value, max_age, now):
    stamp = _aware(value)
    clock = _aware(now if now is not None else datetime.now(UTC))
    return bool(stamp and clock and timedelta(0) <= clock - stamp <= max_age)


def listing_is_fresh(observed_at, *, now=None):
    return _fresh(observed_at, LISTING_DISPLAY_AGE, now)


def image_is_fresh(fetched_at, *, now=None):
    return _fresh(fetched_at, IMAGE_DISPLAY_AGE, now)


def photo_display_is_fresh(observed_at, fetched_at, *, now=None):
    clock = now if now is not None else datetime.now(UTC)
    return listing_is_fresh(observed_at, now=clock) and image_is_fresh(fetched_at, now=clock)


def source_image_is_fresh(data, *, now=None):
    return isinstance(data, dict) and photo_display_is_fresh(
        data.get("listing_observed_at"), data.get("fetched_at"), now=now
    )
