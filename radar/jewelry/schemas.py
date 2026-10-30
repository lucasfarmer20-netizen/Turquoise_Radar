import re
from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

PROMPT_VERSION = "jewelry-1"
SCHEMA_VERSION = "1"
OWNER = "local-owner"
Role = Literal["FRONT", "BACK_HALLMARK", "STONE", "SIDE_DETAIL"]
Stage = Literal["QUICK", "DEEP"]
Kind = Literal[
    "RING",
    "CUFF",
    "BRACELET",
    "PENDANT",
    "EARRINGS",
    "NECKLACE",
    "BROOCH",
    "SQUASH_BLOSSOM_STYLE",
    "MIXED_LOT",
    "UNKNOWN",
]
Signal = Literal[
    "POSSIBLE_HALLMARK",
    "CONSTRUCTION",
    "CRAFTSMANSHIP",
    "MIXED_LOT_DETAIL",
    "OVERLOOKED_DETAIL",
    "PLATING_CONCERN",
    "DAMAGE",
    "POOR_IMAGE",
    "CONFLICT",
    "RESEARCH",
]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", revalidate_instances="always")


def guarded_text(value):
    # Deliberately conservative: these claims cannot enter even AI research evidence.
    # Literal hallmark transcriptions are separately bounded and never authenticate a piece.
    forbidden = (
        r"\d+(?:\.\d+)?\s*(?:grams?\b|g\b|dwt\b|ounces?\b|oz\b)",
        r"\$\s*\d|\bUSD\s*\d",
        r"\b\d+(?:\.\d+)?\s*(?:dollars?|USD)\b",
        r"\b(?:Navajo|Hopi|Zuni|tribal|Indigenous|Native American)\b",
        r"\b(?:natural|untreated|stabilized)\s+turquoise\b",
        r"\b(?:mine origin|mined at|authentic vintage|verified sterling|confirmed purity)\b",
        r"\b(?:definitely|certainly|authenticated|guaranteed)\b",
        r"\b(?:is|confirmed|verified|solid|pure|genuine)\s+(?:925\s+)?sterling\b",
        (
            "\\b(?:Kingman|Sleeping Beauty|Bisbee|Royston|Cerrillos|Carico Lake"
            "|Lander Blue)\\s+(?:turquoise|mine)\\b"
        ),
        r"\bmade by\b",
    )
    if any(re.search(pattern, value, re.I) for pattern in forbidden):
        raise ValueError("Unsupported authentication, weight or valuation claim")
    return value


class Finding(Strict):
    key: str = Field(pattern=r"^[A-Za-z0-9_-]{1,40}$")
    category: Literal["CLASSIFICATION", "METAL", "STONE", "CONSTRUCTION", "CONDITION", "RESEARCH"]
    description: str = Field(min_length=1, max_length=350)
    image_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    visible_evidence: str = Field(min_length=1, max_length=800)
    confidence: Literal["HIGH", "MEDIUM", "LOW", "UNKNOWN"]
    evidence_kind: Literal["DIRECT", "INFERRED"]
    uncertainty: str = Field(min_length=1, max_length=600)
    next_action: str | None = Field(default=None, max_length=400)
    signal: Signal
    legibility: Literal["LEGIBLE", "PARTIAL", "UNREADABLE", "NOT_APPLICABLE"] = "NOT_APPLICABLE"
    hallmark_transcription: str | None = Field(default=None, min_length=1, max_length=40)
    visible_fragments: list[str] = Field(default_factory=list, max_length=5)

    @field_validator("description", "visible_evidence", "uncertainty", "next_action")
    @classmethod
    def no_unsupported_claims(cls, value):
        return guarded_text(value) if value else value

    @model_validator(mode="after")
    def evidence_limits(self):
        if self.evidence_kind == "INFERRED" and self.confidence == "HIGH":
            raise ValueError("Inferred findings cannot have high confidence")
        if self.hallmark_transcription and (
            self.legibility != "LEGIBLE"
            or self.evidence_kind != "DIRECT"
            or self.category != "METAL"
        ):
            raise ValueError("Only directly legible metal marks can be transcribed")
        if self.hallmark_transcription and not re.fullmatch(
            r"[A-Za-z0-9 .&+/'-]{1,40}", self.hallmark_transcription
        ):
            raise ValueError("Unsupported hallmark text")
        if any(not re.fullmatch(r"[A-Za-z0-9? .-]{1,10}", f) for f in self.visible_fragments):
            raise ValueError("Fragments must be short visible characters, not completed marks")
        return self


class MakerCandidate(Strict):
    research_name: str = Field(min_length=1, max_length=100)
    supporting_finding_key: str
    uncertainty: str = Field(min_length=1, max_length=500)
    status: Literal["UNVERIFIED_RESEARCH_LEAD"] = "UNVERIFIED_RESEARCH_LEAD"

    @field_validator("research_name", "uncertainty")
    @classmethod
    def safe(cls, value):
        return guarded_text(value)


