import asyncio

from fastapi import HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import ValidationError
from sqlalchemy import select

from radar.db import DiscoveredItem, DiscoveryGroup, DiscoveryRun, Setting
from radar.discovery.config import Budgets, Connection, DiscoveryError, GroupInput
from radar.discovery.service import (
    budgets,
    overview,
    plan,
    promote,
    run_discovery,
    save_group,
)


def discovery_routes(app, render, preferences):
    app.state.discovery_lock = asyncio.Lock()

    def connection():
        try:
            return Connection.from_env()
        except ValidationError:
            raise HTTPException(
                422, "Invalid eBay environment configuration; check documented variable names"
            ) from None

    @app.get("/discovery")
    def discovery_page(request: Request):
        config = connection()
        with app.state.sessions() as s:
            recent = list(
                s.scalars(select(DiscoveryRun).order_by(DiscoveryRun.id.desc()).limit(20))
            )
            latest = next((run for run in recent if run.completed_at is not None), None)
            return render(
                request,
                "discovery.html",
                connection=config.status(),
                limits=budgets(s),
                runs=recent,
                latest=latest,
                groups=list(s.scalars(select(DiscoveryGroup))),
                notice=request.query_params.get("notice"),
                **overview(s, preferences(s)),
            )

    @app.get("/discovery/plan")
    def preview(request: Request):
        experiment = request.query_params.get("auto_correct", "omit")
        if experiment not in ("omit", "KEYWORD"):
            raise HTTPException(422, "auto_correct must be omitted or KEYWORD")
        with app.state.sessions() as s:
            entries = plan(s, budgets(s), experiment)
            return render(
                request,
                "discovery_plan.html",
                entries=entries,
                limits=budgets(s),
                connection=connection().status(),
            )

    @app.post("/discovery/settings")
    async def settings(request: Request):
        form = await request.form()
        try:
            values = {
                k: form[k]
                for k in ("max_queries", "max_results", "max_pages_per_query", "page_size")
                if k in form
            }
            values["enabled"] = form.get("enabled") == "true"
            limits = Budgets.model_validate(values)
        except ValueError:
            raise HTTPException(
                422, "Discovery budgets must be within the documented bounds"
            ) from None
        with app.state.sessions.begin() as s:
            row = s.get(Setting, 3)
            if row:
                row.data = limits.model_dump()
            else:
                s.add(Setting(id=3, data=limits.model_dump()))
        return RedirectResponse("/discovery", status_code=303)

    @app.post("/discovery/groups/{group_id}")
    async def group_settings(request: Request, group_id: str):
        form = await request.form()
        try:
            group = GroupInput.model_validate(
                dict(
                    name=form.get("name", ""),
                    enabled=form.get("enabled") == "true",
                    queries=[line for line in form.get("queries", "").splitlines() if line.strip()],
                    options={
                        key: form[key]
                        for key in ("buying_format", "min_price", "max_price", "sort")
                        if form.get(key) not in ("", None)
                    },
                )
            )
            with app.state.sessions.begin() as s:
                save_group(s, group_id, group)
        except DiscoveryError as e:
            raise HTTPException(404, str(e)) from e
        except ValueError:
            raise HTTPException(
                422, "Check group name, exact query lines, price range and search options"
            ) from None
        return RedirectResponse("/discovery", status_code=303)

    @app.post("/discovery/run")
    async def run(request: Request):
        form = await request.form()
        mode = form.get("mode", "fixture")
        if mode not in ("fixture", "sandbox", "production"):
            raise HTTPException(422, "Unknown discovery mode")
        if app.state.discovery_lock.locked():
            raise HTTPException(409, "A manual discovery run is already in progress")
        async with app.state.discovery_lock:
            try:
                await run_discovery(
                    app.state.sessions,
                    connection(),
                    mode,
                    auto_correct=form.get("auto_correct", "omit"),
                    approved_run=form.get("approve_production") == "yes",
                )
            except DiscoveryError as e:
                raise HTTPException(422, str(e)) from e
        return RedirectResponse(
            "/discovery?notice=Run%20completed%3B%20inspect%20recorded%20errors", status_code=303
        )

    @app.post("/discovery/items/{item_id}/review")
    def review_item(item_id: int):
        with app.state.sessions.begin() as s:
            try:
                listing = promote(s, item_id)
            except DiscoveryError as e:
                raise HTTPException(404, str(e)) from e
            return RedirectResponse(f"/listings/{listing.id}", status_code=303)

    @app.get("/discovery/items/{item_id}")
    def item_history(request: Request, item_id: int):
        with app.state.sessions() as s:
            row = s.get(DiscoveredItem, item_id)
            if not row:
                raise HTTPException(404, "Discovered item not found")
            from radar.db import DiscoveryObservation, SearchExecution

            observations = list(
                s.execute(
                    select(DiscoveryObservation, SearchExecution)
                    .join(SearchExecution, SearchExecution.id == DiscoveryObservation.execution_id)
                    .where(DiscoveryObservation.item_id == item_id)
                    .order_by(DiscoveryObservation.id)
                )
            )
            return render(request, "discovery_item.html", row=row, observations=observations)
