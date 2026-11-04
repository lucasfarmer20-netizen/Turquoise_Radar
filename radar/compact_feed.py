"""Dated, conservative scan cards and verified local item photographs; no new evidence."""

import os
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic import ValidationError
from sqlalchemy import select

from radar.db import (
    AlertMessage,
    AlertObservation,
    ComparableSale,
    DiscoveryObservation,
    VisionCase,
    VisionPhoto,
)
from radar.discovery.image_freshness import display_expires_at, listing_is_fresh
from radar.feed_photo_framing import (
    framing_request,
    load_photo_framing,
    validated_photo_frame,
)
from radar.intake.service import observation_occurrences
from radar.jewelry.images import ORIGINAL_PROCESSING_VERSION, photo_storage_metadata
from radar.jewelry.schemas import OWNER
from radar.research_assets import read_research_photo
from radar.research_evidence import QUOTE_FIELDS
from radar.schemas import ComparableInput, Shipping, utcnow
from radar.sourcing_feed import build_feed

ALASKA = ZoneInfo("America/Anchorage")
PHOTO_ORDER = {"FRONT": 0, "BACK_HALLMARK": 1, "STONE": 2, "SIDE_DETAIL": 3}


def _aware(value):
    return value is not None and value.utcoffset() is not None


def _orm_utc(value):
    # These columns are written in UTC; SQLite strips their timezone on readback.
    return value.replace(tzinfo=UTC) if value and value.tzinfo is None else value


def _price(value, currency):
    if value is None:
        return "UNKNOWN"
    amount = f"{value:,.2f}"
    return f"{amount} · currency unconfirmed" if currency == "XXX" else f"{amount} {currency}"


def _assessment(
    card, current_date, observed_date, observed_at, comparables_supported, fresh_spot, ended
):
    item, analysis = card["item"], card["a"]
    if ended:
        return "NEEDS INFO", "Ended; historical research only."
    if item.currency != "USD":
        return "NEEDS INFO", "Confirm currency and delivered costs."
    if item.shipping_status != Shipping.CONFIRMED_TO_ALASKA:
        return "NEEDS INFO", "Confirm Alaska delivery eligibility."
    if analysis.landed is None or any(
        value is None or analysis.cost_status.get(key) != "CONFIRMED"
        for key, value in analysis.costs.items()
    ):
        return "NEEDS INFO", "Confirm fees, tax and shipping costs."
    if observed_date != current_date or observed_at is False:
        return "NEEDS INFO", "Refresh the dated price quote."
    if not item.condition or item.material_evidence == "unknown":
        return "NEEDS INFO", "Review condition and material evidence."
    if analysis.confidence == "LOW":
        return "NEEDS INFO", "Confirm the financial assumptions."
    resale = bool(
        comparables_supported
        and analysis.low
        and analysis.low.profit is not None
        and not analysis.low.missing
        and all(value == "CONFIRMED" for value in analysis.low.input_status.values())
    )
    metal = bool(
        item.weight_kind == "net_silver"
        and item.weight_verified
        and item.weight_evidence
        and item.material_evidence == "verified_sterling"
        and analysis.melt.conservative_liquidation_value is not None
    )
    melt = metal and fresh_spot
    if not resale and metal and not fresh_spot:
        return "NEEDS INFO", "Refresh dated silver spot evidence."
    if not resale and not melt:
        return "NEEDS INFO", "Add comparables or verified metal value."
    if analysis.tier == "BUY CANDIDATE" and (
        resale and "WHATNOT FLIP" in analysis.categories or melt and analysis.melt.eligible
    ):
        return "POTENTIAL DEAL", "Meets the supported value target."
    if (
        resale
        and analysis.low.profit >= 0
        or melt
        and analysis.landed <= analysis.melt.conservative_liquidation_value
    ):
        return "FAIR PRICE", "Covers costs; below the target margin."
    return "TOO EXPENSIVE", "Costs exceed supported break-even value."


