"""Bounded manual discovery with independent query, page and listing observation history."""

from collections import Counter
from hashlib import sha256

from pydantic import BaseModel
from sqlalchemy import select

from radar.anomalies import PatternInput, add_pattern, learned_priority
from radar.db import (
    DiscoveredItem,
    DiscoveryGroup,
    DiscoveryObservation,
    DiscoveryRun,
    Listing,
    SearchExecution,
    Setting,
)
from radar.discovery.config import Budgets, Connection, DiscoveryError, GroupInput, SearchOptions
from radar.discovery.ebay import SOURCE_NAMES, EbayAdapter, FixtureDiscoveryAdapter, normalize
from radar.schemas import ListingInput, utcnow
from radar.sources import dedup_key
from radar.workflow import attributed_patterns

DEFAULT_GROUPS = {
    "cheap-rings": dict(
        name="Cheap Ring Hunter",
        queries=[
            "old blue stone ring",
            "turqoise",
            "turquiose",
            "turquise",
            "sterlng",
            "silver ring blue rock",
            "green stone silver ring",
            "925 blue stone",
            "western silver ring",
        ],
    ),
    "silver-lots": dict(
        name="Silver Lot Hunter",
        queries=[
            "silver jewelry lot",
            "heavy silver bracelet",
            "grandma's silver jewelry",
            "mystery silver jewelry",
            "costume jewelry estate lot",
            "silver jewelry jar",
            "bulk silver jewelry",
        ],
    ),
    "southwestern": dict(
        name="Southwestern Anomaly Hunter",
        queries=[
            "nature america",
            "Navaho",
            "Navjo",
            "Zunni",
            "old stamped jewelry",
            "vintage blue stone cuff",
        ],
        options={"buying_format": "BOTH"},
    ),
}


class PlanEntry(BaseModel):
    query: str
    original_query: str
    pattern_id: str | None
    group_ids: list[str]
    priority: int
    options: SearchOptions


def seed_discovery(session):
    for id_, data in DEFAULT_GROUPS.items():
        for query in data["queries"]:
            add_pattern(
                session,
                PatternInput(
                    phrase=query,
                    original_phrase=query,
                    canonical_concept="Curated jewelry discovery",
                    anomaly_category="generic",
                    sources=["eBay"],
                    priority_weight=85,
                ),
            )
        if not session.get(DiscoveryGroup, id_):
            session.add(
                DiscoveryGroup(id=id_, data=GroupInput.model_validate(data).model_dump(mode="json"))
            )
    session.flush()


def budgets(session):
    row = session.get(Setting, 3)
    return Budgets.model_validate(row.data) if row else Budgets()


def plan(session, limits: Budgets, auto_correct="omit"):
    patterns = {p.phrase: p for p in attributed_patterns(session)}
    entries = {}
    if not limits.enabled:
        return []
    for row in session.scalars(select(DiscoveryGroup).order_by(DiscoveryGroup.id)):
        group = GroupInput.model_validate(row.data)
        if not group.enabled:
            continue
        options = group.options.model_copy(update={"auto_correct": auto_correct})
        for query in group.queries:
            p = patterns.get(query)
            if p and (not p.active or p.sources and "eBay" not in p.sources):
                continue
            key = (query, options.model_dump_json())
            if key in entries:
                entries[key].group_ids.append(row.id)
                continue
            entries[key] = PlanEntry(
                query=query,
                original_query=p.original_phrase if p else query,
                pattern_id=p.id if p else None,
                group_ids=[row.id],
                priority=learned_priority(p) if p else 80,
                options=options,
            )
    return sorted(entries.values(), key=lambda e: (-e.priority, e.group_ids[0], e.query))[
        : limits.max_queries
    ]


def save_group(session, id_, data: GroupInput):
    row = session.get(DiscoveryGroup, id_)
    if not row:
        raise DiscoveryError("NOT_FOUND", "Search group not found")
    # Owner-added exact phrases become bounded curated patterns, not generated typos.
    for query in data.queries:
        add_pattern(
            session,
            PatternInput(
                phrase=query,
                original_phrase=query,
                canonical_concept="Owner-curated eBay query",
                anomaly_category="manual",
                sources=["eBay"],
            ),
        )
    row.data = data.model_dump(mode="json")


def _identity(source, item_id):
    return sha256((source + "|" + item_id).encode()).hexdigest()


