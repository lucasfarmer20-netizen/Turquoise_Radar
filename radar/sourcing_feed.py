"""Read-only sourcing cards from existing intake, owner review and query evidence."""

from collections import defaultdict
from datetime import UTC

from sqlalchemy import select

from radar.analysis import analyze
from radar.db import (
    AlertMessage,
    AlertObservation,
    DiscoveredItem,
    DiscoveryObservation,
    Feedback,
    Listing,
    SearchExecution,
)
from radar.ebay_deletion.processing import REMOVED, is_ebay
from radar.intake.service import observation_occurrences
from radar.schemas import ListingInput, Shipping, utcnow
from radar.workflow import queue_state


def _utc(value):
    # ORM timestamps are written in UTC; SQLite returns them without timezone information.
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _removed(data):
    return is_ebay(data) and data.get("raw_title") == REMOVED


def _item_datasets(item):
    """Explicit non-live markers survive edits; missing markers are not mode evidence."""
    modes = set()
    if item.discovery_mode:
        modes.add("real" if item.discovery_mode == "production" else item.discovery_mode)
    if item.synthetic and item.discovery_mode != "sandbox":
        modes.add("fixture")
    source = item.source.casefold()
    if source.endswith(" fixture"):
        modes.add("fixture")
    if source.endswith(" sandbox"):
        modes.add("sandbox")
    return modes


def _query_from_item(item):
    query = item.raw_search_phrase
    if query is None:
        query = item.original_search_phrase
    if query is None:
        return []
    corrected = item.effective_query
    return [
        dict(
            kind="owner",
            source=item.source,
            query=query,
            original_query=item.original_search_phrase,
            corrected_query=corrected,
            correction_state="CORRECTED" if corrected not in (None, query) else "UNKNOWN",
            attribution="OWNER-RECORDED QUERY",
            mode=item.discovery_mode,
            observation_id=None,
        )
    ]


def _next_evidence(item, analysis, availability):
    evidence = []
    missing = [key for key, value in analysis.costs.items() if value is None]
    if missing:
        evidence.append("Obtain missing acquisition costs: " + ", ".join(missing) + ".")
    estimated = [key for key, status in analysis.cost_status.items() if status == "ESTIMATED"]
    if estimated:
        evidence.append("Confirm estimated acquisition costs: " + ", ".join(estimated) + ".")
    if item.shipping_status == Shipping.CONFIRMED_UNAVAILABLE:
        evidence.append("Shipping to Alaska 99669 is unavailable; this is a PASS shipping gate.")
    elif item.shipping_status != Shipping.CONFIRMED_TO_ALASKA:
        evidence.append("Confirm Alaska 99669 shipping eligibility and a delivered shipping quote.")
    if item.currency != "USD":
        evidence.append(
            "Confirm USD acquisition amounts before comparing delivered cost or profit."
        )
    if not item.condition:
        evidence.append("Review condition evidence.")
    if item.material_evidence == "unknown":
        evidence.append("Review material evidence; seller wording alone does not verify silver.")
    if item.resale_low is None:
        evidence.append("Record supported resale comparables or an explicit owner resale scenario.")
    elif not item.resale_confirmed:
        evidence.append("Confirm the recorded resale evidence and scenario.")
    if availability == "ENDED":
        evidence.append(
            "Ended evidence is recorded; confirm a relisting before acquisition review."
        )
    return evidence


def build_feed(session, prefs):
    """Project existing records without writes, source fetches or new copies of private data.

    A linked owner Listing is authoritative for financial evidence and workflow state.
    Candidate identity is never merged by title or across sources. Multiple candidates
    already linked to the same Listing share one card and retain every observation.
    """
    with session.no_autoflush:
        return _build_feed(session, prefs)


