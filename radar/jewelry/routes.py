import json

from fastapi import HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy import select
from starlette.concurrency import run_in_threadpool
from starlette.responses import PlainTextResponse

from radar.db import (
    VisionBilling,
    VisionCase,
    VisionCorrection,
    VisionFinding,
    VisionPhoto,
    VisionPrice,
    VisionRun,
    VisionSubItem,
)
from radar.jewelry.budget import budget_state
from radar.jewelry.config import VisionConfig, image_root
from radar.jewelry.images import (
    MAX_BATCH_BYTES,
    MAX_FILE_BYTES,
    clean_image,
    private_path,
    read_photo,
)
from radar.jewelry.schemas import OWNER, Correction, PriceProfile
from radar.jewelry.service import (
    add_photos,
    analyze,
    correct_run,
    locked,
    open_case,
    owned_case,
    plan,
    reconcile_bill,
    subject_data,
)


class VisionBodyLimit:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if (
            scope["type"] != "http"
            or scope.get("method") != "POST"
            or not scope.get("path", "").startswith("/vision/")
        ):
            return await self.app(scope, receive, send)
        limit = MAX_BATCH_BYTES + 65536 if scope["path"].rstrip("/").endswith("/photos") else 65536
        parts = []
        size = 0
        while True:
            event = await receive()
            if event["type"] == "http.disconnect":
                return
            data = event.get("body", b"")
            size += len(data)
            if size > limit:
                return await PlainTextResponse(
                    "Vision request exceeds size limit", status_code=413
                )(scope, receive, send)
            parts.append(data)
            if not event.get("more_body", False):
                break

        async def replay():
            if parts:
                data = b"".join(parts)
                parts.clear()
                return {"type": "http.request", "body": data, "more_body": False}
            return await receive()

        return await self.app(scope, replay, send)


