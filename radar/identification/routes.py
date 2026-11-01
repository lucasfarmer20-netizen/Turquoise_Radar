"""Private local identification UI. Never fetch remote photos or invoke a provider."""

import json
from types import SimpleNamespace

from fastapi import HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import select
from starlette.concurrency import run_in_threadpool

from radar.db import ComparableSale, Listing, MakerCase, MakerPhoto, MakerRun, MakerScenario
from radar.identification import service as svc
from radar.identification.ranking import SYMBOLS, display_result
from radar.jewelry.images import MAX_BATCH_BYTES, MAX_FILE_BYTES, clean_image, read_photo
from radar.references.directory import source_registry, stats


class IdentificationBodyLimit:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if (
            scope["type"] != "http"
            or scope.get("method") != "POST"
            or not scope.get("path", "").startswith("/identify")
        ):
            return await self.app(scope, receive, send)
        upload = scope["path"] == "/identify/cases" or scope["path"].endswith("/photos")
        cap, chunks, size = (MAX_BATCH_BYTES + 65536 if upload else 65536), [], 0
        while True:
            event = await receive()
            if event["type"] == "http.disconnect":
                return
            size += len(event.get("body", b""))
            if size > cap:
                return await Response("Identification request too large", status_code=413)(
                    scope, receive, send
                )
            chunks.append(event.get("body", b""))
            if not event.get("more_body"):
                break

        async def replay():
            if chunks:
                body = b"".join(chunks)
                chunks.clear()
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        await self.app(scope, replay, send)