def _build_feed(session, prefs):
    now = utcnow()
    listings = {row.id: row for row in session.scalars(select(Listing).order_by(Listing.id))}
    candidates = list(session.scalars(select(DiscoveredItem).order_by(DiscoveredItem.id)))
    removed_listings = {
        row.id
        for row in listings.values()
        if _removed(row.data) or row.dedup_key.startswith("deleted-ebay-listing-")
    }
    removed_candidates = {row.id for row in candidates if _removed(row.normalized)}
    # An inconsistent partially redacted link must not resurrect account data either.
    removed_listings.update(
        row.listing_id for row in candidates if row.id in removed_candidates and row.listing_id
    )
    candidates = [
        row
        for row in candidates
        if row.id not in removed_candidates and row.listing_id not in removed_listings
    ]
    candidate_ids = {row.id for row in candidates}
    queries, datasets, times = defaultdict(list), defaultdict(set), defaultdict(list)
    ended, counts = set(), defaultdict(int)
    for observation, execution in session.execute(
        select(DiscoveryObservation, SearchExecution)
        .join(SearchExecution, SearchExecution.id == DiscoveryObservation.execution_id)
        .order_by(DiscoveryObservation.id)
    ):
        if observation.item_id not in candidate_ids:
            continue
        item_id = observation.item_id
        datasets[item_id].add(
            {"production": "real", "fixture": "fixture", "sandbox": "sandbox"}.get(
                execution.mode, "mixed"
            )
        )
        times[item_id].append(_utc(observation.observed_at))
        counts[item_id] += 1
        queries[item_id].append(
            dict(
                kind="discovery",
                source=execution.source,
                query=execution.transmitted_query,
                original_query=execution.original_query,
                corrected_query=observation.corrected_query,
                correction_state=observation.correction_state,
                attribution="NON-LIVE QUERY"
                if execution.mode != "production"
                else "SOURCE CORRECTED"
                if observation.correction_state == "CORRECTED"
                else "SOURCE QUERY",
                mode=execution.mode,
                observation_id=observation.id,
                execution_id=execution.id,
            )
        )
    for observation, message in session.execute(
        select(AlertObservation, AlertMessage)
        .join(AlertMessage, AlertMessage.id == AlertObservation.message_id)
        .order_by(AlertObservation.id)
    ):
        if observation.item_id not in candidate_ids:
            continue
        item_id = observation.item_id
        datasets[item_id].add("fixture" if message.synthetic else "real")
        times[item_id].append(_utc(observation.created_at))
        for occurrence in observation_occurrences(observation):
            counts[item_id] += 1
            if occurrence.data.get("availability") == "ENDED":
                ended.add(item_id)
            verified = bool(
                occurrence.query_text is not None
                and occurrence.attribution_verified
                and message.message_type == "SEARCH_MATCH"
                and occurrence.data.get("message_role") != "RECOMMENDED"
            )
            queries[item_id].append(
                dict(
                    kind="alert",
                    source=message.source_id,
                    query=occurrence.query_text,
                    original_query=occurrence.query_text,
                    corrected_query=None,
                    correction_state="UNKNOWN",
                    attribution="UNVERIFIED ALERT"
                    if not verified
                    else "NON-LIVE QUERY"
                    if message.synthetic
                    else "SOURCE QUERY",
                    mode="fixture" if message.synthetic else "production",
                    observation_id=observation.id,
                    message_id=message.id,
                    message_type=message.message_type,
                    message_role=occurrence.data.get("message_role"),
                )
            )
    feedback = {}
    for row in session.scalars(select(Feedback).order_by(Feedback.created_at, Feedback.id)):
        feedback[row.listing_id] = dict(action=row.action, created_at=_utc(row.created_at))
    groups = {
        f"listing:{row.id}": (row, [])
        for row in listings.values()
        if row.id not in removed_listings
    }
    for row in candidates:
        listing = listings.get(row.listing_id)
        key = f"listing:{listing.id}" if listing else f"candidate:{row.id}"
        groups.setdefault(key, (listing, []))[1].append(row)
    cards = []
    for key, (listing, linked) in groups.items():
        item = ListingInput.model_validate(listing.data if listing else linked[0].normalized)
        analysis = analyze(item, prefs)
        modes = _item_datasets(item)
        history, observed = [], []
        for row in linked:
            modes.update(_item_datasets(ListingInput.model_validate(row.normalized)))
            modes.update(datasets[row.id])
            history.extend(queries[row.id])
            observed.extend([_utc(row.last_seen), *times[row.id]])
        # Promotion creates an owner row now without observing the source again.
        # Only manual-only records use the owner's capture timestamp as freshness.
        if listing and not linked:
            observed.append(_utc(listing.last_seen))
        # A naive source end time has no trustworthy timezone and cannot establish expiry.
        expired = bool(
            item.item_end_time
            and item.item_end_time.tzinfo is not None
            and item.item_end_time.utcoffset() is not None
            and item.item_end_time <= now
        )
        availability = "ENDED" if expired or any(row.id in ended for row in linked) else "UNKNOWN"
        state = (
            queue_state(listing, analysis)
            if listing
            else "IRRELEVANT"
            if linked[0].inbox_status == "IRRELEVANT"
            else "NEW"
        )
        cards.append(
            dict(
                key=key,
                listing_id=listing.id if listing else None,
                candidate_ids=[row.id for row in linked],
                item=item,
                a=analysis,
                state=state,
                last_seen=max(observed),
                availability=availability,
                dataset="mixed" if len(modes) > 1 else next(iter(modes), "real"),
                query_evidence=history or _query_from_item(item),
                observation_count=sum(counts[row.id] for row in linked),
                latest_feedback=feedback.get(listing.id) if listing else None,
                missing_costs=[k for k, value in analysis.costs.items() if value is None],
                next_evidence=_next_evidence(item, analysis, availability),
            )
        )
    return sorted(cards, key=lambda card: (card["last_seen"], card["key"]), reverse=True)
