import asyncio
import hashlib
import json
from datetime import datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import select

from radar.db import (
    DiscoveredItem,
    Listing,
    VisionBilling,
    VisionCase,
    VisionCorrection,
    VisionFinding,
    VisionPhoto,
    VisionRun,
    VisionSubItem,
)
from radar.jewelry.budget import budget_state, profile_for, reserve_amount, usage_cost
from radar.jewelry.images import private_path, read_photo, write_photo
from radar.jewelry.schemas import OWNER, PROMPT_VERSION, SCHEMA_VERSION, Correction, VisionResult
from radar.jewelry.scoring import visual_score
from radar.schemas import utcnow
from radar.vision import AnthropicVisionProvider, FixtureVisionProvider, VisionImage


def locked(session):
    session.connection().exec_driver_sql("BEGIN IMMEDIATE")


def owned_case(session, id_):
    case = session.get(VisionCase, id_)
    if not case or case.owner_id != OWNER:
        raise ValueError("Visual review not found")
    return case


def subject_data(session, case):
    listing = session.get(Listing, case.listing_id) if case.listing_id else None
    candidate = session.get(DiscoveredItem, case.candidate_id) if case.candidate_id else None
    if not listing and candidate and candidate.listing_id:
        listing = session.get(Listing, candidate.listing_id)
    if listing:
        return listing.data, f"/listings/{listing.id}"
    if candidate:
        return candidate.normalized, f"/inbox/candidates/{candidate.id}"
    raise ValueError("Original listing unavailable")


def provider_context(session, case, data):
    # Alert-normalized descriptions can contain private receipt/address evidence.
    # Do not transmit an original email body under the label "seller description".
    record = session.get(DiscoveredItem, case.candidate_id) if case.candidate_id else None
    if not record and case.listing_id:
        record = session.get(Listing, case.listing_id)
    email_derived = bool(record and record.original_data.get("alert_message_id"))
    return dict(
        title=data["raw_title"],
        seller_description="" if email_derived else data.get("raw_description", "")[:12000],
    )


def open_case(sessions, kind, target_id):
    with sessions() as s:
        locked(s)
        if kind == "listing":
            if not s.get(Listing, target_id):
                raise ValueError("Listing not found")
            case = s.scalar(select(VisionCase).where(VisionCase.listing_id == target_id))
            if not case:
                ids = list(
                    s.scalars(
                        select(DiscoveredItem.id).where(DiscoveredItem.listing_id == target_id)
                    )
                )
                case = (
                    s.scalar(select(VisionCase).where(VisionCase.candidate_id.in_(ids)))
                    if ids
                    else None
                )
                if case:
                    case.listing_id = target_id
            if not case:
                case = VisionCase(listing_id=target_id)
        elif kind == "candidate":
            candidate = s.get(DiscoveredItem, target_id)
            if not candidate:
                raise ValueError("Candidate not found")
            case = s.scalar(select(VisionCase).where(VisionCase.candidate_id == target_id))
            if not case and candidate.listing_id:
                case = s.scalar(
                    select(VisionCase).where(VisionCase.listing_id == candidate.listing_id)
                )
            if not case:
                case = VisionCase(candidate_id=target_id, listing_id=candidate.listing_id)
        else:
            raise ValueError("Unknown visual review target")
        if case.owner_id not in (None, OWNER):
            raise ValueError("Visual review unavailable")
        s.add(case)
        s.commit()
        return case.id


