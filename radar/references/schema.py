"""Evidence is attributed per assertion, never promoted from a description to truth."""

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Classification = Literal["OPEN_ACCESS", "INTERNAL_FAIR_USE", "REFERENCE_ONLY", "BLOCKED"]
Origin = Literal[
    "MUSEUM",
    "GALLERY",
    "SELLER",
    "OWNER_INSPECTION",
    "OWNER_REPORTED_TEST",
    "INDEPENDENT_TEST",
    "MODEL_PREDICTION",
    "COMMUNITY_CATALOG",
    "REFERENCE_REVIEW",
]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Source(Strict):
    id: str = Field(pattern=r"^[a-z0-9_-]{1,64}$")
    name: str
    url: str
    terms_url: str
    reviewed_on: date
    classification: Classification
    access_method: Literal["PUBLIC_API", "REVIEWED_MANIFEST", "LINK_ONLY"]
    hosts: list[str] = Field(default_factory=list)
    enabled: bool = False
    restrictions: str
    rationale: str
    provenance: str
    contact_status: str = "NOT_CONTACTED"
    interval_seconds: float = Field(default=2, ge=1, le=3600)
    internal_use_only: bool = True
    rights_claim: str = "NOT_LEGALLY_CLEARED"

    @model_validator(mode="after")
    def eligible_review(self):
        if self.enabled:
            if self.classification not in {"OPEN_ACCESS", "INTERNAL_FAIR_USE"}:
                raise ValueError("Source classification excludes collection")
            if self.access_method == "LINK_ONLY" or not self.hosts or not self.rationale.strip():
                raise ValueError("Reviewed access method and rationale required")
        if (
            self.classification == "INTERNAL_FAIR_USE"
            and self.rights_claim != "NOT_LEGALLY_CLEARED"
        ):
            raise ValueError("Fair-use rationale is not a license or legal clearance")
        return self


class Assertion(Strict):
    field: str = Field(min_length=1, max_length=80)
    value: str = Field(min_length=1, max_length=12000)
    origin: Origin
    evidence_url: str | None = None
    evidence_photo_id: str | None = None
    confidence: Literal["UNKNOWN", "LOW", "MEDIUM", "HIGH"] = "UNKNOWN"
    status: Literal["ATTRIBUTED", "DOCUMENTED", "DISPUTED", "SUPERSEDED"] = "ATTRIBUTED"
    test_method: Literal["ACID", "XRF", "ASSAY", "OTHER", "UNKNOWN"] | None = None
    tester: str | None = None
    tested_on: date | None = None
    scope: str | None = None
    document_ref: str | None = None
    supersedes: int | None = None
    observable_from_photo: bool = False

    @model_validator(mode="after")
    def evidence(self):
        if self.status == "DOCUMENTED" and not (self.document_ref or self.evidence_photo_id):
            raise ValueError("Documented assertion requires supporting evidence")
        if self.origin == "MODEL_PREDICTION" and self.status == "DOCUMENTED":
            raise ValueError("Model predictions are not ground truth")
        if self.origin in {"OWNER_REPORTED_TEST", "INDEPENDENT_TEST"} and not self.test_method:
            raise ValueError("Record test method, including UNKNOWN")
        return self


class PhotoRef(Strict):
    url: str
    view: str = "UNSPECIFIED"
    license: str
    license_url: str
    credit: str = ""
    evidence: str = Field(min_length=1)


class Item(Strict):
    external_id: str = Field(min_length=1, max_length=300)
    page_url: str
    title: str = Field(min_length=1, max_length=4000)
    object_type: str = ""
    description: str = Field(default="", max_length=30000)
    artists: list[str] = Field(default_factory=list)
    photos: list[PhotoRef] = Field(default_factory=list, max_length=100)
    assertions: list[Assertion] = Field(default_factory=list, max_length=100)


class Mark(Strict):
    item_id: str
    photo_id: str | None = None
    transcription: str | None = None
    pictorial_description: str = ""
    legibility: Literal["LEGIBLE", "PARTIAL", "UNREADABLE", "REPORTED_ONLY"]
    candidate_makers: list[str] = Field(default_factory=list)
    variant_of: int | None = None
    origin: Origin
    evidence: str = Field(min_length=1)

    @model_validator(mode="after")
    def readable(self):
        if self.legibility == "UNREADABLE" and self.transcription:
            raise ValueError("Unreadable marks cannot be transcribed")
        if self.legibility != "REPORTED_ONLY" and not self.photo_id:
            raise ValueError("Visual marks require a linked crop or photo")
        return self


class Pair(Strict):
    left_item: str
    right_item: str
    relation: Literal["POSITIVE", "DIFFICULT_NEGATIVE", "UNCERTAIN"]
    task: Literal["MAKER_CANDIDATE", "MARK_VARIANT", "CONSTRUCTION", "MATERIAL_RESEARCH"]
    rationale: str = Field(min_length=1)
    evidence_refs: list[str] = Field(min_length=1)
    reviewer: str = Field(min_length=1)
