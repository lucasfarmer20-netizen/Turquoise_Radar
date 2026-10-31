"""Private acquisition triage and explicit visual reviews; no network or authentication claims."""

import json
import re
from datetime import date
from typing import Literal

from pydantic import Field, model_validator

from radar.references.ingest import assertion, eligible, identity
from radar.references.schema import Assertion, Strict
from radar.references.store import audit, dump, get_source

TOPICS = {
    "BELL_TRADING": r"\b(?:bell trading|sun ?bell)\b",
    "SOUTHWESTERN": r"\bsouthwest(?:ern)?\b",
    "NATIVE_AMERICAN": r"\b(?:navajo|din[eé]|zuni|hopi|kewa|santo domingo|native american)\b",
    "WHITE_BUFFALO": r"\bwhite buffalo\b",
    "MEXICAN_TAXCO": r"\b(?:mexic(?:o|an)|taxco)\b",
    "ALPACA_GERMAN_SILVER": r"\b(?:alpaca|german silver|nickel silver)\b",
    "IMPORTED_ORIGIN": r"\bimported\b",
    "IMITATION_CLAIM": r"\b(?:imitation|replica|counterfeit)\b",
    "PLATED": r"\b(?:plated|silverplate|electroplat\w*)\b",
    "BASE_METAL": r"\b(?:base metal|brass|copper|nickel silver|german silver|alpaca)\b",
}
Topic = Literal[
    "BELL_TRADING",
    "SOUTHWESTERN",
    "NATIVE_AMERICAN",
    "TURQUOISE_CUFF",
    "WHITE_BUFFALO",
    "MEXICAN_TAXCO",
    "ALPACA_GERMAN_SILVER",
    "IMPORTED_ORIGIN",
    "IMITATION_CLAIM",
    "PLATED",
    "BASE_METAL",
    "MAKER",
    "TRADING_POST",
    "HALLMARK",
    "CONSTRUCTION",
    "DIMENSIONS",
    "WEIGHT",
    "MATERIAL",
]


class Finding(Strict):
    topic: Topic
    value: str = Field(min_length=1, max_length=1000)
    basis: Literal["SOURCE_CLAIM", "PHOTO_OBSERVATION", "INDEPENDENT_EVIDENCE"]
    evidence_url: str | None = None
    photo_ids: list[str] = Field(default_factory=list, max_length=100)
    assertion_ids: list[int] = Field(default_factory=list, max_length=30)

    @model_validator(mode="after")
    def references(self):
        if self.basis == "SOURCE_CLAIM" and not (self.evidence_url or self.assertion_ids):
            raise ValueError("Source findings require a catalog URL or assertion")
        if self.basis == "PHOTO_OBSERVATION" and not self.photo_ids:
            raise ValueError("Visual findings require reviewed photographs")
        if self.basis == "INDEPENDENT_EVIDENCE" and not self.assertion_ids:
            raise ValueError("Independent findings require documented evidence")
        return self


class ReferenceReview(Strict):
    schema_version: Literal["jri-review-1"] = "jri-review-1"
    item_id: str
    reviewer: str = Field(min_length=1, max_length=200)
    reviewed_on: date
    decision: Literal["KEEP", "HOLD", "REJECT"]
    image_quality: Literal["USABLE", "LIMITED", "UNUSABLE"]
    photo_ids: list[str] = Field(min_length=1, max_length=100)
    rationale: str = Field(min_length=1, max_length=2000)
    findings: list[Finding] = Field(default_factory=list, max_length=30)
    related_item_ids: list[str] = Field(default_factory=list, max_length=50)


def candidate_topics(item, assertions=()):
    """Catalog wording is a triage signal, including style claims, never authentication."""
    text = " ".join(
        [item.get("title", ""), item.get("object_type", ""), item.get("description", "")]
        + item.get("artists", [])
        + [a["value"] for a in assertions if a["origin"] != "REFERENCE_REVIEW"]
    )
    topics = [key for key, pattern in TOPICS.items() if re.search(pattern, text, re.I)]
    if re.search(r"\bturquoise\b", text, re.I) and re.search(r"\bcuff\b", text, re.I):
        topics.append("TURQUOISE_CUFF")
    return sorted(topics)


def review_photos(c, item_id):
    return {
        r[0]
        for r in c.execute(
            "SELECT id FROM assets WHERE item_id=? AND removed=0 UNION "
            "SELECT cr.id FROM crops cr JOIN assets a ON a.id=cr.asset_id "
            "WHERE a.item_id=? AND a.removed=0 AND cr.removed=0",
            (item_id, item_id),
        )
    }


def triage(c, source=None, limit=50):
    results = []
    for row in c.execute(
        "SELECT * FROM items WHERE excluded=0 AND (? IS NULL OR source=?) ORDER BY created_at,id",
        (source, source),
    ):
        if not eligible(get_source(c, row["source"])):
            continue
        item = json.loads(row["data"])
        evidence = [
            json.loads(r[0])
            for r in c.execute(
                "SELECT data FROM assertions WHERE item_id=? ORDER BY id", (row["id"],)
            )
        ]
        topics = candidate_topics(item, evidence)
        if not topics:
            continue
        photos = sorted(review_photos(c, row["id"]))
        results.append(
            {
                "item_id": row["id"],
                "source": row["source"],
                "external_id": row["external_id"],
                "title": item["title"],
                "candidate_topics": topics,
                "photo_ids": photos,
                "review_required": True,
                "authenticated": False,
            }
        )
        if len(results) >= min(50, max(1, limit)):
            break
    return results