def add_photos(sessions, root, case_id, images, replace=None):
    written = []
    try:
        with sessions() as s:
            locked(s)
            owned_case(s, case_id)
            if not 1 <= len(images) <= 4:
                raise ValueError("Upload one to four photos")
            if replace:
                old = s.get(VisionPhoto, replace)
                if not old or old.case_id != case_id or not old.active or len(images) != 1:
                    raise ValueError("Replacement needs one active photo from this review")
                old.active = False
            for clean, role in images:
                if role not in {"FRONT", "BACK_HALLMARK", "STONE", "SIDE_DETAIL"}:
                    raise ValueError("Unsupported photo role")
                row = s.scalar(
                    select(VisionPhoto).where(
                        VisionPhoto.case_id == case_id,
                        VisionPhoto.content_hash == clean.content_hash,
                        VisionPhoto.role == role,
                    )
                )
                if row:
                    read_photo(root, row)
                    row.active = True
                    continue
                id_ = uuid4().hex
                write_photo(root, id_, clean.data)
                written.append(id_)
                s.add(
                    VisionPhoto(
                        id=id_,
                        case_id=case_id,
                        role=role,
                        original_hash=clean.original_hash,
                        content_hash=clean.content_hash,
                        data=dict(
                            format="PNG",
                            original_format=clean.original_format,
                            original_width=clean.original_width,
                            original_height=clean.original_height,
                            width=clean.width,
                            height=clean.height,
                            bytes=len(clean.data),
                            metadata_removed=True,
                            processing_version="1",
                        ),
                    )
                )
            s.flush()
            if (
                len(
                    list(
                        s.scalars(
                            select(VisionPhoto).where(
                                VisionPhoto.case_id == case_id, VisionPhoto.active
                            )
                        )
                    )
                )
                > 4
            ):
                raise ValueError(
                    "Remove or replace a photo before exceeding four active photographs"
                )
            s.commit()
    except BaseException:
        for id_ in written:
            private_path(root, id_).unlink(missing_ok=True)
        raise


def plan(session, config, stage, provider):
    model = (
        (config.deep_model if stage == "DEEP" else config.model)
        if provider == "anthropic"
        else f"fixture-{stage.lower()}-v1"
    )
    profile = profile_for(session, model) if provider == "anthropic" else None
    reserve = reserve_amount(profile) if profile else Decimal(0)
    reasons = []
    if provider == "anthropic":
        if config.pilot_one_shot:
            # The owner can authorize one Haiku attempt while general live mode stays off.
            # analyze() checks this under BEGIN IMMEDIATE before reserving the run.
            if config.provider != "fixture" or config.live_enabled:
                reasons.append("One-request pilot requires fixture mode and general live disabled")
            if stage != "QUICK" or model != "claude-haiku-5-5":
                reasons.append(
                    "One-request pilot permits only Haiku quick analysis; Sonnet is disabled"
                )
            used = session.scalar(
                select(VisionRun.id)
                .where(VisionRun.owner_id == OWNER, VisionRun.provider == "anthropic")
                .limit(1)
            )
            if used is not None:
                reasons.append(
                    "One-request pilot already consumed; no further paid attempts allowed"
                )
        elif config.provider != "anthropic" or not config.live_enabled:
            reasons.append(
                "Paid vision is disabled; select fixture or explicitly configure live mode"
            )
        if not config.api_key.get_secret_value():
            reasons.append("Secure Anthropic API key is not configured")
        if not profile:
            reasons.append(
                "Current model access/pricing/context ceiling needs dated owner verification"
            )
        budget = budget_state(session, config)
        if budget["daily_used"] >= config.daily_limit:
            reasons.append("Daily paid-analysis limit reached")
        if reserve > budget["remaining"] or not config.monthly_budget:
            reasons.append("Monthly budget cannot cover this conservative reservation")
    return dict(
        model=model,
        profile=profile,
        reservation=reserve,
        reasons=reasons,
        output_limit=profile.max_output_tokens if profile else 2048,
        pilot_one_shot=config.pilot_one_shot,
    )


def cache_identity(case, provider, stage, plan_, photos, context):
    data = dict(
        owner=OWNER,
        case=case.id,
        provider=provider,
        stage=stage,
        model=plan_["model"],
        prompt=PROMPT_VERSION,
        schema=SCHEMA_VERSION,
        context=context,
        images=[dict(id=p.id, hash=p.content_hash, role=p.role) for p in photos],
        output_limit=plan_["output_limit"],
    )
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


