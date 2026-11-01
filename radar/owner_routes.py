"""Owner-operated capture, evidence editing, review queue and comparable workflows."""

import json
from decimal import Decimal

from fastapi import HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from radar.anomalies import match_pattern
from radar.capture import suggest
from radar.comparables import add_comparables, summaries
from radar.db import ComparableSale, Listing, ListingEdit, SearchPattern, Setting
from radar.schemas import FeeProfile, ListingInput, Shipping, ValueStatus, utcnow
from radar.sources import dedup_key, upsert_listing
from radar.workflow import STATES, pattern_effectiveness

ACQUISITION = [
    "item_price",
    "buyer_premium",
    "buyer_service_fee",
    "sales_tax",
    "shipping_cost",
    "handling_fee",
]
EDITABLE = ACQUISITION + [
    "source_listing_id",
    "seller_id",
    "sale_auction_id",
    "shipping_status",
    "carrier",
    "combined_shipping_available",
    "combined_shipping_evidence",
    "condition",
    "stated_weight",
    "weight_unit",
    "weight_kind",
    "weight_evidence",
    "weight_verified",
    "material_evidence",
    "hallmark_text",
    "ring_size",
    "resale_low",
    "resale_high",
    "resale_basis",
    "buyer_profile",
    "resale_buyer_shipping",
    "resale_buyer_tax",
    "resale_shipping_status",
    "resale_tax_status",
    "resale_confirmed",
    "selling_category",
    "commission_tier",
    "raw_search_phrase",
    "original_search_phrase",
    "effective_query",
    "invoice_fee_allocation",
    "invoice_allocation_confirmed",
    "invoice_allocation_evidence",
]


