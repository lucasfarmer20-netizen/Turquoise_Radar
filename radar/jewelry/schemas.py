import re
from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from radar.jewelry.config import HAIKU_QUICK_MAX_TOKENS
from radar.jewelry.validation import reference_error, rule_error

PROMPT_VERSION = "jewelry-3"
SCHEMA_VERSION = "3"
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


# Full-clause exceptions, not a general "contains not" exemption. No names, values,
# weights or arbitrary suffixes can enter through these fixed caution templates.
_SUBJECT = r"(?:(?:this|the) (?:piece|item|jewelry|metal|cuff|ring|bracelet|pendant)|this|it)"
_PHOTO_BASIS = r"(?: from (?:this|the|a) (?:photo|photograph|image)| from appearance alone)?"
_SAFE_CAUTION = tuple(
    re.compile(pattern, re.I)
    for pattern in (
        rf"(?:{_SUBJECT} is )?not authenticated{_PHOTO_BASIS}",
        rf"{_SUBJECT} (?:cannot|can not|can't) be authenticated{_PHOTO_BASIS}",
        rf"(?:cannot|can not|can't) (?:determine|confirm|verify) whether {_SUBJECT} is "
        rf"(?:solid |genuine |pure )?(?:925 )?sterling(?: silver)?{_PHOTO_BASIS}",
        rf"whether {_SUBJECT} is (?:solid |genuine |pure )?(?:925 )?sterling(?: silver)? "
        rf"cannot be (?:determined|confirmed|verified){_PHOTO_BASIS}",
        rf"no (?:tribal|cultural) attribution (?:can be|is) "
        rf"(?:established|verified|supported|determined){_PHOTO_BASIS}",
        rf"(?:tribal|cultural) attribution cannot be "
        rf"(?:established|verified|supported|determined){_PHOTO_BASIS}",
        rf"(?:cannot|can not|can't) determine who {_SUBJECT} was made by{_PHOTO_BASIS}",
        rf"who {_SUBJECT} was made by cannot be determined{_PHOTO_BASIS}",
    )
)


def guarded_text(value):
    forbidden = (
        ("vision_weight", r"\d+(?:\.\d+)?\s*(?:grams?\b|g\b|dwt\b|ounces?\b|oz\b)"),
        ("vision_value", r"\$\s*\d|\bUSD\s*\d|\b\d+(?:\.\d+)?\s*(?:dollars?|USD)\b"),
        ("vision_culture", r"\b(?:Navajo|Hopi|Zuni|tribal|Indigenous|Native American)\b"),
        ("vision_treatment", r"\b(?:natural|untreated|stabilized)\s+turquoise\b"),
        (
            "vision_authentication",
            r"\b(?:authentic vintage|confirmed purity|definitely|certainly|"
            r"authenticated|guaranteed)\b",
        ),
        (
            "vision_material",
            r"\b(?:is|confirmed|verified|solid|pure|genuine)\s+(?:925\s+)?sterling\b",
        ),
        (
            "vision_mine",
            r"\b(?:mine origin|mined at)\b|\b(?:Kingman|Sleeping Beauty|Bisbee|Royston|"
            r"Cerrillos|Carico Lake|Lander Blue)\s+(?:turquoise|mine)\b",
        ),
        ("vision_maker_claim", r"\bmade by\b"),
    )
    # Identify whole caution clauses, then scan the ORIGINAL string. In particular,
    # decimals and assertions spanning newlines must not evade the original bans.
    safe_spans = []
    start = 0
    separators = list(
        re.finditer(r"[.;!?,\n]|\b(?:but|however|yet|although|and|because)\b", value, re.I)
    )
    boundaries = [(m.start(), m.end()) for m in separators] + [(len(value), len(value))]
    for end, next_start in boundaries:
        if any(pattern.fullmatch(value[start:end].strip()) for pattern in _SAFE_CAUTION):
            safe_spans.append((start, end))
        start = next_start
    for code, pattern in forbidden:
        # Both matches and caution spans are ordered. Scan each span at most once
        # per rule so long explanations do not cause quadratic containment work.
        spans = iter(safe_spans)
        span = next(spans, None)
        for match in re.finditer(pattern, value, re.I):
            while span is not None and span[1] < match.end():
                span = next(spans, None)
            if span is None or span[0] > match.start():
                raise rule_error(code)
    return value  # Never rewrite accepted findings.