async def analyze(
    sessions,
    root,
    case_id,
    photo_ids,
    config,
    *,
    stage="QUICK",
    provider="fixture",
    initiate=False,
    paid_confirm=False,
    deep_confirm=False,
    force=False,
    force_confirm=False,
    approved_amount=None,
    approved_model=None,
    provider_factory=None,
):
    if not initiate:
        raise ValueError("Owner initiation is required")
    if stage not in {"QUICK", "DEEP"} or provider not in {"fixture", "anthropic"}:
        raise ValueError("Unsupported stage/provider")
    if stage == "DEEP" and not deep_confirm:
        raise ValueError("Deeper investigation requires separate explicit confirmation")
    if force and not force_confirm:
        raise ValueError("Forced rerun requires explicit confirmation")
    if not 1 <= len(photo_ids) <= 4 or len(set(photo_ids)) != len(photo_ids):
        raise ValueError("Select one to four independent photographs")
    with sessions() as s:
        locked(s)
        case = owned_case(s, case_id)
        photos = [s.get(VisionPhoto, id_) for id_ in photo_ids]
        if any(not p or p.case_id != case_id or not p.active for p in photos):
            raise ValueError("Select active photos belonging to this review")
        data, _ = subject_data(s, case)
        context = provider_context(s, case, data)
        secret = config.api_key.get_secret_value()
        if secret and secret in json.dumps(context):
            raise ValueError("Sensitive context cannot be submitted or stored in an analysis")
        quote = plan(s, config, stage, provider)
        key = cache_identity(case, provider, stage, quote, photos, context)
        if not force:
            cached = s.scalar(
                select(VisionRun)
                .where(VisionRun.cache_key == key, VisionRun.status == "SUCCESS")
                .order_by(VisionRun.id.desc())
            )
            if cached:
                return cached.id, True
        pending = s.scalar(select(VisionRun).where(VisionRun.active_key == key))
        if pending:
            return pending.id, True
        if provider == "anthropic":
            if not paid_confirm:
                raise ValueError("Explicit paid analysis confirmation is required")
            if quote["reasons"]:
                raise ValueError("; ".join(quote["reasons"]))
            try:
                amount = Decimal(str(approved_amount))
                if (
                    not amount.is_finite()
                    or amount < quote["reservation"]
                    or approved_model != quote["model"]
                ):
                    raise ValueError()
            except (ValueError, ArithmeticError):
                raise ValueError(
                    "Model/reservation changed; review the current allowance before confirming"
                ) from None
        images = [VisionImage(p.id, p.role, read_photo(root, p)) for p in photos]
        prior = s.scalar(
            select(VisionRun).where(VisionRun.cache_key == key, VisionRun.status != "SUCCESS")
        )
        if prior and not force_confirm:
            raise ValueError("A prior failed or interrupted run exists; explicitly confirm a rerun")
        run = VisionRun(
            case_id=case_id,
            provider=provider,
            model=quote["model"],
            stage=stage,
            status="RESERVED",
            cache_key=key,
            active_key=key,
            reserved_usd=str(quote["reservation"]),
            accounted_usd=str(quote["reservation"]),
            cost_status="HELD_UNKNOWN" if provider == "anthropic" else "NOT_APPLICABLE",
            data=dict(
                prompt_version=PROMPT_VERSION,
                authorization_scope=(
                    "single_haiku_pilot"
                    if provider == "anthropic" and config.pilot_one_shot
                    else "standard_per_request"
                ),
                schema_version=SCHEMA_VERSION,
                photo_ids=[p.id for p in photos],
                image_hashes=[p.content_hash for p in photos],
                context=context,
                price_profile=quote["profile"].model_dump(mode="json")
                if quote["profile"]
                else None,
                usage={},
                result=None,
                score=None,
                error_code=None,
                lease_until=(utcnow() + timedelta(seconds=config.timeout + 30)).isoformat(),
            ),
        )
        s.add(run)
        s.commit()
        run_id = run.id
    with sessions.begin() as s:
        s.get(VisionRun, run_id).status = "SENT"
    try:
        if provider_factory:
            adapter = provider_factory(provider, config)
        else:
            adapter = (
                FixtureVisionProvider()
                if provider == "fixture"
                else AnthropicVisionProvider(config.api_key.get_secret_value(), config.timeout)
            )
        async with asyncio.timeout(config.timeout + 5):
            response = await adapter.inspect(
                images, context, model=quote["model"], stage=stage, max_tokens=quote["output_limit"]
            )
        result = VisionResult.model_validate(response.result)
        result.validate_images({p.id for p in images})
        payload = [
            result.model_dump(mode="json"),
            response.usage,
            response.request_id,
            response.reported_model,
        ]
        if secret and secret in json.dumps(payload):
            raise ValueError("Provider output contains sensitive content")
        estimated = (
            usage_cost(response.usage, quote["profile"]) if provider == "anthropic" else None
        )
        if estimated is not None and estimated > quote["reservation"]:
            estimated = None
        if response.reported_model and response.reported_model != quote["model"]:
            estimated = None  # A different returned model needs its own pricing verification.
        with sessions() as s:
            locked(s)
            run = s.get(VisionRun, run_id)
            run.data = {
                **run.data,
                "result": result.model_dump(mode="json"),
                "score": visual_score(result),
                "usage": response.usage,
                "request_id": response.request_id,
                "reported_model": response.reported_model,
            }
            run.status = "SUCCESS"
            run.active_key = None
            run.completed_at = utcnow()
            if estimated is not None:
                run.estimated_usd = str(estimated)
                run.accounted_usd = str(estimated)
                run.cost_status = "ESTIMATED"
            for finding in result.findings:
                s.add(VisionFinding(run_id=run.id, data=finding.model_dump(mode="json")))
            for item in result.sub_items:
                s.add(VisionSubItem(run_id=run.id, data=item.model_dump(mode="json")))
            s.commit()
    except BaseException as error:
        # A timeout/interruption does not prove no provider charge. Hold the allowance.
        name = type(error).__name__
        code = (
            "INTERRUPTED"
            if isinstance(error, asyncio.CancelledError)
            else "TIMEOUT"
            if "Timeout" in name
            else "RATE_LIMIT"
            if "RateLimit" in name
            else "INVALID_RESULT"
            if isinstance(error, ValueError)
            else "PROVIDER_FAILURE"
        )
        with sessions.begin() as s:
            run = s.get(VisionRun, run_id)
            run.status = "FAILED"
            run.active_key = None
            run.completed_at = utcnow()
            run.data = {**run.data, "error_code": code}
        if isinstance(error, (asyncio.CancelledError, KeyboardInterrupt, SystemExit)):
            raise
    return run_id, False