def owner_routes(app, render, preferences, view):
    def context():
        return dict(
            cost_fields=ACQUISITION, statuses=list(ValueStatus), shipping_options=list(Shipping)
        )

    @app.get("/calibration")
    def fee_calibration(request: Request):
        with app.state.sessions() as s:
            prefs = preferences(s)
            return render(
                request,
                "calibration.html",
                profile=prefs.resale_profiles[prefs.resale_channel],
                profile_name=prefs.resale_channel,
                statuses=list(ValueStatus),
            )

    @app.post("/calibration")
    async def save_calibration(request: Request):
        form = await request.form()
        with app.state.sessions.begin() as s:
            prefs = preferences(s)
            old = prefs.resale_profiles[prefs.resale_channel]
            data = old.model_dump(mode="json")
            data.update(
                {
                    k: (v if v != "" else None)
                    for k, v in form.items()
                    if k
                    in (
                        "commission",
                        "processing",
                        "processing_fixed",
                        "packaging",
                        "outbound_shipping",
                        "source",
                        "as_of",
                        "verification_status",
                    )
                }
            )
            try:
                profile = FeeProfile.model_validate(data)
            except ValueError as e:
                raise HTTPException(422, str(e)) from e
            prefs.resale_profiles[prefs.resale_channel] = profile
            row = s.get(Setting, 1)
            if row:
                row.data = prefs.model_dump(mode="json")
            else:
                s.add(Setting(id=1, data=prefs.model_dump(mode="json")))
        return RedirectResponse("/calibration", status_code=303)

    @app.get("/capture")
    def capture(request: Request):
        return render(request, "capture.html")

    @app.post("/capture/preview")
    async def preview(request: Request):
        form = await request.form()
        try:
            item = suggest(
                form.get("pasted_text", ""),
                form.get("listing_url", ""),
                form.get("source", "manual") or "manual",
            )
        except ValueError as e:
            raise HTTPException(422, str(e)) from e
        return render(request, "capture_preview.html", item=item, **context())

    @app.post("/capture/save")
    async def capture_save(request: Request):
        form = await request.form()
        if form.get("owner_confirmed") != "yes":
            raise HTTPException(422, "Review and confirm extracted suggestions before saving")
        raw = {
            k: v
            for k, v in form.items()
            if k not in ("owner_confirmed",) and not k.endswith("_status")
        }
        # shipping_status is a domain field, not a monetary status selector.
        raw["shipping_status"] = form.get("shipping_status", "UNKNOWN")
        raw = {k: v for k, v in raw.items() if v != ""}
        if "material_terms" in raw:
            raw["material_terms"] = [
                t.strip() for t in raw["material_terms"].split(",") if t.strip()
            ]
        raw["cost_status"] = {
            k: form.get(k + "_status", "ESTIMATED") for k in ACQUISITION if k in raw
        }
        raw["capture_confirmed"] = True
        try:
            item = ListingInput.model_validate(raw)
        except ValueError as e:
            raise HTTPException(422, str(e)) from e
        with app.state.sessions.begin() as s:
            row, _ = upsert_listing(s, item, raw)
            if row.review_state == "NEW":
                row.review_state = "RESEARCH"
            return RedirectResponse(f"/listings/{row.id}", status_code=303)

    @app.get("/queue")
    def queue(request: Request):
        from radar.db import SearchPattern

        with app.state.sessions() as s:
            prefs = preferences(s)
            patterns = {p.id: p for p in s.scalars(select(SearchPattern))}
            rows = [
                view(r, prefs, patterns)
                for r in s.scalars(select(Listing).order_by(Listing.id.desc()))
            ]
            counts = {state: sum(v["state"] == state for v in rows) for state in STATES}
            filtered = [
                v
                for v in rows
                if not request.query_params.get("state")
                or v["state"] == request.query_params["state"]
            ]
            return render(request, "queue.html", listings=filtered, counts=counts, total=len(rows))

    @app.get("/listings/{listing_id}/edit")
    def edit(request: Request, listing_id: int):
        with app.state.sessions() as s:
            row = s.get(Listing, listing_id)
            if not row:
                raise HTTPException(404, "Listing not found")
            item = ListingInput.model_validate(row.data)
            comps = list(s.scalars(select(ComparableSale).order_by(ComparableSale.id.desc())))
            return render(
                request,
                "edit.html",
                row=row,
                item=item,
                comps=comps,
                summaries=summaries(comps),
                **context(),
            )

    @app.post("/listings/{listing_id}/edit")
    async def save_edit(request: Request, listing_id: int):
        form = await request.form()
        with app.state.sessions.begin() as s:
            row = s.get(Listing, listing_id)
            if not row:
                raise HTTPException(404, "Listing not found")
            before = dict(row.data)
            updates = {k: (v if v != "" else None) for k, v in form.items() if k in EDITABLE}
            for key in ("resale_confirmed", "weight_verified", "invoice_allocation_confirmed"):
                if key in form:
                    updates[key] = form[key] == "true"
            if "combined_shipping_available" in form:
                value = form["combined_shipping_available"]
                updates["combined_shipping_available"] = None if value == "" else value == "true"
            statuses = dict(before.get("cost_status", {}))
            statuses.update({k: form[k + "_status"] for k in ACQUISITION if k + "_status" in form})
            try:
                updated = ListingInput.model_validate(
                    {**before, **updates, "cost_status": statuses}
                )
            except ValueError as e:
                raise HTTPException(422, str(e)) from e
            if "raw_search_phrase" in updates:
                match = match_pattern(
                    updated.model_copy(update={"search_pattern_id": None}),
                    list(s.scalars(select(SearchPattern))),
                )
                updated = updated.model_copy(
                    update={"search_pattern_id": match.id if match else None}
                )
            key = dedup_key(updated)
            collision = s.scalar(
                select(Listing).where(Listing.dedup_key == key, Listing.id != row.id)
            )
            if collision:
                raise HTTPException(422, "That source identity already belongs to another listing")
            row.dedup_key = key
            row.data = updated.model_dump(mode="json")
            row.last_seen = utcnow()
            if row.review_state not in ("PURCHASED", "PASSED", "WATCH"):
                row.review_state = "RESEARCH"
            s.add(ListingEdit(listing_id=row.id, before_data=before, after_data=row.data))
        return RedirectResponse(f"/listings/{listing_id}", status_code=303)

    @app.post("/listings/{listing_id}/state")
    async def state(request: Request, listing_id: int):
        form = await request.form()
        target = form.get("state")
        if target not in ("NEW", "RESEARCH", "WATCH"):
            raise HTTPException(
                422, "Use feedback for Purchased/Passed; BUY CANDIDATE is calculated"
            )
        with app.state.sessions.begin() as s:
            row = s.get(Listing, listing_id)
            if not row:
                raise HTTPException(404, "Listing not found")
            row.review_state = target
        return RedirectResponse(f"/listings/{listing_id}", status_code=303)

    @app.get("/effectiveness")
    def effectiveness(request: Request):
        with app.state.sessions() as s:
            return render(request, "effectiveness.html", groups=pattern_effectiveness(s))

    @app.get("/comparables")
    def comparable_page(request: Request):
        with app.state.sessions() as s:
            rows = list(s.scalars(select(ComparableSale).order_by(ComparableSale.id.desc())))
            query = request.query_params.get("q", "").strip()[:300]
            if query:
                rows = [r for r in rows if query.casefold() in json.dumps(r.data).casefold()]
            return render(
                request, "comparables.html", rows=rows, summaries=summaries(rows), query=query
            )

    @app.post("/comparables")
    async def comparable_import(request: Request):
        form = await request.form()
        try:
            if form.get("content"):
                content = form["content"]
            else:
                data = {k: v for k, v in form.items() if v != "" and k not in ("format",)}
                content = json.dumps([data])
            if len(content.encode()) > 2_000_000:
                raise ValueError("Comparable import is limited to 2MB")
            with app.state.sessions.begin() as s:
                add_comparables(s, content, form.get("format", "json"))
        except (ValueError, TypeError, AttributeError) as e:
            raise HTTPException(422, str(e)) from e
        return RedirectResponse("/comparables", status_code=303)

    @app.post("/listings/{listing_id}/use-comparables")
    async def use_comparables(request: Request, listing_id: int):
        form = await request.form()
        if form.get("confirm") != "yes":
            raise HTTPException(
                422, "Owner confirmation is required before using comparable values"
            )
        try:
            ids = sorted({int(value) for value in form.getlist("comparable_id")})
        except ValueError as e:
            raise HTTPException(422, "Invalid comparable ID") from e
        if not ids:
            raise HTTPException(422, "Select comparable evidence")
        with app.state.sessions.begin() as s:
            listing = s.get(Listing, listing_id)
            if not listing:
                raise HTTPException(404, "Listing not found")
            rows = list(s.scalars(select(ComparableSale).where(ComparableSale.id.in_(ids))))
            if len(rows) != len(ids):
                raise HTTPException(422, "Comparable evidence not found")
            if any(r.data.get("synthetic", False) for r in rows) and not listing.data.get(
                "synthetic"
            ):
                raise HTTPException(422, "Synthetic comparables cannot price real listings")
            before = dict(listing.data)
            values = [Decimal(r.data["sale_price"]) for r in rows]
            update = dict(
                resale_low=str(min(values)),
                resale_high=str(max(values)),
                resale_confirmed=True,
                resale_comparable_ids=ids,
                resale_basis=f"Owner-confirmed comparable range; n={len(rows)}; "
                "uncertainty: selected sample, not an appraisal. Evidence: "
                + "; ".join(r.data["evidence"] for r in rows),
            )
            listing.data = ListingInput.model_validate({**before, **update}).model_dump(mode="json")
            if listing.review_state == "NEW":
                listing.review_state = "RESEARCH"
            s.add(ListingEdit(listing_id=listing_id, before_data=before, after_data=listing.data))
        return RedirectResponse(f"/listings/{listing_id}", status_code=303)