def compact_projection(
    card, *, now=None, observed_at=None, comparables_supported=False, source_quote=None
):
    """Assess dated evidence, not live availability.

    Acquisition quotes must have today's Alaska date; date-only owner reports retain
    their uncertainty label. Melt support additionally needs an aware, nonfuture spot
    timestamp on that same Alaska date. The underlying financial analysis is unchanged.
    """
    now = now or utcnow()
    if not _aware(now):
        raise ValueError("Compact-feed clock requires an explicit timezone")
    item, research = card["item"], card.get("research")
    latest = research["latest"] if research else None
    deadline = latest.reported_end_at if latest and latest.reported_end_at else item.item_end_time
    deadline = deadline if _aware(deadline) else None
    ended = card.get("availability") == "ENDED" or bool(deadline and deadline <= now)
    quote_conflict = bool(source_quote and source_quote.get("conflicting"))
    use_report = latest and (latest.item_quote is not None or latest.availability == "ENDED")
    if use_report and source_quote and latest.availability != "ENDED":
        source_time = source_quote["observed_at"]
        source_date = source_time.astimezone(ALASKA).date()
        report_date = (
            latest.observed_at.astimezone(ALASKA).date()
            if latest.observed_at
            else latest.observed_date
        )
        if (
            latest.observed_at
            and source_time > latest.observed_at
            or not latest.observed_at
            and source_date > report_date
        ):
            use_report = False
        elif (
            source_date == report_date and latest.observed_at is None
        ) or source_time == latest.observed_at:
            quote_conflict = quote_conflict or (
                source_quote["amount"] != latest.item_quote
                or source_quote["currency"] != latest.currency
            )
    if use_report:
        observed_at = latest.observed_at
        observed_date = latest.observed_date
        if latest.availability == "ENDED":
            price, currency, label = (
                latest.realized_hammer,
                latest.realized_currency or "XXX",
                "Realized",
            )
        else:
            price, currency = latest.item_quote, latest.currency
            label = {
                "CURRENT_BID": "Last observed bid",
                "MINIMUM_BID": "Last observed minimum",
            }.get(latest.quote_kind, "Last observed price")
            quote_conflict = (
                quote_conflict
                or latest.currency != item.currency
                or any(
                    getattr(latest, field) is not None
                    and getattr(latest, field) != card["a"].costs.get(key)
                    for key, field in QUOTE_FIELDS.items()
                )
            )
    else:
        price, currency, label = (
            (None if ended else item.item_price),
            item.currency,
            "Last observed price",
        )
        if source_quote and not ended:
            price, currency, observed_at = (
                source_quote["amount"],
                source_quote["currency"],
                source_quote["observed_at"],
            )
            quote_conflict = (
                quote_conflict
                or price != card["a"].costs.get("item_price")
                or currency != item.currency
            )
        observed_at = observed_at if _aware(observed_at) else None
        observed_date = observed_at.astimezone(ALASKA).date() if observed_at else None
    if latest and latest.availability != "ENDED":
        supplied_costs = {
            key: getattr(latest, field)
            for key, field in QUOTE_FIELDS.items()
            if getattr(latest, field) is not None
        }
        quote_conflict = (
            quote_conflict
            or bool(supplied_costs)
            and (
                latest.currency != item.currency
                or any(value != card["a"].costs.get(key) for key, value in supplied_costs.items())
            )
            or (
                latest.shipping_state in ("UNAVAILABLE", "RESTRICTIONS_REVIEW")
                and item.shipping_status == Shipping.CONFIRMED_TO_ALASKA
            )
        )
    if _aware(observed_at):
        observed_date = observed_at.astimezone(ALASKA).date()
        observed_label = observed_at.astimezone(ALASKA).strftime("%Y-%m-%d %H:%M %Z")
    elif observed_date:
        observed_label = f"{observed_date.isoformat()} · exact time unknown"
    else:
        observed_label = "Observation date unknown"
    deadline_text, deadline_iso = "Close time unknown", None
    countdown = "Close time unknown"
    if deadline:
        deadline_text = deadline.astimezone(ALASKA).strftime("%Y-%m-%d %H:%M %Z")
        deadline_iso = deadline.astimezone(UTC).isoformat()
        seconds = max(0, int((deadline - now).total_seconds()))
        days, remainder = divmod(seconds, 86400)
        hours, remainder = divmod(remainder, 3600)
        minutes = remainder // 60
        duration = (
            f"{days}d {hours}h"
            if days
            else f"{hours}h {minutes}m"
            if hours
            else f"{minutes}m"
            if minutes
            else "<1m"
        )
        countdown = "Recorded close passed" if deadline <= now else f"Recorded close in {duration}"
    spot_at = card.get("spot_timestamp")
    if isinstance(spot_at, str):
        try:
            spot_at = datetime.fromisoformat(spot_at)
        except ValueError:
            spot_at = None
    fresh_spot = bool(
        _aware(spot_at)
        and spot_at <= now
        and spot_at.astimezone(ALASKA).date() == now.astimezone(ALASKA).date()
    )
    assessment, reason = _assessment(
        card,
        now.astimezone(ALASKA).date(),
        observed_date,
        False if _aware(observed_at) and observed_at > now else observed_at,
        comparables_supported,
        fresh_spot,
        ended,
    )
    if quote_conflict and not ended:
        assessment, reason = "NEEDS INFO", "Reconcile the quote and cost evidence."
    return dict(
        assessment=assessment,
        reason=reason,
        price_label=label,
        price_text=_price(price, currency),
        observed_label=observed_label,
        deadline_text=deadline_text,
        deadline_iso=deadline_iso,
        countdown_label=countdown,
        ended=ended,
        needs_info=assessment == "NEEDS INFO",
        photo_id=None,
        photo_url=None,
        photo_role=None,
        photo_frame=None,
    )