def correct_run(session, run_id, correction: Correction):
    run = session.get(VisionRun, run_id)
    if not run or run.owner_id != OWNER or run.status != "SUCCESS":
        raise ValueError("Completed analysis not found")
    for model, id_ in [
        (VisionFinding, correction.finding_id),
        (VisionSubItem, correction.sub_item_id),
    ]:
        if id_:
            row = session.get(model, id_)
            if not row or row.run_id != run_id:
                raise ValueError("Correction target belongs to another analysis")
    session.add(VisionCorrection(run_id=run_id, data=correction.model_dump(mode="json")))


def reconcile_bill(session, run_id, amount, evidence, confirmed):
    if not confirmed or not evidence or len(evidence) > 2000:
        raise ValueError("Confirm final provider billing with evidence, never an assumption")
    try:
        amount = Decimal(amount)
        if (
            not amount.is_finite()
            or amount < 0
            or amount > 10000
            or amount.as_tuple().exponent < -6
        ):
            raise ValueError()
    except (ArithmeticError, ValueError, TypeError):
        raise ValueError("Invalid confirmed USD amount") from None
    run = session.get(VisionRun, run_id)
    if not run or run.owner_id != OWNER or run.provider != "anthropic":
        raise ValueError("Paid analysis not found")
    if run.status in {"SENT", "RESERVED"}:
        deadline = run.data.get("lease_until")
        if not deadline or utcnow() < datetime.fromisoformat(deadline):
            raise ValueError("In-flight request cannot be released")
        # The bounded request lifetime has elapsed. Explicit final provider billing
        # confirmation is still required; expiration alone never frees spending.
        run.status = "INTERRUPTED_RECONCILED"
        run.active_key = None
        run.completed_at = utcnow()
    session.add(VisionBilling(run_id=run_id, amount_usd=str(amount), evidence=evidence))
    run.confirmed_usd = str(amount)
    run.accounted_usd = str(amount)
    run.cost_status = "OWNER_CONFIRMED_BILLING"
