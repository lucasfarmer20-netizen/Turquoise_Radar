"""Private sourcing feed and explicit, local-only search preparation."""

from collections import Counter
from urllib.parse import urlencode, urlsplit

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from sqlalchemy import select

from radar.compact_feed import decorate_compact_cards, read_compact_photo
from radar.db import DiscoveryGroup, Feedback, IntakeSource, Listing, SavedSourceSearch
from radar.discovery.config import DiscoveryError
from radar.discovery.service import budgets, promote
from radar.feedback import record_feedback
from radar.intake.service import promote_candidate
from radar.schemas import FeedbackInput
from radar.search_evaluation import evaluate_searches
from radar.sourcing_catalog import MAX_STAGED_PHRASES, catalog_entries, stage_catalog_group
from radar.sourcing_feed import build_feed
from radar.workflow import STATES

PAGE_SIZE = 24
DATASETS = ("real", "fixture", "sandbox", "mixed", "all")
CLOSED_STATES = {"PASSED", "PURCHASED", "IRRELEVANT"}


def sourcing_routes(app, render, preferences):
    @app.get("/feed/evaluation")
    def evaluation(request: Request):
        with app.state.sessions() as session:
            return render(
                request,
                "search_evaluation.html",
                evaluation=evaluate_searches(session, preferences(session)),
            )

    @app.get("/feed")
    def feed(request: Request):
        q = request.query_params
        dataset = q.get("dataset", "real")
        scope = q.get("scope", "active")
        state = q.get("state", "")
        sort = q.get("sort", "recent")
        if (
            dataset not in DATASETS
            or scope not in ("active", "needs_photos", "ended", "reviewed", "archive", "all")
            or state not in ("", *STATES, "IRRELEVANT")
            or sort not in ("recent", "priority")
        ):
            raise HTTPException(422, "Invalid sourcing feed filter")
        try:
            page = int(q.get("page", "1"))
            if page < 1:
                raise ValueError
        except ValueError:
            raise HTTPException(422, "Page must be a positive integer") from None
        source = q.get("source", "")
        with app.state.sessions() as session:
            cards = decorate_compact_cards(session, build_feed(session, preferences(session)))
            counts = Counter(card["dataset"] for card in cards)
            sources = sorted({card["item"].source for card in cards})
            if source and source not in sources:
                # Keep bookmarked filters useful even after a source is emptied.
                sources.append(source)
                sources.sort()
            results = []
            view_counts = Counter(active=0, needs_photos=0, ended=0, reviewed=0, archive=0, all=0)
            for card in cards:
                if dataset != "all" and card["dataset"] != dataset:
                    continue
                if state and card["state"] != state or source and card["item"].source != source:
                    continue
                ended = card["compact"]["ended"]
                archived = card["state"] in CLOSED_STATES or ended
                views = {"all"}
                if ended:
                    views.add("ended")
                if archived:
                    views.add("archive")
                    if card["state"] in CLOSED_STATES:
                        views.add("reviewed")
                else:
                    views.add("active" if card["compact"]["photo_url"] else "needs_photos")
                view_counts.update(views)
                if scope in views:
                    results.append(card)
            if sort == "priority":
                results.sort(
                    key=lambda c: (
                        c["research_priority_weight"],
                        c["research_score"],
                        c["last_seen"],
                        c["key"],
                    ),
                    reverse=True,
                )
            else:
                results.sort(key=lambda c: (c["last_seen"], c["key"]), reverse=True)
            pages = max(1, (len(results) + PAGE_SIZE - 1) // PAGE_SIZE)
            page = min(page, pages)
            filters = dict(dataset=dataset, scope=scope, state=state, source=source, sort=sort)

            def page_url(number):
                return "/feed?" + urlencode({**filters, "page": number})

            return render(
                request,
                "sourcing_feed.html",
                cards=results[(page - 1) * PAGE_SIZE : page * PAGE_SIZE],
                total=len(results),
                counts=counts,
                view_counts=view_counts,
                filters=filters,
                datasets=DATASETS,
                states=[*STATES, "IRRELEVANT"],
                sources=sources,
                page=page,
                pages=pages,
                previous=page_url(page - 1) if page > 1 else None,
                following=page_url(page + 1) if page < pages else None,
                return_to=page_url(page),
                tab_urls={
                    target: "/feed?" + urlencode({**filters, "scope": target})
                    for target in ("active", "needs_photos", "ended", "reviewed", "archive", "all")
                },
            )

    @app.get("/feed/photos/{photo_id}")
    def compact_photo(photo_id: str):
        with app.state.sessions() as session:
            try:
                raw = read_compact_photo(session, preferences(session), photo_id)
            except (OSError, ValueError):
                raise HTTPException(404, "Private photo unavailable") from None
        return Response(
            raw,
            media_type="image/png",
            headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
        )

    @app.post("/feed/review/{kind}/{target_id}")
    async def compact_review(request: Request, kind: str, target_id: int):
        form = await request.form()
        decision = form.get("decision")
        if kind not in ("listing", "candidate") or target_id < 1:
            raise HTTPException(404, "Review target unavailable")
        if decision not in ("GO", "NO_GO", "CLEAR"):
            raise HTTPException(422, "Choose GO, NO GO or CLEAR")
        action = {"GO": "Interested", "NO_GO": "Passed", "CLEAR": "Cleared"}[decision]
        try:
            expected = (
                int(form["expected_feedback_id"]) if form.get("expected_feedback_id") else None
            )
            if expected is not None and expected < 1:
                raise ValueError
        except (ValueError, TypeError):
            raise HTTPException(422, "Invalid prior feedback receipt") from None
        with app.state.sessions.begin() as session:
            # Serialize promotion plus feedback and retries in the existing local SQLite app.
            if session.get_bind().dialect.name == "sqlite":
                session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            card = next(
                (
                    card
                    for card in build_feed(session, preferences(session))
                    if (kind == "listing" and card["listing_id"] == target_id)
                    or (kind == "candidate" and target_id in card["candidate_ids"])
                ),
                None,
            )
            if card is None:
                raise HTTPException(404, "Review target unavailable")
            if decision == "CLEAR" and card["listing_id"] is None:
                raise HTTPException(409, "There is no saved review decision to clear")
            try:
                if kind == "listing" or decision == "CLEAR":
                    listing = session.get(Listing, card["listing_id"])
                elif any(e["kind"] == "discovery" for e in card["query_evidence"]):
                    listing = promote(session, target_id)
                else:
                    listing = promote_candidate(session, target_id)
            except (ValueError, DiscoveryError):
                raise HTTPException(409, "Review target changed; reload the feed") from None
            if listing is None:
                raise HTTPException(404, "Review target unavailable")
            if listing.review_state == "PURCHASED":
                raise HTTPException(409, "Review purchased records in the workbench")
            latest = session.scalar(
                select(Feedback)
                .where(Feedback.listing_id == listing.id)
                .order_by(Feedback.created_at.desc(), Feedback.id.desc())
                .limit(1)
            )
            if latest and latest.action == action:
                if decision == "CLEAR":
                    try:
                        receipt = record_feedback(session, listing, FeedbackInput(action=action))
                    except ValueError as error:
                        raise HTTPException(409, str(error)) from None
                else:
                    receipt = latest
            else:
                if (latest.id if latest else None) != expected:
                    raise HTTPException(
                        409, "Feedback changed; reload before changing the decision"
                    )
                try:
                    receipt = record_feedback(session, listing, FeedbackInput(action=action))
                except ValueError as error:
                    raise HTTPException(409, str(error)) from None
                session.flush()
            result = {
                "saved": True,
                "decision": decision,
                "action": action,
                "feedback_id": receipt.id,
                "listing_id": listing.id,
                "state": listing.review_state,
                "research_url": f"/listings/{listing.id}",
            }
        if "application/json" in request.headers.get("accept", ""):
            return JSONResponse(result)
        return_to = form.get("return_to", "/feed")
        try:
            parsed = urlsplit(return_to)
            safe_return = parsed.path in ("/feed", f"/listings/{result['listing_id']}") and not (
                parsed.scheme or parsed.netloc
            )
        except (ValueError, TypeError):
            safe_return = False
        return RedirectResponse(return_to if safe_return else "/feed", status_code=303)

    @app.get("/feed/prepare")
    def prepare(request: Request):
        with app.state.sessions() as session:
            selected = Counter()
            configured = Counter()
            for row in session.scalars(select(SavedSourceSearch)):
                selected[row.source_id] += int(row.selected)
                configured[row.source_id] += int(row.configured)
            sources = [
                dict(
                    row=row,
                    selected=selected[row.id],
                    configured=configured[row.id],
                    url="/saved-searches?" + urlencode({"source": row.id, "catalog": "all"}),
                )
                for row in session.scalars(select(IntakeSource).order_by(IntakeSource.id))
            ]
            group_id = request.query_params.get("group", "")
            group = (
                session.get(DiscoveryGroup, group_id) if group_id.startswith("catalog-") else None
            )
            return render(
                request,
                "sourcing_prepare.html",
                entries=catalog_entries(),
                sources=sources,
                limits=budgets(session),
                maximum=MAX_STAGED_PHRASES,
                staged=group,
            )

    @app.post("/feed/prepare")
    async def stage(request: Request):
        form = await request.form()
        try:
            with app.state.sessions.begin() as session:
                row = stage_catalog_group(session, form.getlist("phrase"), form.get("name", ""))
                group_id = row.id
        except DiscoveryError as error:
            raise HTTPException(409, str(error)) from None
        except ValueError:
            raise HTTPException(
                422,
                "Choose 1–20 distinct catalog phrases and a nonblank draft name of at most "
                "100 characters",
            ) from None
        return RedirectResponse("/feed/prepare?" + urlencode({"group": group_id}), status_code=303)
