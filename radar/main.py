import json
from contextlib import asynccontextmanager
from decimal import Decimal, InvalidOperation
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError
from sqlalchemy import select

from radar.analysis import analyze
from radar.anomalies import (
    PatternInput,
    add_pattern,
    generate_queries,
    learned_priority,
    seed_patterns,
)
from radar.db import ComparableSale, Feedback, Listing, SearchPattern, Setting, database
from radar.discovery.routes import discovery_routes
from radar.discovery.service import discovery_history, seed_discovery
from radar.owner_routes import owner_routes
from radar.schemas import FeedbackInput, ListingInput, Preferences, Shipping
from radar.sources import import_rows, upsert_listing
from radar.workflow import attributed_patterns, queue_state

ROOT = Path(__file__).resolve().parent
TEMPLATES = Jinja2Templates(directory=str(ROOT / "templates"))
TEMPLATES.env.filters["usd"] = lambda v: "UNKNOWN" if v is None else f"${v:,.2f}"
TEMPLATES.env.filters["pct"] = lambda v: "UNKNOWN" if v is None else f"{v:.1%}"
CATEGORIES = ["RING RESTOCK", "STERLING / MELT", "WHATNOT FLIP", "COLLECTOR UPSIDE", "MULTI-LOT"]
ACTIONS = ["Interested", "Purchased", "Passed", "False positive", "Sold"]


def preferences(session):
    row = session.get(Setting, 1)
    return Preferences.model_validate(row.data) if row else Preferences()


def view(row, prefs, patterns):
    item = ListingInput.model_validate(row.data)
    analysis = analyze(item, prefs)
    return {
        "state": queue_state(row, analysis),
        "row": row,
        "item": item,
        "a": analysis,
        "pattern": patterns.get(item.search_pattern_id),
    }


