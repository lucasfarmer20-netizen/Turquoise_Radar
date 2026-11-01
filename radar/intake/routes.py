import base64
import json
from pathlib import Path
from urllib.parse import urlencode

from fastapi import HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from radar.capture import suggest
from radar.db import (
    AlertImport,
    AlertInvoice,
    AlertMessage,
    AlertObservation,
    DiscoveredItem,
    IntakeSource,
    SavedSourceSearch,
)
from radar.intake.parsers import (
    MAX_BATCH_BYTES,
    MAX_MESSAGE_BYTES,
    MAX_MESSAGES,
    safe_link,
)
from radar.intake.registry import (
    STARTER_PHRASES,
    SourceDefinition,
    duplicate_auction_platform,
    seed_searches,
)
from radar.intake.service import (
    PaidCosts,
    confirm_paid_order,
    confirm_query,
    import_alerts,
    import_structured,
    observation_occurrences,
    promote_candidate,
    source_definition,
)
from radar.intake.views import auction_research, estate_groups, inbox_cards, search_status


def intake_routes(app, render, preferences):
    @app.get("/inbox")
    def inbox(request: Request):
        with app.state.sessions() as s:
            cards = inbox_cards(s, preferences(s))
            total = len(cards)
            if request.query_params.get("focus") == "auction":
                cards = auction_research(cards)
            if request.query_params.get("source"):
                cards = [c for c in cards if c["item"].source == request.query_params["source"]]
            if request.query_params.get("opportunity"):
                cards = [
                    c
                    for c in cards
                    if request.query_params["opportunity"] in c["leads"]
                    or request.query_params["opportunity"] in c["a"].categories
                ]
            if request.query_params.get("status"):
                cards = [c for c in cards if c["state"] == request.query_params["status"]]
            messages = list(
                s.scalars(select(AlertMessage).order_by(AlertMessage.id.desc()).limit(50))
            )
            batches = list(s.scalars(select(AlertImport).order_by(AlertImport.id.desc()).limit(10)))
            return render(
                request,
                "inbox.html",
                cards=cards,
                total=total,
                messages=messages,
                batches=batches,
                sources=sorted(set(s.scalars(select(DiscoveredItem.source)))),
                q=request.query_params,
                observations=sum(c["observations"] for c in inbox_cards(s, preferences(s))),
            )

    @app.get("/inbox/import")
    def import_page(request: Request):
        with app.state.sessions() as s:
            return render(
                request, "alert_import.html", sources=list(s.scalars(select(IntakeSource)))
            )

    @app.post("/inbox/import")
    async def upload(request: Request):
        form = await request.form()
        source_id = form.get("source_id", "")
        uploads = [f for f in form.getlist("files") if getattr(f, "filename", None)]
        if len(uploads) > MAX_MESSAGES:
            raise HTTPException(413, "Maximum 10 messages per import")
        raws = []
        for upload in uploads:
            if Path(upload.filename).suffix.lower() != ".eml":
                raise HTTPException(
                    422, "Upload .eml messages only; attachments are never executed"
                )
            raw = await upload.read(MAX_MESSAGE_BYTES + 1)
            if len(raw) > MAX_MESSAGE_BYTES:
                raise HTTPException(413, "Message exceeds 512000 bytes")
            raws.append(raw)
        texts = form.get("content", "")
        if texts:
            # Explicit separator splits owner-pasted messages, never listing cards.
            raws.extend(
                part.encode() for part in texts.split("\n===NEXT MESSAGE===\n") if part.strip()
            )
        if len(raws) > MAX_MESSAGES or sum(map(len, raws)) > MAX_BATCH_BYTES:
            raise HTTPException(413, "Import exceeds message count or 3MB size limit")
        try:
            with app.state.sessions.begin() as s:
                batch = import_alerts(s, source_id, raws, form.get("received_at") or None)
                batch_id = batch.id
        except (ValueError, TypeError, UnicodeError):
            raise HTTPException(
                422,
                "Invalid alert import: check source, size and timezone; no body logged.",
            ) from None
        return RedirectResponse(f"/inbox/imports/{batch_id}", status_code=303)

    @app.get("/inbox/imports/{batch_id}")
    def report(request: Request, batch_id: int):
        with app.state.sessions() as s:
            row = s.get(AlertImport, batch_id)
            if not row:
                raise HTTPException(404, "Import not found")
            return render(request, "alert_report.html", batch=row)

    @app.post("/inbox/import-data")
    async def structured(request: Request):
        form = await request.form()
        if len(form.get("content", "").encode()) > MAX_BATCH_BYTES:
            raise HTTPException(413, "Structured import exceeds size limit")
        try:
            with app.state.sessions.begin() as s:
                batch = import_structured(
                    s,
                    form.get("source_id"),
                    form.get("content", ""),
                    form.get("format", "json"),
                    form.get("verify_query") == "yes",
                )
                id_ = batch.id
        except (ValueError, TypeError, AttributeError):
            raise HTTPException(
                422,
                "Invalid structured import: check listing fields and selected source.",
            ) from None
        return RedirectResponse(f"/inbox/imports/{id_}", status_code=303)

    @app.post("/inbox/manual")
    async def manual(request: Request):
        form = await request.form()
        try:
            with app.state.sessions.begin() as s:
                _, definition = source_definition(s, form.get("source_id"))
                url = safe_link(form.get("listing_url", ""), definition)
                if not url:
                    raise ValueError("Unsafe source URL")
                item = suggest(form.get("content", ""), url, definition.name)
                item = item.model_copy(
                    update={
                        "source": definition.name,
                        "source_listing_id": form.get("source_listing_id") or None,
                    }
                )
                batch = import_structured(
                    s,
                    form.get("source_id"),
                    json.dumps([item.model_dump(mode="json")]),
                    "json",
                    False,
                )
                id_ = batch.id
        except (ValueError, TypeError):
            raise HTTPException(
                422, "Supply copied text and a safe direct source URL; the page will not be fetched"
            ) from None
        return RedirectResponse(f"/inbox/imports/{id_}", status_code=303)

    @app.get("/inbox/messages/{message_id}")
    def evidence(request: Request, message_id: int):
        with app.state.sessions() as s:
            message = s.get(AlertMessage, message_id)
            if not message:
                raise HTTPException(404, "Message not found")
            observations = [
                card
                for o in s.scalars(
                    select(AlertObservation).where(AlertObservation.message_id == message_id)
                )
                for card in observation_occurrences(o)
            ]
            return render(
                request,
                "alert_message.html",
                message=message,
                observations=observations,
                raw=base64.b64decode(message.raw_b64).decode("utf-8", "replace"),
            )

    @app.post("/inbox/observations/{observation_id}/confirm-query")
    async def verify(request: Request, observation_id: int):
        form = await request.form()
        if form.get("confirm") != "yes":
            raise HTTPException(422, "Explicit source-query evidence confirmation required")
        try:
            with app.state.sessions.begin() as s:
                confirm_query(s, observation_id, form.get("occurrence_id") or None)
                message_id = s.get(AlertObservation, observation_id).message_id
        except ValueError as e:
            raise HTTPException(422, str(e)) from None
        return RedirectResponse(f"/inbox/messages/{message_id}", status_code=303)

    @app.get("/inbox/candidates/{item_id}")
    def candidate(request: Request, item_id: int):
        with app.state.sessions() as s:
            cards = inbox_cards(s, preferences(s))
            card = next((c for c in cards if c["row"].id == item_id), None)
            if not card:
                raise HTTPException(404, "Candidate not found")
            return render(request, "inbox_candidate.html", c=card)

    @app.post("/inbox/candidates/{item_id}/review")
    def review(item_id: int):
        with app.state.sessions.begin() as s:
            try:
                row = promote_candidate(s, item_id)
            except ValueError:
                raise HTTPException(404, "Candidate not found") from None
            return RedirectResponse(f"/listings/{row.id}", status_code=303)

    @app.post("/inbox/candidates/{item_id}/status")
    async def status(request: Request, item_id: int):
        form = await request.form()
        status = form.get("status")
        if status not in ("NEW", "IRRELEVANT"):
            raise HTTPException(422, "Unknown inbox status")
        with app.state.sessions.begin() as s:
            row = s.get(DiscoveredItem, item_id)
            if not row:
                raise HTTPException(404, "Candidate not found")
            row.inbox_status = status
        return RedirectResponse("/inbox", status_code=303)

    @app.get("/sources")
    def sources(request: Request):
        with app.state.sessions() as s:
            return render(
                request, "source_registry.html", sources=list(s.scalars(select(IntakeSource)))
            )

    @app.post("/sources")
    async def add_source(request: Request):
        import re

        form = await request.form()
        id_ = form.get("source_id", "")
        try:
            if not re.fullmatch(r"custom-[a-z0-9-]{1,40}", id_):
                raise ValueError("Custom source ID")
            data = SourceDefinition(
                name=form.get("name", ""),
                domains=[d.strip() for d in form.get("domains", "").split(",") if d.strip()],
            )
        except ValueError:
            raise HTTPException(422, "Use a custom-name ID and public source domains") from None
        with app.state.sessions.begin() as s:
            if s.get(IntakeSource, id_) or any(
                r.data["name"].casefold() == data.name.casefold()
                for r in s.scalars(select(IntakeSource))
            ):
                raise HTTPException(422, "Source identity/name already exists")
            if duplicate_auction_platform(s, data):
                raise HTTPException(422, "This platform already has a source; retain its history")
            s.add(IntakeSource(id=id_, data=data.model_dump(mode="json")))
            s.flush()
            seed_searches(s, id_)
        return RedirectResponse("/sources", status_code=303)

    @app.post("/sources/{source_id}")
    async def save_source(request: Request, source_id: str):
        form = await request.form()
        try:
            definition = SourceDefinition.model_validate_json(form.get("payload", ""))
            if definition.sample_verified and form.get("confirm_sample") != "yes":
                raise ValueError("Confirm sample evidence")
        except ValueError:
            raise HTTPException(
                422,
                "Invalid source config: custom rules require permitted-sample verification.",
            ) from None
        with app.state.sessions.begin() as s:
            row = s.get(IntakeSource, source_id)
            if not row:
                raise HTTPException(404, "Source not found")
            if duplicate_auction_platform(s, definition, exclude_id=row.id):
                raise HTTPException(422, "This platform already has a source; retain its history")
            if any(
                other.id != row.id and other.data["name"].casefold() == definition.name.casefold()
                for other in s.scalars(select(IntakeSource))
            ):
                raise HTTPException(422, "Source name already belongs to another marketplace")
            if definition.name != row.data["name"] and row.last_successful_import is not None:
                raise HTTPException(
                    422,
                    "Keep imported source names stable; add a separate marketplace entry",
                )
            row.data = definition.model_dump(mode="json")
        return RedirectResponse("/sources", status_code=303)

    @app.get("/saved-searches")
    def searches(request: Request):
        with app.state.sessions() as s:
            sources = list(s.scalars(select(IntakeSource)))
            source = request.query_params.get("source", "shopgoodwill")
            if source not in {row.id for row in sources}:
                raise HTTPException(404, "Source not found")
            group = request.query_params.get("group", "")
            catalog = "all" if request.query_params.get("catalog") == "all" else "starter"
            statuses = [v for v in search_status(s) if v["saved"].source_id == source]
            total = len(statuses)
            groups = sorted({v["saved"].group_name for v in statuses})
            if group not in groups:
                group = ""
            if group:
                statuses = [v for v in statuses if v["saved"].group_name == group]
            if catalog == "starter":
                order = {phrase: i for i, phrase in enumerate(STARTER_PHRASES)}
                statuses = [v for v in statuses if v["saved"].phrase in order]
                statuses.sort(key=lambda v: order[v["saved"].phrase])
            return render(
                request,
                "saved_searches.html",
                statuses=statuses,
                sources=sources,
                source_filter=source,
                group_filter=group,
                catalog_filter=catalog,
                groups=groups,
                total=total,
                starter_phrases=STARTER_PHRASES,
            )

    @app.post("/saved-searches/{search_id}")
    async def save_search(request: Request, search_id: int):
        form = await request.form()
        with app.state.sessions.begin() as s:
            row = s.get(SavedSourceSearch, search_id)
            if not row:
                raise HTTPException(404, "Search not found")
            row.selected = form.get("selected") == "true"
            row.configured = form.get("configured") == "true"
            context = {
                "source": row.source_id,
                "group": form.get("group", ""),
                "catalog": "starter" if form.get("catalog") == "starter" else "all",
            }
        return RedirectResponse(
            f"/saved-searches?{urlencode(context)}#search-{search_id}", status_code=303
        )

    @app.get("/estate-groups")
    def groups(request: Request):
        with app.state.sessions() as s:
            cards = inbox_cards(s, preferences(s))
            return render(
                request,
                "estate_groups.html",
                groups=estate_groups(cards, list(s.scalars(select(AlertMessage)))),
                ungrouped=[
                    c
                    for c in cards
                    if not (c.get("owner_item") or c["item"]).seller_id
                    and not (c.get("owner_item") or c["item"]).sale_auction_id
                ],
            )

    @app.get("/invoices")
    def invoices(request: Request):
        with app.state.sessions() as s:
            return render(
                request,
                "invoices.html",
                invoices=list(s.scalars(select(AlertInvoice).order_by(AlertInvoice.id.desc()))),
            )

    @app.get("/invoices/{invoice_id}")
    def invoice(request: Request, invoice_id: int):
        with app.state.sessions() as s:
            row = s.get(AlertInvoice, invoice_id)
            if not row:
                raise HTTPException(404, "Invoice not found")
            items = [s.get(DiscoveredItem, id_) for id_ in row.data["item_ids"]]
            return render(
                request,
                "invoice.html",
                invoice=row,
                items=items,
                cost_fields=list(PaidCosts.model_fields)[:-1],
            )

    @app.post("/invoices/{invoice_id}/confirm")
    async def apply_receipt(request: Request, invoice_id: int):
        form = await request.form()
        if form.get("confirm_owned") != "yes":
            raise HTTPException(422, "Confirm ownership and inspect paid receipt evidence first")
        try:
            costs = PaidCosts.model_validate(
                {k: form[k] for k in PaidCosts.model_fields if k in form}
            )
            with app.state.sessions.begin() as s:
                row = confirm_paid_order(s, invoice_id, costs)
                id_ = row.id
        except ValueError as e:
            # Financial messages contain labels only, never full uploaded content.
            raise HTTPException(
                422,
                "Paid-cost check failed: inspect amounts, reconciliation and item count.",
            ) from e
        return RedirectResponse(f"/listings/{id_}", status_code=303)