def review_item(c, data):
    review = ReferenceReview.model_validate(data)
    row = c.execute("SELECT * FROM items WHERE id=? AND excluded=0", (review.item_id,)).fetchone()
    if not row or not eligible(get_source(c, row["source"])):
        raise ValueError("Review item must be eligible")
    item = json.loads(row["data"])
    photos = review_photos(c, review.item_id)
    if not set(review.photo_ids) <= photos:
        raise ValueError("Reviewed photos must belong to the item")
    superseded = {
        a["supersedes"]
        for row in c.execute("SELECT data FROM assertions WHERE item_id=?", (review.item_id,))
        if (a := json.loads(row[0])).get("supersedes")
    }
    for finding in review.findings:
        if not set(finding.photo_ids) <= set(review.photo_ids):
            raise ValueError("Findings must refer to reviewed photos")
        refs = []
        for id_ in finding.assertion_ids:
            a = c.execute("SELECT * FROM assertions WHERE id=?", (id_,)).fetchone()
            if not a or a["item_id"] != review.item_id:
                raise ValueError("Finding evidence must belong to the item")
            if id_ in superseded or json.loads(a["data"])["status"] == "SUPERSEDED":
                raise ValueError("Finding evidence has been superseded")
            refs.append(json.loads(a["data"]))
        urls = {item["page_url"]} | {a["evidence_url"] for a in refs if a.get("evidence_url")}
        if finding.evidence_url and finding.evidence_url not in urls:
            raise ValueError("Finding URL must identify retained source evidence")
        if finding.basis == "INDEPENDENT_EVIDENCE" and any(
            a["status"] != "DOCUMENTED"
            or a["origin"] not in {"OWNER_INSPECTION", "OWNER_REPORTED_TEST", "INDEPENDENT_TEST"}
            for a in refs
        ):
            raise ValueError("A gallery/museum attribution is not independent evidence")
    related = set(review.related_item_ids) | {review.item_id}
    for id_ in related:
        if not c.execute("SELECT 1 FROM items WHERE id=?", (id_,)).fetchone():
            raise ValueError("Related item missing")
    if len(related) > 1:
        # Retain a shared key across the whole relationship component, including
        # an excluded intermediate item, so its removal cannot separate endpoints.
        groups = {}
        for row in c.execute("SELECT item_id,data FROM assertions"):
            a = json.loads(row["data"])
            if a["field"] == "split_group" and a["origin"] == "REFERENCE_REVIEW":
                groups.setdefault(a["value"], set()).add(row["item_id"])
        while True:
            expanded = related | set().union(*(g for g in groups.values() if g & related))
            if expanded == related:
                break
            related = expanded
    value = dump(review.model_dump(mode="json"))
    evidence = Assertion(field="reference_review", value=value, origin="REFERENCE_REVIEW")
    previous = next(
        (
            row
            for row in c.execute(
                "SELECT id,data FROM assertions WHERE item_id=? ORDER BY id DESC", (review.item_id,)
            )
            if json.loads(row["data"])["field"] == "reference_review"
            and json.loads(row["data"])["origin"] == "REFERENCE_REVIEW"
        ),
        None,
    )
    unchanged = previous is not None and json.loads(previous["data"])["value"] == value
    if previous and not unchanged:
        evidence.supersedes = previous["id"]
    with c:
        if not unchanged:
            assertion(c, review.item_id, evidence)
        if len(related) > 1:
            # All retained relationships union transitively at export, even in item mode.
            group = "reviewed-relationship:" + identity(sorted(related))
            for id_ in sorted(related):
                assertion(
                    c, id_, Assertion(field="split_group", value=group, origin="REFERENCE_REVIEW")
                )
        audit(c, "REFERENCE_REVIEW", review.item_id)
    return {"review_saved": True, "authenticated": False}


def review_report(c):
    latest = {}
    superseded = {
        a["supersedes"]
        for row in c.execute("SELECT data FROM assertions")
        if (a := json.loads(row[0])).get("supersedes")
    }
    for row in c.execute(
        "SELECT a.item_id,a.data,i.source,i.excluded FROM assertions a "
        "JOIN items i ON i.id=a.item_id ORDER BY a.id"
    ):
        a = json.loads(row["data"])
        if a["field"] != "reference_review" or a["origin"] != "REFERENCE_REVIEW" or row["excluded"]:
            continue
        if not eligible(get_source(c, row["source"])):
            continue
        review = ReferenceReview.model_validate_json(a["value"])
        photos = review_photos(c, row["item_id"])
        references = {id_ for finding in review.findings for id_ in finding.assertion_ids}
        if set(review.photo_ids) <= photos and not references & superseded:
            latest[row["item_id"]] = review
        else:
            latest.pop(row["item_id"], None)
    counts = {"KEEP": 0, "HOLD": 0, "REJECT": 0}
    quality = {"USABLE": 0, "LIMITED": 0, "UNUSABLE": 0}
    topics = {}
    for review in latest.values():
        counts[review.decision] += 1
        quality[review.image_quality] += 1
        if review.decision != "KEEP" or review.image_quality == "UNUSABLE":
            continue
        for topic, basis in {(f.topic, f.basis) for f in review.findings}:
            topics.setdefault(topic, {}).setdefault(basis, 0)
            topics[topic][basis] += 1
    return {
        "reviewed_items": len(latest),
        "decisions": counts,
        "image_quality": quality,
        "kept_item_findings": topics,
        "gallery_attribution_is_not_authentication": True,
        "negative_candidates_are_not_verified_negatives": True,
        "training_ready": False,
    }