def create_app(database_url=None):
    @asynccontextmanager
    async def lifespan(app):
        app.state.engine, app.state.sessions = database(database_url)
        with app.state.sessions.begin() as session:
            seed_patterns(session)
            seed_discovery(session)
        yield
        app.state.engine.dispose()

    app = FastAPI(title="Turquoise Radar", lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=str(ROOT / "static")), name="static")

    @app.middleware("http")
    async def private_mutations(request, call_next):
        # Browser same-origin guard for unauthenticated local/private deployment.
        # This is not authentication; the server must not be publicly exposed.
        origin = request.headers.get("origin")
        expected = str(request.base_url).rstrip("/")
        if request.method == "POST" and (
            request.headers.get("sec-fetch-site") == "cross-site" or (origin and origin != expected)
        ):
            return HTMLResponse("Cross-origin mutation rejected", status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' https:; connect-src 'self'; frame-ancestors 'none'; "
            "base-uri 'self'; form-action 'self'"
        )
        return response

    def render(request, name, **context):
        return TEMPLATES.TemplateResponse(request=request, name=name, context=context)

    @app.get("/health")
    def health():
        with app.state.sessions() as s:
            s.execute(select(Setting).limit(1))
        return {"status": "ok", "destination": "99669", "mode": "private"}

    @app.get("/", response_class=HTMLResponse)
    def dashboard(request: Request):
        q = request.query_params
        try:
            minimum = Decimal(q["min_cost"]) if q.get("min_cost") else None
            maximum = Decimal(q["max_cost"]) if q.get("max_cost") else None
            if any(v is not None and (not v.is_finite() or v < 0) for v in (minimum, maximum)):
                raise ValueError("Cost filters must be finite and nonnegative")
        except (ValueError, InvalidOperation) as e:
            raise HTTPException(422, "Invalid cost filter") from e
        with app.state.sessions() as s:
            prefs = preferences(s)
            patterns = {p.id: p for p in attributed_patterns(s)}
            all_views = [view(row, prefs, patterns) for row in s.scalars(select(Listing))]
            sources = sorted({v["item"].source for v in all_views})
            results = []
            for v in all_views:
                i, a, p = v["item"], v["a"], v["pattern"]
                if q.get("source") and i.source != q["source"]:
                    continue
                if q.get("opportunity") and q["opportunity"] not in a.categories:
                    continue
                if q.get("confidence") and a.confidence != q["confidence"]:
                    continue
                if q.get("anomaly") and (not p or p.anomaly_category != q["anomaly"]):
                    continue
                if q.get("shipping") and i.shipping_status != q["shipping"]:
                    continue
                if minimum is not None and (a.landed is None or a.landed < minimum):
                    continue
                if maximum is not None and (a.landed is None or a.landed > maximum):
                    continue
                results.append(v)
            sort = q.get("sort", "priority")
            if sort == "margin":
                results.sort(
                    key=lambda v: v["a"].low.profit
                    if v["a"].low and v["a"].low.profit is not None
                    else Decimal("-Infinity"),
                    reverse=True,
                )
            elif sort == "recent":
                results.sort(key=lambda v: v["row"].id, reverse=True)
            else:
                results.sort(key=lambda v: (-v["a"].score, v["row"].id))
            top_patterns = sorted(
                (p for p in patterns.values() if p.active), key=learned_priority, reverse=True
            )[:5]
            context = dict(
                listings=results,
                total=len(all_views),
                sources=sources,
                categories=CATEGORIES,
                shipping_options=list(Shipping),
                q=q,
                anomaly_classes=sorted({p.anomaly_category for p in patterns.values()}),
                top_patterns=top_patterns,
                review_count=sum(v["a"].tier == "RESEARCH NEEDED" for v in all_views),
                candidate_count=sum(v["a"].tier == "BUY CANDIDATE" for v in all_views),
            )
            return render(
                request,
                "results.html" if request.headers.get("HX-Request") else "dashboard.html",
                **context,
            )

    @app.get("/listings/{listing_id}", response_class=HTMLResponse)
    def detail(request: Request, listing_id: int):
        with app.state.sessions() as s:
            row = s.get(Listing, listing_id)
            if not row:
                raise HTTPException(404, "Listing not found")
            patterns = {p.id: p for p in attributed_patterns(s)}
            feedback = list(
                s.scalars(
                    select(Feedback)
                    .where(Feedback.listing_id == listing_id)
                    .order_by(Feedback.id.desc())
                )
            )
            item = ListingInput.model_validate(row.data)
            related = [
                r
                for r in s.scalars(select(Listing))
                if r.id != row.id
                and item.seller_id
                and r.data.get("seller_id") == item.seller_id
                and r.data["source"] == item.source
            ]
            return render(
                request,
                "detail.html",
                v=view(row, preferences(s), patterns),
                feedback=feedback,
                discovery_observations=discovery_history(s, listing_id),
                outcomes=list(
                    s.scalars(select(ComparableSale).where(ComparableSale.listing_id == listing_id))
                ),
                related=related,
                actions=ACTIONS,
                raw=json.dumps(row.original_data, indent=2),
                normalized=json.dumps(row.data, indent=2),
            )

    @app.get("/entry", response_class=HTMLResponse)
    def entry(request: Request):
        example = ListingInput(source="manual", raw_title="My listing").model_dump(mode="json")
        return render(request, "entry.html", example=json.dumps(example, indent=2))

    @app.post("/listings")
    async def manual(request: Request):
        form = await request.form()
        try:
            if form.get("payload"):
                raw = json.loads(form["payload"])
            else:
                raw = {k: v for k, v in form.items() if v != ""}
            item = ListingInput.model_validate(raw)
        except (ValueError, TypeError) as e:
            raise HTTPException(422, "Invalid listing: " + str(e)) from e
        with app.state.sessions.begin() as s:
            row, _ = upsert_listing(s, item, raw)
            return RedirectResponse(f"/listings/{row.id}", status_code=303)

    @app.post("/import")
    async def import_file(request: Request):
        form = await request.form()
        content = form.get("content", "")
        upload = form.get("file")
        if upload and getattr(upload, "filename", None):
            data = await upload.read(2_000_001)
            if len(data) > 2_000_000:
                raise HTTPException(413, "Maximum import size is 2MB")
            try:
                content = data.decode("utf-8-sig")
            except UnicodeError as e:
                raise HTTPException(422, "Use UTF-8 CSV or JSON") from e
        if len(content.encode()) > 2_000_000:
            raise HTTPException(413, "Maximum import size is 2MB")
        try:
            with app.state.sessions.begin() as s:
                result = import_rows(s, content, form.get("format", "json"))
        except (ValueError, TypeError) as e:
            raise HTTPException(422, str(e)) from e
        return render(
            request,
            "message.html",
            title="Import complete",
            message=f"{result['created']} new · {result['updated']} updated · "
            f"{result['rows']} rows processed",
        )

    @app.post("/listings/{listing_id}/feedback", response_class=HTMLResponse)
    async def feedback_route(request: Request, listing_id: int):
        form = await request.form()
        try:
            data = FeedbackInput.model_validate({k: v for k, v in form.items() if v != ""})
        except ValidationError as e:
            raise HTTPException(422, str(e)) from e
        with app.state.sessions.begin() as s:
            listing = s.get(Listing, listing_id)
            if not listing:
                raise HTTPException(404, "Listing not found")
            prior = list(s.scalars(select(Feedback).where(Feedback.listing_id == listing_id)))
            # Milestone counters apply once per listing. A repeated Sold edits profit by delta.
            pattern = s.get(SearchPattern, listing.data.get("search_pattern_id") or "")
            if pattern and listing.data.get("raw_search_phrase") == pattern.phrase:
                if data.action == "Interested" and not any(f.action == "Interested" for f in prior):
                    pattern.useful_matches += 1
                if data.action == "Purchased" and not any(f.action == "Purchased" for f in prior):
                    pattern.successful_purchases += 1
                if data.action == "Sold" and data.realized_profit is not None:
                    previous = next(
                        (
                            Decimal(f.realized_profit)
                            for f in reversed(prior)
                            if f.action == "Sold" and f.realized_profit is not None
                        ),
                        Decimal(0),
                    )
                    pattern.realized_profit = str(
                        Decimal(pattern.realized_profit) + data.realized_profit - previous
                    )
            if data.action in ("Purchased", "Sold"):
                listing.review_state = "PURCHASED"
            elif data.action in ("Passed", "False positive"):
                listing.review_state = "PASSED"
            elif data.action == "Interested":
                listing.review_state = "WATCH"
            s.add(
                Feedback(
                    listing_id=listing_id,
                    action=data.action,
                    note=data.note,
                    realized_profit=str(data.realized_profit)
                    if data.realized_profit is not None
                    else None,
                )
            )
        if request.headers.get("HX-Request"):
            return HTMLResponse("<p>Feedback recorded. Refresh to see history.</p>")
        return RedirectResponse(f"/listings/{listing_id}", status_code=303)

    @app.get("/patterns", response_class=HTMLResponse)
    def patterns_route(request: Request):
        with app.state.sessions() as s:
            rows = sorted(attributed_patterns(s), key=learned_priority, reverse=True)
            return render(
                request,
                "patterns.html",
                patterns=rows,
                queries=generate_queries(rows),
                priority=learned_priority,
            )

    @app.post("/patterns")
    async def pattern_add(request: Request):
        form = await request.form()
        try:
            raw = dict(form)
            raw["sources"] = [v.strip() for v in raw.get("sources", "").split(",") if v.strip()]
            data = PatternInput.model_validate(raw)
        except ValidationError as e:
            raise HTTPException(422, str(e)) from e
        with app.state.sessions.begin() as s:
            add_pattern(s, data)
        return RedirectResponse("/patterns", status_code=303)

    @app.post("/patterns/{pattern_id}/toggle")
    def pattern_toggle(pattern_id: str):
        with app.state.sessions.begin() as s:
            row = s.get(SearchPattern, pattern_id)
            if not row:
                raise HTTPException(404, "Pattern not found")
            row.active = not row.active
        return RedirectResponse("/patterns", status_code=303)

    @app.get("/settings", response_class=HTMLResponse)
    def settings(request: Request):
        with app.state.sessions() as s:
            return render(
                request,
                "settings.html",
                settings=json.dumps(preferences(s).model_dump(mode="json"), indent=2),
            )

    @app.post("/settings")
    async def save_settings(request: Request):
        form = await request.form()
        try:
            prefs = Preferences.model_validate_json(form.get("payload", ""))
        except ValidationError as e:
            raise HTTPException(422, str(e)) from e
        with app.state.sessions.begin() as s:
            row = s.get(Setting, 1)
            if not row:
                s.add(Setting(id=1, data=prefs.model_dump(mode="json")))
            else:
                row.data = prefs.model_dump(mode="json")
        return RedirectResponse("/settings", status_code=303)

    owner_routes(app, render, preferences, view)
    discovery_routes(app, render, preferences)
    return app


app = create_app()
