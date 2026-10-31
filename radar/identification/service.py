"""Private owner workflow; reference stores and frozen evaluations are read-only."""

import io
import json
import os
import uuid
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from PIL import Image, ImageStat
from sqlalchemy import select

from radar.analysis import analyze
from radar.db import (
    ComparableSale,
    Listing,
    MakerCase,
    MakerDecision,
    MakerPhoto,
    MakerRun,
    MakerScenario,
    Setting,
)
from radar.identification.ranking import SYMBOLS, maker_key, rank, signature
from radar.jewelry.images import clean_image, read_photo, write_photo
from radar.references.directory import connect, current_review, digest
from radar.references.library import local_file
from radar.references.research import available
from radar.references.similarity import Encoder, references, search
from radar.references.store import private_root
from radar.schemas import ListingInput, Preferences
from radar.sources import upsert_listing


@dataclass
class Config:
    directory: Path
    library: Path
    weights: Path
    images: Path

    def image_store(self):
        root = self.images.resolve()
        protected = [
            self.library.resolve(),
            self.directory.resolve(),
            Path("data/jri004-owner-baseline").resolve(),
        ]
        if any(root == p or root.is_relative_to(p) or p.is_relative_to(root) for p in protected):
            raise ValueError(
                "Owner photographs must be separate from reference and evaluation storage"
            )
        return private_root(root)

    @classmethod
    def for_engine(cls, engine):
        db = Path(engine.url.database or "data/radar.db")
        return cls(
            Path(os.getenv("JRI_DIRECTORY_DIR", "data/jri-directory")),
            Path(os.getenv("JRI_LIBRARY_DIR", "data/jri")),
            Path(os.getenv("JRI_MODEL_PATH", "data/jri-models/resnet18-f37072fd.pth")),
            Path(os.getenv("JRI_OWNER_IMAGE_DIR", str(db.parent / (db.stem + "-maker-images")))),
        )


def case_or_error(s, id_):
    row = s.get(MakerCase, id_)
    if not row:
        raise ValueError("Jewelry research case not found")
    return row


def latest_run(s, case_id):
    return s.scalar(
        select(MakerRun).where(MakerRun.case_id == case_id).order_by(MakerRun.id.desc())
    )


def decisions(s, case_id):
    return list(
        s.scalars(
            select(MakerDecision).where(MakerDecision.case_id == case_id).order_by(MakerDecision.id)
        )
    )


def accepted(s, case_id):
    choice = None
    for d in decisions(s, case_id):
        if d.action == "CONFIRM":
            choice = d
        elif d.action in {"CORRECT_MAKER", "CORRECT_TRANSCRIPTION"} or (
            choice and d.candidate_id == choice.candidate_id and d.action in {"REJECT", "RESEARCH"}
        ):
            choice = None
    return choice


def add_photo(s, cfg, case_id, raw, role, parent=None, box=None):
    case_or_error(s, case_id)
    if role not in {"JEWELRY", "HALLMARK"}:
        raise ValueError("Select jewelry view or hallmark view")
    photos = list(s.scalars(select(MakerPhoto).where(MakerPhoto.case_id == case_id)))
    if len(photos) >= 20:
        raise ValueError("A research case supports at most 20 photographs and crops")
    im = clean_image(raw)
    existing = next(
        (p for p in photos if p.content_hash == im.content_hash and p.role == role), None
    )
    if existing:
        return existing
    root = cfg.image_store()
    id_ = uuid.uuid4().hex
    write_photo(root, id_, im.data)
    p = MakerPhoto(
        id=id_,
        case_id=case_id,
        role=role,
        content_hash=im.content_hash,
        data=dict(
            original_hash=im.original_hash,
            width=im.width,
            height=im.height,
            parent=parent,
            box=box,
            reference_index_eligible=False,
            training=False,
        ),
    )
    s.add(p)
    s.flush()
    return p