class Finding(Strict):
    key: str = Field(pattern=r"^[A-Za-z0-9_-]{1,40}$")
    category: Literal["CLASSIFICATION", "METAL", "STONE", "CONSTRUCTION", "CONDITION", "RESEARCH"]
    # Prose is bounded by the provider response cap, not presentation lengths.
    description: str = Field(min_length=1)
    image_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    visible_evidence: str = Field(min_length=1)
    confidence: Literal["HIGH", "MEDIUM", "LOW", "UNKNOWN"]
    evidence_kind: Literal["DIRECT", "INFERRED"]
    uncertainty: str = Field(min_length=1)
    next_action: str | None = None
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
            raise rule_error("vision_inferred_confidence")
        if self.hallmark_transcription and (
            self.legibility != "LEGIBLE"
            or self.evidence_kind != "DIRECT"
            or self.category != "METAL"
        ):
            raise rule_error("vision_hallmark_evidence")
        if self.hallmark_transcription and not re.fullmatch(
            r"[A-Za-z0-9 .&+/'-]{1,40}", self.hallmark_transcription
        ):
            raise rule_error("vision_hallmark_text")
        if any(not re.fullmatch(r"[A-Za-z0-9? .&+/'-]{1,10}", f) for f in self.visible_fragments):
            raise rule_error("vision_fragment_text")
        return self


class MakerCandidate(Strict):
    research_name: str = Field(min_length=1)
    supporting_finding_key: str
    uncertainty: str = Field(min_length=1)
    status: Literal["UNVERIFIED_RESEARCH_LEAD"] = "UNVERIFIED_RESEARCH_LEAD"

    @field_validator("research_name", "uncertainty")
    @classmethod
    def safe(cls, value):
        return guarded_text(value)


class SubItem(Strict):
    key: str = Field(pattern=r"^[A-Za-z0-9_-]{1,40}$")
    label: str = Field(min_length=1)
    image_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    location_hint: str = Field(min_length=1)
    finding_keys: list[str] = Field(min_length=1, max_length=8)
    uncertainty: str = Field(min_length=1)

    @field_validator("label", "location_hint", "uncertainty")
    @classmethod
    def safe(cls, value):
        return guarded_text(value)


class VisionResult(Strict):
    classification: Kind
    summary: str = Field(min_length=1)
    visible_southwestern_style: str
    hallmark_candidate: str
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
            raise rule_error("vision_duplicate_keys")
        if any(set(s.finding_keys) - keys for s in self.sub_items):
            raise rule_error("vision_unknown_finding")
        if self.possible_maker_identity:
            key = self.possible_maker_identity.supporting_finding_key
            f = next((f for f in self.findings if f.key == key), None)
            if not f or not f.hallmark_transcription:
                raise rule_error("vision_maker_mark")
            tokens = re.findall(r"[A-Z]+", f.hallmark_transcription.upper())
            generic = {
                "STERLING",
                "SILVER",
                "GOLD",
                "FILLED",
                "PLATED",
                "PLATE",
                "GF",
                "GP",
                "GEP",
                "HGE",
                "RGP",
                "EPNS",
                "EP",
                "K",
                "KT",
                "KARAT",
                "KARATS",
            }
            if not set(tokens) - generic or (
                tokens == ["S"] and re.search(r"\d{3}", f.hallmark_transcription)
            ):
                raise rule_error("vision_generic_mark")
        return self

    def validate_images(self, ids):
        for field in ("findings", "sub_items"):
            for index, item in enumerate(getattr(self, field)):
                if item.image_id not in ids:
                    raise reference_error("vision_unknown_image", (field, index, "image_id"))
        for index, item in enumerate(self.sub_items):
            if not any(
                f.key in item.finding_keys and f.image_id == item.image_id for f in self.findings
            ):
                raise reference_error("vision_subitem_image", ("sub_items", index, "finding_keys"))


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
    max_output_tokens: int = Field(ge=512, le=HAIKU_QUICK_MAX_TOKENS)
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