def identification_routes(app, render, preferences):
    def cfg():
        return getattr(app.state, "identification_config", None) or svc.Config.for_engine(
            app.state.engine
        )

    def error(exc):
        return HTTPException(422, str(exc))

    async def uploaded(form):
        files, size = [], 0
        for field, role in [("jewelry_photos", "JEWELRY"), ("hallmark_photos", "HALLMARK")]:
            for f in form.getlist(field):
                if not getattr(f, "filename", None):
                    continue
                raw = await f.read(MAX_FILE_BYTES + 1)
                size += len(raw)
                if len(files) >= 6 or size > MAX_BATCH_BYTES:
                    raise ValueError("Upload at most six photos, 40 MB total")
                clean_image(raw)
                files.append((raw, role))
        return files

    @app.get("/identify")
    def home(request: Request):
        problem, coverage, sources = None, None, []
        try:
            coverage, sources = stats(cfg().directory), source_registry(cfg().directory)
        except (ValueError, OSError) as exc:
            problem = "Private reference library unavailable. Restore the verified handoff. " + str(
                exc
            )
        with app.state.sessions() as s:
            listing = None
            if request.query_params.get("listing"):
                try:
                    listing = s.get(Listing, int(request.query_params["listing"]))
                except ValueError:
                    raise HTTPException(422, "Invalid listing") from None
            cases = list(s.scalars(select(MakerCase).order_by(MakerCase.id.desc()).limit(50)))
            return render(
                request,
                "identify_home.html",
                coverage=coverage,
                sources=sources,
                problem=problem,
                cases=cases,
                listing=listing,
            )

    @app.post("/identify/cases")
    async def create(request: Request):
        form = await request.form()
        try:
            # Reference verification is read-only; a missing directory is never initialized.
            stats(cfg().directory)
            files = await uploaded(form)
            with app.state.sessions.begin() as s:
                case = svc.new_case(
                    s,
                    str(form.get("title", "")),
                    str(form.get("details", "")),
                    int(form["listing_id"]) if form.get("listing_id") else None,
                    str(form.get("listing_url", "")),
                )
                for raw, role in files:
                    svc.add_photo(s, cfg(), case.id, raw, role)
                id_ = case.id
        except (ValueError, OSError) as exc:
            raise error(exc) from exc
        return RedirectResponse(f"/identify/cases/{id_}", status_code=303)

    @app.get("/identify/cases/{case_id}")
    def case_page(request: Request, case_id: int):
        with app.state.sessions() as s:
            try:
                case = svc.case_or_error(s, case_id)
            except ValueError as exc:
                raise HTTPException(404, str(exc)) from exc
            photos = list(
                s.scalars(
                    select(MakerPhoto)
                    .where(MakerPhoto.case_id == case_id)
                    .order_by(MakerPhoto.created_at, MakerPhoto.id)
                )
            )
            run = svc.latest_run(s, case_id)
            if run:
                run = SimpleNamespace(
                    id=run.id, created_at=run.created_at, data=display_result(run.data)
                )
            history = svc.decisions(s, case_id)
            query = dict(run.data["query"]) if run else {}
            hallmark_ids = [p.id for p in photos if p.role == "HALLMARK"]
            chosen_photo = request.query_params.get("photo")
            if chosen_photo in hallmark_ids:
                query["photo_id"] = chosen_photo
            elif not run and hallmark_ids:
                query["photo_id"] = hallmark_ids[-1]
            corrected = next(
                (d for d in reversed(history) if d.action == "CORRECT_TRANSCRIPTION"), None
            )
            if corrected and (not run or corrected.run_id == run.id):
                query["transcription"] = corrected.data["value"]
            statuses = {}
            for d in history:
                if d.candidate_id and d.action in {"CONFIRM", "REJECT", "RESEARCH"}:
                    statuses[d.candidate_id] = d.action
            scenarios = list(
                s.scalars(
                    select(MakerScenario)
                    .where(MakerScenario.case_id == case_id)
                    .order_by(MakerScenario.id.desc())
                    .limit(10)
                )
            )
            return render(
                request,
                "identify_case.html",
                case=case,
                photos=photos,
                run=run,
                query=query,
                symbols=SYMBOLS,
                history=history[::-1],
                statuses=statuses,
                choice=svc.accepted(s, case_id),
                listing=s.get(Listing, case.listing_id) if case.listing_id else None,
                comparables=list(
                    s.scalars(select(ComparableSale).order_by(ComparableSale.id.desc()))
                ),
                scenarios=[dict(row=r, status=svc.scenario_status(s, case, r)) for r in scenarios],
            )

    @app.post("/identify/cases/{case_id}/photos")
    async def photos(request: Request, case_id: int):
        try:
            files = await uploaded(await request.form())
            if not files:
                raise ValueError("Select a photograph")
            with app.state.sessions.begin() as s:
                for raw, role in files:
                    svc.add_photo(s, cfg(), case_id, raw, role)
        except (ValueError, OSError) as exc:
            raise error(exc) from exc
        return RedirectResponse(f"/identify/cases/{case_id}#photos", status_code=303)

    @app.get("/identify/cases/{case_id}/photos/{photo_id}")
    def photo(case_id: int, photo_id: str):
        with app.state.sessions() as s:
            p = s.get(MakerPhoto, photo_id)
            if not p or p.case_id != case_id:
                raise HTTPException(404, "Photograph not found")
            try:
                raw = read_photo(cfg().images, p)
            except (ValueError, OSError) as exc:
                raise HTTPException(404, "Photograph unavailable") from exc
        return Response(raw, media_type="image/png", headers={"Cache-Control": "private, no-store"})

    @app.post("/identify/cases/{case_id}/photos/{photo_id}/crop")
    async def crop(request: Request, case_id: int, photo_id: str):
        try:
            form = await request.form()
            with app.state.sessions.begin() as s:
                cropped = svc.crop_photo(
                    s, cfg(), case_id, photo_id, [int(form[k]) for k in ["x1", "y1", "x2", "y2"]]
                )
                crop_id = cropped.id
        except (ValueError, KeyError, OSError) as exc:
            raise error(exc) from exc
        return RedirectResponse(
            f"/identify/cases/{case_id}?photo={crop_id}#observations", status_code=303
        )

    @app.post("/identify/cases/{case_id}/search")
    async def lookup(request: Request, case_id: int):
        form = await request.form()

        def run():
            with app.state.sessions.begin() as s:
                return svc.run_search(
                    s,
                    cfg(),
                    case_id,
                    svc.query_data(form),
                    form.get("photo_id") or None,
                    getattr(app.state, "identification_encoder", None),
                ).id

        try:
            await run_in_threadpool(run)
        except (ValueError, OSError) as exc:
            raise error(exc) from exc
        return RedirectResponse(f"/identify/cases/{case_id}#candidates", status_code=303)

    @app.post("/identify/cases/{case_id}/decisions")
    async def decide(request: Request, case_id: int):
        form = await request.form()
        try:
            with app.state.sessions.begin() as s:
                svc.decide(
                    s,
                    case_id,
                    int(form["run_id"]),
                    form.get("candidate_id"),
                    str(form.get("action", "")),
                    str(form.get("note", "")),
                    str(form.get("value", "")),
                )
        except (ValueError, KeyError) as exc:
            raise error(exc) from exc
        return RedirectResponse(f"/identify/cases/{case_id}#candidates", status_code=303)

    @app.post("/identify/cases/{case_id}/workbench")
    def to_workbench(case_id: int):
        try:
            with app.state.sessions.begin() as s:
                listing = svc.link_workbench(s, case_id)
        except ValueError as exc:
            raise error(exc) from exc
        return RedirectResponse(f"/listings/{listing}", status_code=303)

    @app.get("/identify/reference/{record_id}")
    def reference(request: Request, record_id: str):
        try:
            raw = svc.reference_image(cfg(), record_id, request.query_params.get("view", ""))
            raw = clean_image(raw).data
        except (ValueError, OSError, KeyError) as exc:
            raise HTTPException(
                404, "Reference is external-only or unavailable under current rights"
            ) from exc
        return Response(raw, media_type="image/png", headers={"Cache-Control": "private, no-store"})

    @app.post("/identify/cases/{case_id}/scenarios")
    async def valuation(request: Request, case_id: int):
        form = await request.form()
        try:
            if form.get("selected_for_comparison") != "yes":
                raise ValueError("Confirm that you selected these sales for a comparison scenario")
            with app.state.sessions.begin() as s:
                svc.scenario(
                    s,
                    case_id,
                    str(form.get("candidate_id", "")),
                    [int(v) for v in form.getlist("comparable_id")],
                    preferences(s),
                )
        except ValueError as exc:
            raise error(exc) from exc
        return RedirectResponse(f"/identify/cases/{case_id}#value", status_code=303)

    @app.get("/identify/cases/{case_id}/export")
    def export(case_id: int):
        with app.state.sessions() as s:
            try:
                case = svc.case_or_error(s, case_id)
            except ValueError as exc:
                raise HTTPException(404, str(exc)) from exc
            result = dict(
                case=case.data,
                listing_id=case.listing_id,
                runs=[
                    dict(id=r.id, data=r.data)
                    for r in s.scalars(select(MakerRun).where(MakerRun.case_id == case_id))
                ],
                owner_decisions=[
                    dict(id=d.id, action=d.action, data=d.data, created_at=str(d.created_at))
                    for d in svc.decisions(s, case_id)
                ],
                training_eligible=False,
            )
        return Response(
            json.dumps(result, indent=2),
            media_type="application/json",
            headers={
                "Cache-Control": "private, no-store",
                "Content-Disposition": f'attachment; filename="maker-case-{case_id}.json"',
            },
        )