def crop_photo(s, cfg, case_id, photo_id, box):
    photo = s.get(MakerPhoto, photo_id)
    if not photo or photo.case_id != case_id:
        raise ValueError("Photograph does not belong to this case")
    if len(box) != 4 or any(type(v) is not int for v in box):
        raise ValueError("Use four integer pixel coordinates")
    with Image.open(io.BytesIO(read_photo(cfg.images, photo))) as im:
        x1, y1, x2, y2 = box
        if not (
            0 <= x1 < x2 <= im.width
            and 0 <= y1 < y2 <= im.height
            and x2 - x1 >= 16
            and y2 - y1 >= 16
        ):
            raise ValueError("Crop must be at least 16 pixels and inside the photograph")
        out = io.BytesIO()
        im.crop(box).save(out, "PNG")
    return add_photo(s, cfg, case_id, out.getvalue(), "HALLMARK", parent=photo_id, box=box)


def new_case(s, title, details, listing_id=None, listing_url=""):
    title, details = title.strip(), details.strip()
    if not title or len(title) > 500 or len(details) > 10000:
        raise ValueError(
            "Give the piece a title (500 characters maximum) and bounded listing notes"
        )
    listing = s.get(Listing, listing_id) if listing_id else None
    if listing_id and not listing:
        raise ValueError("Workbench listing not found")
    # Pydantic validates the URL; it is retained as a link, never fetched.
    item = ListingInput(
        source="owner-research",
        raw_title=title,
        listing_url=listing_url or None,
        raw_description=details,
    )
    case = MakerCase(
        listing_id=listing_id,
        data=dict(
            title=title,
            details=details,
            listing_url=str(item.listing_url) if item.listing_url else None,
            synthetic=bool(listing and listing.data.get("synthetic")),
        ),
    )
    s.add(case)
    s.flush()
    return case


def link_workbench(s, case_id):
    case = case_or_error(s, case_id)
    if case.listing_id:
        return case.listing_id
    item = ListingInput(
        source="owner-research",
        source_listing_id=f"maker-case-{case.id}",
        raw_title=case.data["title"],
        raw_description=case.data["details"],
        listing_url=case.data.get("listing_url"),
        synthetic=case.data.get("synthetic", False),
    )
    row, _ = upsert_listing(s, item, item.model_dump(mode="json"))
    case.listing_id = row.id
    return row.id


def query_data(form):
    text = str(form.get("transcription", "")).strip()
    construction = str(form.get("construction", "")).strip()
    symbols = form.getlist("symbols")
    if (
        len(text) > 300
        or len(construction) > 2000
        or any(v not in SYMBOLS for v in symbols)
        or form.get("legibility", "PARTIAL") not in {"LEGIBLE", "PARTIAL", "UNREADABLE"}
    ):
        raise ValueError("Invalid or excessive hallmark observations")
    return dict(
        transcription=text,
        construction=construction,
        symbols=sorted(set(symbols)),
        legibility=form.get("legibility", "PARTIAL"),
    )


def feedback(s, case_id, query):
    subject = s.get(MakerCase, case_id)
    if not subject:
        return []
    allowed_cases = {
        c.id
        for c in s.scalars(select(MakerCase))
        if bool(c.data.get("synthetic")) == bool(subject.data.get("synthetic"))
    }
    key = signature(query)
    latest = {}
    choices = {}
    for d in s.scalars(select(MakerDecision).order_by(MakerDecision.id)):
        if d.case_id not in allowed_cases:
            continue
        if d.action == "CONFIRM":
            choices[d.case_id] = d.id
        elif d.action in {"CORRECT_MAKER", "CORRECT_TRANSCRIPTION"}:
            choices[d.case_id] = None
        if d.action not in {"CONFIRM", "REJECT", "RESEARCH"}:
            continue
        if d.case_id != case_id and (not key or key != d.data.get("signature")):
            continue
        latest[(d.case_id, d.candidate_id)] = dict(
            candidate_id=d.candidate_id,
            action=d.action,
            case_id=d.case_id,
            same_case=d.case_id == case_id,
            note=d.data["note"],
            basis="Owner research judgment, not independently authenticated",
            decision_id=d.id,
        )
    return [
        v
        for v in latest.values()
        if v["action"] != "CONFIRM" or choices.get(v["case_id"]) == v["decision_id"]
    ]