async def run_discovery(
    sessions, connection: Connection, mode, *, auto_correct="omit", approved_run=False, adapter=None
):
    connection.require(mode, approved_run)
    if auto_correct not in ("omit", "KEYWORD"):
        raise DiscoveryError("INVALID", "Correction experiment must be omitted or KEYWORD")
    with sessions() as session:
        limits = budgets(session)
        if not limits.enabled:
            raise DiscoveryError("DISABLED", "Discovery is disabled in owner settings")
        entries = plan(session, limits, auto_correct)
    if not entries:
        raise DiscoveryError("NO_QUERIES", "No enabled source-applicable search queries")
    source = SOURCE_NAMES[mode]
    with sessions.begin() as s:
        run = DiscoveryRun(mode=mode, options={**limits.model_dump(), "auto_correct": auto_correct})
        s.add(run)
        s.flush()
        run_id = run.id
    if adapter is None:
        adapter = (
            FixtureDiscoveryAdapter()
            if mode == "fixture"
            else EbayAdapter(connection, production_run_approved=approved_run)
        )
    remaining = limits.max_results
    run_errors = []
    try:
        for index, entry in enumerate(entries):
            if remaining <= 0:
                break
            allowance = min(remaining, max(1, remaining // (len(entries) - index)))
            params = entry.options.params(entry.query, min(limits.page_size, allowance))
            with sessions.begin() as s:
                execution = SearchExecution(
                    run_id=run_id,
                    source=source,
                    mode=mode,
                    group_ids=entry.group_ids,
                    pattern_id=entry.pattern_id,
                    original_query=entry.original_query,
                    transmitted_query=entry.query,
                    parameters=params,
                )
                s.add(execution)
                s.flush()
                execution_id = execution.id
            try:
                result = await adapter.search(
                    entry.query,
                    entry.options,
                    allowance,
                    limits.max_pages_per_query,
                    limits.page_size,
                )
                errors = list(result.errors)
            except DiscoveryError as e:
                errors = [f"{e.code}: {e}"]
                result = None
            except Exception:
                # Neither request URLs nor upstream exception bodies/headers reach storage/UI.
                errors = ["ADAPTER_ERROR: Discovery adapter failed; inspect configuration securely"]
                result = None
            with sessions.begin() as s:
                execution = s.get(SearchExecution, execution_id)
                run = s.get(DiscoveryRun, run_id)
                page_requests = []
                seen = set()
                corrected = []
                states = []
                returned = 0
                for page in result.pages if result else []:
                    page_requests.append(
                        entry.options.params(
                            entry.query, min(limits.page_size, allowance - returned), page.offset
                        )
                    )
                    states.append(page.correction_state)
                    if page.corrected_query is not None:
                        corrected.append(page.corrected_query)
                    for raw in page.items:
                        if returned >= allowance or remaining <= 0:
                            break
                        returned += 1
                        remaining -= 1
                        try:
                            item = normalize(
                                raw,
                                mode,
                                entry.query,
                                entry.original_query,
                                page.corrected_query,
                                entry.pattern_id,
                            )
                        except DiscoveryError as e:
                            errors.append(f"{e.code}: {e}")
                            run.invalid_count += 1
                            continue
                        identity = _identity(source, item.source_listing_id)
                        found = s.scalar(
                            select(DiscoveredItem).where(DiscoveredItem.identity == identity)
                        )
                        if found is None:
                            found = DiscoveredItem(
                                identity=identity,
                                source=source,
                                ebay_item_id=item.source_listing_id,
                                normalized=item.model_dump(mode="json"),
                                original_data=raw,
                            )
                            s.add(found)
                            s.flush()
                            execution.new_count += 1
                            run.new_count += 1
                        else:
                            found.last_seen = utcnow()
                            # Keep the first normalized query; retain each independent observation.
                            first = found.normalized
                            updated = item.model_dump(mode="json")
                            for key in (
                                "raw_search_phrase",
                                "original_search_phrase",
                                "search_pattern_id",
                                "effective_query",
                            ):
                                updated[key] = first.get(key)
                            found.normalized = updated
                            execution.duplicate_count += 1
                            run.duplicate_count += 1
                        seen.add(found.id)
                        s.add(
                            DiscoveryObservation(
                                execution_id=execution_id,
                                item_id=found.id,
                                raw_data=raw,
                                corrected_query=page.corrected_query,
                                correction_state=page.correction_state,
                            )
                        )
                execution.parameters = {
                    **params,
                    "pages": page_requests,
                    "destination_context": "US 99669 requested; shipping unconfirmed",
                }
                execution.results_returned = returned
                execution.unique_listings = len(seen)
                execution.corrected_queries = list(dict.fromkeys(corrected))
                execution.correction_state = (
                    "CORRECTED"
                    if "CORRECTED" in states
                    else "REPORTED_UNCHANGED"
                    if states and all(x == "REPORTED_UNCHANGED" for x in states)
                    else "UNKNOWN"
                )
                execution.errors = errors
                execution.status = (
                    "COMPLETED_WITH_ERRORS"
                    if errors and returned
                    else "FAILED"
                    if errors
                    else "COMPLETED"
                )
                run_errors.extend(errors)
            if any(
                error.startswith(("PERMISSION:", "RATE_LIMIT:", "NETWORK:")) for error in errors
            ):
                # Do not repeat an authorization/rate failure under each remaining query.
                break
    except Exception:
        run_errors.append("RUN_ERROR: Discovery stopped unexpectedly; no credentials recorded")
    finally:
        try:
            await adapter.close()
        except Exception:
            run_errors.append("CLEANUP_ERROR: Adapter cleanup failed")
        finally:
            with sessions.begin() as s:
                run = s.get(DiscoveryRun, run_id)
                executions = list(
                    s.scalars(select(SearchExecution).where(SearchExecution.run_id == run_id))
                )
                for execution in executions:
                    if execution.status == "RUNNING":
                        execution.status = "FAILED"
                        execution.errors = ["RUN_ERROR: Search was interrupted"]
                run.errors = list(dict.fromkeys(run_errors))
                failed = all(e.status == "FAILED" for e in executions)
                run.status = (
                    "FAILED" if failed else "COMPLETED_WITH_ERRORS" if run_errors else "COMPLETED"
                )
                run.completed_at = utcnow()
    return run_id


def promote(session, item_id):
    found = session.get(DiscoveredItem, item_id)
    if not found:
        raise DiscoveryError("NOT_FOUND", "Discovered item not found")
    item = ListingInput.model_validate(found.normalized)
    if found.listing_id:
        return session.get(Listing, found.listing_id)
    key = dedup_key(item)
    row = session.scalar(select(Listing).where(Listing.dedup_key == key))
    if row is None:
        # Raw API item remains original evidence, never merged into ListingInput keys.
        row = Listing(
            dedup_key=key,
            data=item.model_dump(mode="json"),
            original_data={
                "provider": "eBay Browse",
                "mode": item.discovery_mode,
                "item": found.original_data,
            },
            review_state="RESEARCH",
        )
        session.add(row)
        session.flush()
    found.listing_id = row.id
    return row


def discovery_history(session, listing_id):
    return list(
        session.execute(
            select(DiscoveryObservation, SearchExecution)
            .join(SearchExecution, SearchExecution.id == DiscoveryObservation.execution_id)
            .join(DiscoveredItem, DiscoveredItem.id == DiscoveryObservation.item_id)
            .where(DiscoveredItem.listing_id == listing_id)
            .order_by(DiscoveryObservation.id)
        )
    )


def overview(session, prefs):
    from radar.analysis import analyze

    items = list(session.scalars(select(DiscoveredItem).order_by(DiscoveredItem.last_seen.desc())))
    observations = list(
        session.execute(
            select(DiscoveryObservation, SearchExecution).join(
                SearchExecution, SearchExecution.id == DiscoveryObservation.execution_id
            )
        )
    )
    history = {item.id: [] for item in items}
    for observation, execution in observations:
        history[observation.item_id].append((observation, execution))
    cards = []
    for row in items:
        item = ListingInput.model_validate(row.normalized)
        a = analyze(item, prefs)
        cards.append(
            dict(
                row=row,
                item=item,
                a=a,
                history=history[row.id],
                reason=(
                    "Ring title; affordability requires full Alaska costs"
                    if "ring" in item.raw_title.lower()
                    else "Lot / silver vocabulary; material and net weight require evidence"
                ),
                image=str(item.image_urls[0])
                if item.image_urls and item.discovery_mode != "fixture"
                else None,
            )
        )
    executions = list(session.scalars(select(SearchExecution).order_by(SearchExecution.id.desc())))
    productive = sorted(executions, key=lambda e: (-e.unique_listings, -e.new_count, e.id))[:8]
    return dict(
        cards=cards,
        productive=productive,
        executions=executions[:100],
        missing=sum(c["a"].landed is None or c["item"].resale_low is None for c in cards),
        rings=sum(
            "ring" in c["item"].raw_title.lower()
            and c["item"].currency == "USD"
            and c["item"].item_price is not None
            and c["item"].item_price <= prefs.ring_high
            for c in cards
        ),
        lots=sum("lot" in c["item"].raw_title.lower() for c in cards),
        formats=dict(Counter(option for c in cards for option in c["item"].buying_options)),
    )