class SubItem(Strict):
    key: str = Field(pattern=r"^[A-Za-z0-9_-]{1,40}$")
    label: str = Field(min_length=1, max_length=150)
    image_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    location_hint: str = Field(min_length=1, max_length=250)
    finding_keys: list[str] = Field(min_length=1, max_length=8)
    uncertainty: str = Field(min_length=1, max_length=400)

    @field_validator("label", "location_hint", "uncertainty")
    @classmethod
    def safe(cls, value):
        return guarded_text(value)


class VisionResult(Strict):
    classification: Kind
    summary: str = Field(min_length=1, max_length=800)
    visible_southwestern_style: str = Field(max_length=500)
    hallmark_candidate: str = Field(max_length=500)
    possible_maker_identity: MakerCandidate | None = None
    documented_cultural_attribution: Literal["UNVERIFIED"] = "UNVERIFIED"
    material_verification: Literal["UNVERIFIED"] = "UNVERIFIED"
    independent_owner_evidence: Literal["NOT_ASSESSED"] = "NOT_ASSESSED"
    findings: list[Finding] = Field(default_factory=list, max_length=20)
    sub_items: list[SubItem] = Field(default_factory=list, max_length=12)

    @field_validator("summary", "visible_southwestern_style", "hallmark_candidate")
    @classmethod
    def safe(cls, value):
        return guarded_text(value)

    @model_validator(mode="after")
    def valid_references(self):
        keys = {f.key for f in self.findings}
        if len(keys) != len(self.findings) or len({s.key for s in self.sub_items}) != len(
            self.sub_items
        ):
            raise ValueError("Duplicate finding/sub-item keys")
        if any(set(s.finding_keys) - keys for s in self.sub_items):
            raise ValueError("Sub-item references an unknown finding")
        if self.possible_maker_identity:
            key = self.possible_maker_identity.supporting_finding_key
            f = next((f for f in self.findings if f.key == key), None)
            if not f or not f.hallmark_transcription:
                raise ValueError("Maker research requires a directly legible supporting mark")
            text = re.sub(r"STERLING|SILVER|925|\W|\d", "", f.hallmark_transcription, flags=re.I)
            if not text:
                raise ValueError("A generic purity stamp does not support a maker research name")
        return self

    def validate_images(self, ids):
        if any(f.image_id not in ids for f in [*self.findings, *self.sub_items]):
            raise ValueError("Finding references a photo outside this analysis")
        for item in self.sub_items:
            if not any(
                f.key in item.finding_keys and f.image_id == item.image_id for f in self.findings
            ):
                raise ValueError("Sub-item must reference evidence in its photograph")


class PriceProfile(Strict):
    model: str = Field(pattern=r"^[A-Za-z0-9._-]{1,120}$")
    verified_on: date
    source: str = Field(min_length=10, max_length=1000)
    conditions: str = Field(min_length=10, max_length=1500)
    model_access_verified: bool = False
    input_per_million: Decimal = Field(gt=0, le=10000, allow_inf_nan=False)
    output_per_million: Decimal = Field(gt=0, le=10000, allow_inf_nan=False)
    cache_read_per_million: Decimal | None = Field(default=None, ge=0, le=10000)
    cache_write_5m_per_million: Decimal | None = Field(default=None, ge=0, le=10000)
    cache_write_1h_per_million: Decimal | None = Field(default=None, ge=0, le=10000)
    max_input_tokens: int = Field(ge=100000, le=2000000)
    max_output_tokens: int = Field(ge=512, le=8192)
    standard_rates_through_input_limit: Literal[True]

    @field_validator("source")
    @classmethod
    def official_reference(cls, value):
        from urllib.parse import urlsplit

        u = urlsplit(value)
        if (
            u.scheme != "https"
            or u.hostname
            not in {
                "platform.claude.com",
                "docs.anthropic.com",
                "www.anthropic.com",
                "anthropic.com",
                "claude.com",
                "www.claude.com",
            }
            or u.username
            or u.password
        ):
            raise ValueError("Supply an official Anthropic pricing/model reference")
        return value


FeedbackAction = Literal[
    "CORRECT",
    "INCORRECT",
    "UNCERTAIN",
    "HALLMARK_CONFIRMED_VISUALLY",
    "FALSE_HALLMARK",
    "INCORRECT_STONE",
    "PLATED_ITEM",
    "STERLING_VERIFIED_SEPARATELY",
    "POSSIBLE_FACTORY",
    "INTERESTING",
    "LOW_RESALE_APPEAL",
    "PURCHASED",
    "PASSED",
    "SUBITEM_REVIEW",
]


class Correction(Strict):
    action: FeedbackAction
    note: str = Field(min_length=1, max_length=2000)
    finding_id: int | None = None
    sub_item_id: int | None = None