def run_search(s, cfg, case_id, query, photo_id=None, encoder=None):
    case_or_error(s, case_id)
    with connect(cfg.directory) as c:
        records = [r for r, _ in available(c)]
    if not records:
        raise ValueError(
            "No currently eligible references. Restore/review the private directory first."
        )
    visual, warnings = [], []
    photo = s.get(MakerPhoto, photo_id) if photo_id else None
    if photo_id and (not photo or photo.case_id != case_id or photo.role != "HALLMARK"):
        raise ValueError("Choose a hallmark photograph or crop from this case")
    if photo:
        raw = read_photo(cfg.images, photo)
        with Image.open(io.BytesIO(raw)) as im:
            contrast = ImageStat.Stat(im.convert("L")).stddev[0]
        if contrast < 8:
            warnings.append("This crop has too little contrast for a useful visual comparison.")
        else:
            try:
                encoder = encoder or Encoder(cfg.weights)
                visual = search(cfg.library, encoder, raw, top_k=12)["matches"]
            except (ValueError, OSError) as exc:
                warnings.append("Local visual matching unavailable: " + str(exc))
    else:
        warnings.append("Choose or crop a hallmark view for local visual comparison.")
    query = {**query, "photo_id": photo_id, "photo_hash": photo.content_hash if photo else None}
    result = rank(records, query, visual, feedback(s, case_id, query))
    # A manual maker correction is a separate owner lead; it never edits a source.
    correction = next(
        (d for d in reversed(decisions(s, case_id)) if d.action == "CORRECT_MAKER"), None
    )
    if correction:
        name = correction.data["maker"]
        key = digest(maker_key(name).encode())
        if not any(c["id"] == key for c in result["candidates"] + result["rejected"]):
            owner = dict(
                id=key,
                maker=name,
                strength="Owner-supplied lead",
                references=[],
                similarities=[correction.data["note"]],
                discrepancies=[
                    "Owner correction; no matching published reference established in this search."
                ],
                visual=[],
                authenticated=False,
                owner_feedback=[],
            )
            latest = next(
                (
                    d
                    for d in reversed(decisions(s, case_id))
                    if d.candidate_id == key and d.action in {"CONFIRM", "REJECT", "RESEARCH"}
                ),
                None,
            )
            if latest and latest.action == "REJECT":
                result["rejected"].append(owner)
            else:
                result["candidates"] = [owner, *result["candidates"]][:5]
    result["status"] = "CANDIDATES" if result["candidates"] else "UNKNOWN"
    result.update(
        query=query,
        warnings=warnings,
        reference_count=len(records),
        visual_reference_ids=[r["reference_id"] for r in visual],
    )
    row = MakerRun(case_id=case_id, data=result)
    s.add(row)
    s.flush()
    return row


def decide(s, case_id, run_id, candidate_id, action, note, value=""):
    case_or_error(s, case_id)
    if action not in {"CONFIRM", "REJECT", "RESEARCH", "CORRECT_TRANSCRIPTION", "CORRECT_MAKER"}:
        raise ValueError("Unknown owner action")
    note, value = note.strip(), value.strip()
    if len(note) > 2000 or len(value) > 300 or not note:
        raise ValueError("Add a short reason or correction note (maximum 2000 characters)")
    run = s.get(MakerRun, run_id) if run_id else None
    if not run or run.case_id != case_id or run.id != latest_run(s, case_id).id:
        raise ValueError("Review the latest search before recording a decision")
    candidate = next(
        (c for c in run.data["candidates"] + run.data["rejected"] if c["id"] == candidate_id), None
    )
    if action in {"CONFIRM", "REJECT", "RESEARCH"} and not candidate:
        raise ValueError("Candidate was not part of this search")
    if action.startswith("CORRECT_") and not value:
        raise ValueError("Supply the corrected reading or maker name")
    d = MakerDecision(
        case_id=case_id,
        run_id=run.id,
        candidate_id=candidate_id or None,
        action=action,
        data=dict(
            note=note,
            maker=value if action == "CORRECT_MAKER" else candidate["maker"] if candidate else "",
            value=value,
            signature=signature(run.data["query"]),
            independently_authenticated=False,
            training=False,
            source_claims_changed=False,
        ),
    )
    s.add(d)
    s.flush()
    return d