def vision_routes(app, render):
    def config():
        try:
            return (
                app.state.vision_config_factory()
                if hasattr(app.state, "vision_config_factory")
                else VisionConfig.from_env()
            )
        except ValueError:
            raise HTTPException(
                503, "Invalid vision environment configuration; check documented settings"
            ) from None

    def root():
        return image_root(app.state.engine)

    def run_or_404(s, run_id):
        run = s.get(VisionRun, run_id)
        if not run or run.owner_id != OWNER:
            raise HTTPException(404, "Analysis not found")
        return run

    @app.get("/vision")
    def dashboard(request: Request):
        cfg = config()
        with app.state.sessions() as s:
            cases = list(
                s.scalars(
                    select(VisionCase)
                    .where(VisionCase.owner_id == OWNER)
                    .order_by(VisionCase.id.desc())
                )
            )
            cards = []
            for case in cases:
                data, url = subject_data(s, case)
                latest = s.scalar(
                    select(VisionRun)
                    .where(VisionRun.case_id == case.id)
                    .order_by(VisionRun.id.desc())
                )
                cards.append(dict(case=case, title=data["raw_title"], url=url, latest=latest))
            return render(
                request,
                "vision_dashboard.html",
                cards=cards,
                budget=budget_state(s, cfg),
                quick=plan(s, cfg, "QUICK", "anthropic"),
                deep=plan(s, cfg, "DEEP", "anthropic"),
            )

    @app.get("/vision/for/{kind}/{target_id}")
    def open_review(kind: str, target_id: int):
        try:
            case_id = open_case(app.state.sessions, kind, target_id)
        except ValueError:
            raise HTTPException(404, "Listing or candidate not found") from None
        return RedirectResponse(f"/vision/cases/{case_id}", status_code=303)

    @app.get("/vision/cases/{case_id}")
    def case_page(request: Request, case_id: int):
        cfg = config()
        with app.state.sessions() as s:
            try:
                case = owned_case(s, case_id)
                data, url = subject_data(s, case)
            except ValueError:
                raise HTTPException(404, "Visual review not found") from None
            photos = list(
                s.scalars(
                    select(VisionPhoto)
                    .where(VisionPhoto.case_id == case_id, VisionPhoto.active)
                    .order_by(VisionPhoto.created_at, VisionPhoto.id)
                )
            )
            runs = list(
                s.scalars(
                    select(VisionRun)
                    .where(VisionRun.case_id == case_id)
                    .order_by(VisionRun.id.desc())
                )
            )
            selected_ids = [v for v in request.query_params.getlist("run") if v][:2]
            if selected_ids:
                chosen = [r for r in runs if str(r.id) in selected_ids]
                if len(chosen) != len(set(selected_ids)):
                    raise HTTPException(404, "Analysis does not belong to this visual review")
            else:
                chosen = runs[:2]
            analyses = []
            sub_items = []
            for run in runs:
                items = list(s.scalars(select(VisionSubItem).where(VisionSubItem.run_id == run.id)))
                sub_items.extend(items)
                if run in chosen:
                    analyses.append(
                        dict(
                            run=run,
                            photos_current=set(run.data["photo_ids"]) == {p.id for p in photos},
                            findings=list(
                                s.scalars(
                                    select(VisionFinding).where(VisionFinding.run_id == run.id)
                                )
                            ),
                            sub_items=items,
                            corrections=list(
                                s.scalars(
                                    select(VisionCorrection)
                                    .where(VisionCorrection.run_id == run.id)
                                    .order_by(VisionCorrection.id.desc())
                                )
                            ),
                            billing=list(
                                s.scalars(
                                    select(VisionBilling).where(VisionBilling.run_id == run.id)
                                )
                            ),
                        )
                    )
            differences = []
            if len(chosen) == 2 and all(r.data.get("result") for r in chosen):
                for field in ["classification", "hallmark_candidate", "visible_southwestern_style"]:
                    a, b = [r.data["result"][field] for r in chosen]
                    if a != b:
                        differences.append(dict(field=field, left=a, right=b))
                signals = [{f["signal"] for f in r.data["result"]["findings"]} for r in chosen]
                if signals[0] != signals[1]:
                    differences.append(
                        dict(
                            field="visual signals",
                            left=sorted(signals[0]),
                            right=sorted(signals[1]),
                        )
                    )
            return render(
                request,
                "vision_case.html",
                case=case,
                subject=data,
                subject_url=url,
                photos=photos,
                runs=runs,
                analyses=analyses,
                differences=differences,
                sub_items=sub_items,
                quick=plan(s, cfg, "QUICK", "anthropic"),
                deep=plan(s, cfg, "DEEP", "anthropic"),
                budget=budget_state(s, cfg),
            )

    @app.post("/vision/cases/{case_id}/photos")
    async def upload(request: Request, case_id: int):
        form = await request.form(max_files=4, max_fields=20)
        files = [f for f in form.getlist("files") if getattr(f, "filename", None)]
        roles = form.getlist("roles")
        if not 1 <= len(files) <= 4:
            raise HTTPException(422, "Upload one to four JPEG, PNG or WebP photos")
        cleaned = []
        try:
            for n, file in enumerate(files):
                data = await file.read(MAX_FILE_BYTES + 1)
                if len(data) > MAX_FILE_BYTES:
                    raise ValueError("Photo exceeds 10MB")
                clean = await run_in_threadpool(clean_image, data)
                cleaned.append((clean, roles[n] if n < len(roles) else "FRONT"))
            await run_in_threadpool(
                add_photos,
                app.state.sessions,
                root(),
                case_id,
                cleaned,
                form.get("replace") or None,
            )
        except (ValueError, OSError):
            raise HTTPException(
                422,
                (
                    "Photo rejected: check image bytes, dimensions, roles and four-pho"
                    "to limit. No upload content logged."
                ),
            ) from None
        return RedirectResponse(f"/vision/cases/{case_id}", status_code=303)

    @app.post("/vision/photos/{photo_id}/remove")
    def remove(photo_id: str):
        with app.state.sessions() as s:
            locked(s)
            photo = s.get(VisionPhoto, photo_id)
            if not photo:
                raise HTTPException(404, "Photo not found")
            try:
                owned_case(s, photo.case_id)
            except ValueError:
                raise HTTPException(404, "Photo not found") from None
            photo.active = False
            s.commit()
            return RedirectResponse(f"/vision/cases/{photo.case_id}", status_code=303)

    @app.get("/vision/photos/{photo_id}")
    def photo_file(photo_id: str):
        with app.state.sessions() as s:
            photo = s.get(VisionPhoto, photo_id)
            if not photo:
                raise HTTPException(404, "Photo not found")
            try:
                owned_case(s, photo.case_id)
                read_photo(root(), photo)
                path = private_path(root(), photo.id)
            except (ValueError, OSError):
                raise HTTPException(404, "Private photo unavailable") from None
        return FileResponse(
            path,
            media_type="image/png",
            headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
        )

    @app.post("/vision/cases/{case_id}/analyze")
    async def run_analysis(request: Request, case_id: int):
        form = await request.form()
        try:
            run_id, cached = await analyze(
                app.state.sessions,
                root(),
                case_id,
                form.getlist("photos"),
                config(),
                stage=form.get("stage", "QUICK"),
                provider=form.get("provider", "fixture"),
                initiate=form.get("initiate") == "yes",
                paid_confirm=form.get("paid_confirm") == "yes",
                deep_confirm=form.get("deep_confirm") == "yes",
                force=form.get("force") == "yes",
                force_confirm=form.get("force_confirm") == "yes",
                approved_amount=form.get("approved_amount"),
                approved_model=form.get("approved_model"),
                provider_factory=getattr(app.state, "vision_provider_factory", None),
            )
        except (ValueError, OSError):
            # Explicit known service errors contain no provider body/credential content.
            raise HTTPException(
                422,
                (
                    "Analysis not started: confirm selections, stage/rerun authorizati"
                    "on, live gates, verified pricing and budget. Review Vision settin"
                    "gs."
                ),
            ) from None
        return RedirectResponse(
            f"/vision/cases/{case_id}?run={run_id}&cached={'yes' if cached else 'no'}"
            "#vision-results",
            status_code=303,
        )

    @app.post("/vision/runs/{run_id}/correct")
    async def feedback(request: Request, run_id: int):
        form = await request.form()
        try:
            correction = Correction(
                action=form.get("action"),
                note=form.get("note", ""),
                finding_id=form.get("finding_id") or None,
                sub_item_id=form.get("sub_item_id") or None,
            )
            with app.state.sessions.begin() as s:
                correct_run(s, run_id, correction)
                case_id = run_or_404(s, run_id).case_id
        except ValueError:
            raise HTTPException(422, "Invalid correction or analysis reference") from None
        return RedirectResponse(
            f"/vision/cases/{case_id}?run={run_id}#vision-results", status_code=303
        )

    @app.get("/vision/settings")
    def settings(request: Request):
        cfg = config()
        example = dict(
            model=cfg.model,
            verified_on=None,
            source="https://platform.claude.com/docs/en/about-claude/pricing",
            conditions=(
                "Owner must verify model eligibility, full context ceiling and sta"
                "ndard synchronous rates; no tools, caching, batch or fast mode"
            ),
            model_access_verified=False,
            input_per_million=None,
            output_per_million=None,
            max_input_tokens=None,
            max_output_tokens=2048,
            standard_rates_through_input_limit=True,
        )
        with app.state.sessions() as s:
            return render(
                request,
                "vision_settings.html",
                provider=cfg.provider,
                enabled=cfg.live_enabled,
                key_present=bool(cfg.api_key.get_secret_value()),
                quick=plan(s, cfg, "QUICK", "anthropic"),
                deep=plan(s, cfg, "DEEP", "anthropic"),
                budget=budget_state(s, cfg),
                profiles=list(s.scalars(select(VisionPrice).order_by(VisionPrice.id.desc()))),
                example=json.dumps(example, indent=2),
            )

    @app.post("/vision/settings/pricing")
    async def prices(request: Request):
        form = await request.form()
        try:
            if form.get("confirm") != "yes":
                raise ValueError("Confirmation required")
            profile = PriceProfile.model_validate_json(form.get("payload", ""))
            from datetime import UTC, datetime

            if (
                not 0 <= (datetime.now(UTC).date() - profile.verified_on).days <= 30
                or not profile.model_access_verified
            ):
                raise ValueError("Current model eligibility and dated price verification required")
            with app.state.sessions.begin() as s:
                s.add(VisionPrice(model=profile.model, data=profile.model_dump(mode="json")))
        except ValueError:
            raise HTTPException(
                422,
                (
                    "Pricing not saved: supply current official evidence, rates, full "
                    "input-context ceiling and explicit verification. Never enter API "
                    "keys here."
                ),
            ) from None
        return RedirectResponse("/vision/settings", status_code=303)

    @app.post("/vision/runs/{run_id}/billing")
    async def billing(request: Request, run_id: int):
        form = await request.form()
        try:
            with app.state.sessions() as s:
                locked(s)
                reconcile_bill(
                    s,
                    run_id,
                    form.get("amount"),
                    form.get("evidence", ""),
                    form.get("confirm") == "yes",
                )
                case_id = run_or_404(s, run_id).case_id
                s.commit()
        except ValueError:
            raise HTTPException(
                422,
                (
                    "Billing reconciliation requires a finished request, explicit fina"
                    "l provider evidence and a valid amount"
                ),
            ) from None
        return RedirectResponse(f"/vision/cases/{case_id}?run={run_id}", status_code=303)
