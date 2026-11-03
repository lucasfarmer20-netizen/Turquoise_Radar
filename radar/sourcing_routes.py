"""Private sourcing feed and explicit, local-only search preparation."""

from collections import Counter
from urllib.parse import urlencode

from fastapi import HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from radar.db import DiscoveryGroup, IntakeSource, SavedSourceSearch
from radar.discovery.config import DiscoveryError
from radar.discovery.service import budgets
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
            or scope not in ("active", "archive", "all")
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
            cards = build_feed(session, preferences(session))
            counts = Counter(card["dataset"] for card in cards)
            sources = sorted({card["item"].source for card in cards})
            if source and source not in sources:
                # Keep bookmarked filters useful even after a source is emptied.
                sources.append(source)
                sources.sort()
            results = []
            for card in cards:
                archived = card["state"] in CLOSED_STATES or card["availability"] == "ENDED"
                if dataset != "all" and card["dataset"] != dataset:
                    continue
                if scope == "active" and archived or scope == "archive" and not archived:
                    continue
                if state and card["state"] != state or source and card["item"].source != source:
                    continue
                results.append(card)
            if sort == "priority":
                results.sort(
                    key=lambda c: (
                        c["research_priority_weight"], c["research_score"], c["last_seen"], c["key"]
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
                filters=filters,
                datasets=DATASETS,
                states=[*STATES, "IRRELEVANT"],
                sources=sources,
                page=page,
                pages=pages,
                previous=page_url(page - 1) if page > 1 else None,
                following=page_url(page + 1) if page < pages else None,
            )

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