def reference_image(cfg, record_id, ref_id=""):
    """Resolve only a current eligible reference ID, never a caller-provided path/URL."""
    with connect(cfg.directory) as c:
        row = c.execute(
            "SELECT data,source FROM records WHERE id=? AND active=1", (record_id,)
        ).fetchone()
        if not row or not current_review(c, row["source"])[1]["facts_allowed"]:
            raise ValueError("Reference unavailable under current source review")
        r = json.loads(row["data"])
        if ref_id and ref_id in r.get("local_reference_ids", []):
            ref = next(
                (v for v in references(cfg.library, True) if v["reference_id"] == ref_id), None
            )
            if not ref:
                raise ValueError("Reference image is no longer eligible")
            raw = local_file(cfg.library, ref["path"]).read_bytes()
            if digest(raw) != ref["image_sha256"]:
                raise ValueError("Reference image hash failed")
            return raw
        if r.get("source_kind") == "MANUAL_BOOK" and r["permitted_use"].get("local_reference_use"):
            photos = r["citation"]["photos"]
            photo = (
                next((p for p in photos if p["sha256"] == ref_id), None) if ref_id else photos[0]
            )
            if not photo:
                raise ValueError("Unknown photographed page view")
            raw = local_file(cfg.directory, photo["private_path"]).read_bytes()
            if digest(raw) != photo["sha256"]:
                raise ValueError("Book photograph hash failed")
            return raw
    raise ValueError("This reference image must remain external")


def scenario(s, case_id, candidate_id, ids, prefs):
    case = case_or_error(s, case_id)
    run = latest_run(s, case_id)
    candidate = (
        next((c for c in run.data["candidates"] if c["id"] == candidate_id), None) if run else None
    )
    if not candidate or not case.listing_id:
        raise ValueError("Select a current candidate and link a workbench listing first")
    listing = s.get(Listing, case.listing_id)
    ids = sorted(set(ids))
    if not ids or len(ids) > 30:
        raise ValueError("Select 1–30 recorded comparable sales")
    rows = list(s.scalars(select(ComparableSale).where(ComparableSale.id.in_(ids))))
    if (
        len(rows) != len(ids)
        or any(r.data.get("synthetic") for r in rows)
        and not listing.data.get("synthetic")
    ):
        raise ValueError("Comparables must exist and synthetic sales cannot price real items")
    choice = accepted(s, case_id)
    owner_accepted = bool(choice and choice.candidate_id == candidate_id)
    prices = [Decimal(r.data["sale_price"]) for r in rows]
    data = {
        **listing.data,
        "resale_low": str(min(prices)),
        "resale_high": str(max(prices)),
        "resale_basis": "Owner-selected maker comparison scenario; sample may differ in condition.",
        "resale_confirmed": owner_accepted,
    }
    analysis = analyze(ListingInput.model_validate(data), prefs).model_dump(mode="json")
    row = MakerScenario(
        case_id=case_id,
        data=dict(
            candidate_id=candidate_id,
            maker=candidate["maker"],
            comparable_ids=ids,
            comparable_snapshots=[r.data for r in rows],
            listing_snapshot=listing.data,
            preferences_snapshot=prefs.model_dump(mode="json"),
            analysis=analysis,
            owner_accepted_at_creation=owner_accepted,
            decision_id=choice.id if owner_accepted else None,
            listing_financials_changed=False,
        ),
    )
    s.add(row)
    s.flush()
    return row


def scenario_status(s, case, row):
    choice = accepted(s, case.id)
    if not choice or choice.candidate_id != row.data["candidate_id"]:
        return "CONDITIONAL — maker attribution not accepted"
    listing = s.get(Listing, case.listing_id)
    if not listing or listing.data != row.data["listing_snapshot"]:
        return "RECALCULATE — workbench inputs have changed"
    if choice.id != row.data.get("decision_id"):
        return "RECALCULATE — owner attribution decision has changed"
    setting = s.get(Setting, 1)
    current = Preferences.model_validate(setting.data) if setting else Preferences()
    if current.model_dump(mode="json") != row.data.get("preferences_snapshot"):
        return "RECALCULATE — fee or buying preferences have changed"
    return "OWNER-ACCEPTED LIKELY MAKER — research scenario"


def workbench_context(s, listing_id):
    result = []
    for case in s.scalars(select(MakerCase).where(MakerCase.listing_id == listing_id)):
        row = s.scalar(
            select(MakerScenario)
            .where(MakerScenario.case_id == case.id)
            .order_by(MakerScenario.id.desc())
        )
        result.append(
            dict(
                case=case,
                choice=accepted(s, case.id),
                scenario=row,
                status=scenario_status(s, case, row) if row else None,
            )
        )
    return result