def _quote_times(session, candidate_ids):
    quotes = []
    if not candidate_ids:
        return quotes
    for observation, sent_at in session.execute(
        select(AlertObservation, AlertMessage.sent_at)
        .join(AlertMessage, AlertMessage.id == AlertObservation.message_id)
        .where(AlertObservation.item_id.in_(candidate_ids))
    ):
        if sent_at:
            for occurrence in observation_occurrences(observation):
                quotes.append(
                    (
                        observation.item_id,
                        occurrence.data.get("quoted_price"),
                        occurrence.data.get("currency") or "XXX",
                        _orm_utc(sent_at),
                    )
                )
    for observation in session.scalars(
        select(DiscoveryObservation).where(DiscoveryObservation.item_id.in_(candidate_ids))
    ):
        raw = observation.raw_data
        if not isinstance(raw, dict):
            continue
        price = raw.get("currentBidPrice") or raw.get("price") or {}
        if isinstance(price, dict):
            quotes.append(
                (
                    observation.item_id,
                    price.get("value"),
                    price.get("currency") or "XXX",
                    _orm_utc(observation.observed_at),
                )
            )
    return quotes


def _latest_quote(card, quotes):
    matches = []
    for candidate_id, amount, currency, observed_at in quotes:
        if candidate_id in card["candidate_ids"] and amount is not None and currency is not None:
            try:
                amount = Decimal(str(amount))
                if amount.is_finite() and amount >= 0 and _aware(observed_at):
                    matches.append(dict(amount=amount, currency=currency, observed_at=observed_at))
            except InvalidOperation:
                continue
    if not matches:
        return None
    latest = max(matches, key=lambda value: value["observed_at"])
    tied = [value for value in matches if value["observed_at"] == latest["observed_at"]]
    if len({(value["amount"], value["currency"]) for value in tied}) > 1:
        return dict(
            amount=None, currency="XXX", observed_at=latest["observed_at"], conflicting=True
        )
    return latest


def _comparable_support(item, rows, dataset):
    if not item.resale_confirmed or not item.resale_comparable_ids or dataset == "mixed":
        return False
    selected = [rows.get(id_) for id_ in set(item.resale_comparable_ids)]
    if any(row is None for row in selected):
        return False
    try:
        inputs = [ComparableInput.model_validate(row.data) for row in selected]
    except ValidationError:
        return False
    if any(value.synthetic != (dataset != "real") for value in inputs):
        return False
    prices = [value.sale_price for value in inputs]
    return item.resale_low == min(prices) and item.resale_high == max(prices)


def _photo_root(session, configured):
    if configured is not None:
        return Path(configured)
    database = Path(session.get_bind().engine.url.database or "data/radar.db")
    return Path(
        os.getenv("VISION_IMAGE_DIR", str(database.parent / (database.stem + "-vision-images")))
    )


def _photo_choices(card, photos):
    attachments = {
        value.vision_photo_id: value
        for value in card["item"].research_attachments
        if value.vision_photo_id
    }
    for photo, case in photos:
        matches = (
            case.listing_id == card["listing_id"]
            if case.listing_id
            else case.candidate_id in card["candidate_ids"]
        )
        attachment = attachments.get(photo.id)
        if (
            not photo.active
            or case.owner_id != OWNER
            or not matches
            or photo.role not in PHOTO_ORDER
            or attachment
            and photo.original_hash != attachment.original_sha256
        ):
            continue
        yield photo


def _photos(session):
    return sorted(
        session.execute(
            select(VisionPhoto, VisionCase).join(VisionCase, VisionCase.id == VisionPhoto.case_id)
        ),
        key=lambda pair: (PHOTO_ORDER.get(pair[0].role, 99), pair[0].id),
    )


def _framed_photo_choices(card, photos, root, manifest):
    attached = {value.vision_photo_id for value in card["item"].research_attachments}
    attached_hashes = {value.original_sha256 for value in card["item"].research_attachments}
    requests = []
    for photo in _photo_choices(card, photos):
        if photo.data.get("processing_version") == ORIGINAL_PROCESSING_VERSION:
            item = card["item"]
            if (
                item.source.casefold() != "ebay"
                or item.discovery_mode != "production"
                or photo.data.get("source_listing_id") != item.source_listing_id
                or not any(
                    sha256(str(url).encode()).hexdigest() == photo.data.get("source_url_sha256")
                    for url in item.image_urls
                )
            ):
                continue
        request = framing_request(
            manifest,
            card["item"],
            photo,
            attached=photo.id in attached or photo.original_hash in attached_hashes,
        )
        if request is None:
            continue
        direct = request == "direct"
        area = 0 if direct else request.bbox[2] * request.bbox[3]
        requests.append((not direct, -area, PHOTO_ORDER[photo.role], photo.id, photo, request))
    for _, _, _, _, photo, request in sorted(requests):
        try:
            raw = read_research_photo(root, photo)
        except (ValueError, OSError):
            continue
        frame = validated_photo_frame(
            request,
            raw,
            allow_original=photo.data.get("processing_version") == ORIGINAL_PROCESSING_VERSION,
        )
        if frame is not None:
            yield photo, frame, raw


def decorate_compact_cards(session, cards, *, now=None, photo_root=None):
    """Read-only decoration; dates come from source quotes, never import/owner activity."""
    now = now or utcnow()
    with session.no_autoflush:
        quotes = _quote_times(session, {id_ for card in cards for id_ in card["candidate_ids"]})
        comps = {row.id: row for row in session.scalars(select(ComparableSale))}
        photos, root = _photos(session), _photo_root(session, photo_root)
        manifest = load_photo_framing(root)
        result = []
        for card in cards:
            source_observations = [
                observed_at
                for candidate_id, _, _, observed_at in quotes
                if candidate_id in card["candidate_ids"] and _aware(observed_at)
            ]
            production_ebay = (
                card["item"].source.casefold() == "ebay"
                and card["item"].discovery_mode == "production"
            )
            compact = compact_projection(
                card,
                now=now,
                source_quote=_latest_quote(card, quotes),
                comparables_supported=_comparable_support(card["item"], comps, card["dataset"]),
            )
            compact["listing_fresh"] = not production_ebay or listing_is_fresh(
                max(source_observations) if source_observations else None, now=now
            )
            observed = max(source_observations) if source_observations else None
            compact["source_expiry_required"] = production_ebay
            if production_ebay:
                compact["source_checked_at"] = now.isoformat()
                compact["source_expires_at"] = display_expires_at(observed)
            for photo, frame, _ in _framed_photo_choices(card, photos, root, manifest):
                if (
                    production_ebay
                    and photo.data.get("processing_version") == ORIGINAL_PROCESSING_VERSION
                ):
                    compact["source_expires_at"] = display_expires_at(
                        observed, photo_data=photo.data
                    )
                compact.update(
                    photo_id=photo.id,
                    photo_url=f"/feed/photos/{photo.id}",
                    photo_role=photo.role,
                    photo_frame=frame,
                )
                break
            result.append({**card, "compact": compact})
        return result


def read_compact_photo(session, prefs, photo_id, *, photo_root=None, include_media_type=False):
    """Recheck surviving source membership and ownership on every thumbnail/full-image GET."""
    with session.no_autoflush:
        photos = [(photo, case) for photo, case in _photos(session) if photo.id == photo_id]
        root = _photo_root(session, photo_root)
        manifest = load_photo_framing(root)
        for card in build_feed(session, prefs):
            if (
                card["item"].source.casefold() == "ebay"
                and card["item"].discovery_mode == "production"
            ):
                observations = _quote_times(session, set(card["candidate_ids"]))
                stamps = [stamp for _, _, _, stamp in observations if _aware(stamp)]
                if not listing_is_fresh(max(stamps) if stamps else None):
                    continue
            for photo, _, raw in _framed_photo_choices(card, photos, root, manifest):
                if include_media_type:
                    _, media_type = photo_storage_metadata(
                        photo.data, photo.original_hash, photo.content_hash
                    )
                    return raw, media_type
                return raw
    raise ValueError("Private item photo unavailable")
