import re
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, StringConstraints, model_validator

Money = Annotated[Decimal, Field(ge=0, max_digits=14, decimal_places=4)]
Rate = Annotated[Decimal, Field(ge=0, le=1)]
ExactQuery = Annotated[str, StringConstraints(strip_whitespace=False, max_length=1000)]


class Shipping(StrEnum):
    CONFIRMED_TO_ALASKA = "CONFIRMED_TO_ALASKA"
    CONFIRMED_UNAVAILABLE = "CONFIRMED_UNAVAILABLE"
    UNKNOWN = "UNKNOWN"


class ValueStatus(StrEnum):
    CONFIRMED = "CONFIRMED"
    ESTIMATED = "ESTIMATED"
    UNKNOWN = "UNKNOWN"


class FeeProfile(BaseModel):
    """Dated owner assumptions; no claim that a category/tier is universally applicable."""

    commission: Rate = Decimal("0.08")
    processing: Rate = Decimal("0.029")
    processing_fixed: Money = Decimal("0.30")
    packaging: Money | None = None
    outbound_shipping: Money | None = None
    commission_tiers: dict[str, Rate] = Field(default_factory=dict)
    source: str = "Task 002 owner-provided US standard assumption; verify current eligibility"
    as_of: date | None = date(2026, 10, 29)
    verification_status: ValueStatus = ValueStatus.ESTIMATED
    # Named, explicit scenario assumptions may substitute for otherwise unknown buyer inputs.
    scenario_name: str | None = None
    buyer_shipping_assumption: Money | None = None
    buyer_tax_assumption: Money | None = None

    @model_validator(mode="after")
    def named_assumptions(self):
        if self.verification_status == ValueStatus.CONFIRMED and (
            not self.as_of or not self.source
        ):
            raise ValueError("Confirmed resale profile requires dated source evidence")
        if (
            self.buyer_shipping_assumption is not None or self.buyer_tax_assumption is not None
        ) and not self.scenario_name:
            raise ValueError("Buyer-paid shipping/tax assumptions require a scenario name")
        return self


FeeBase = Literal["item", "item_shipping", "item_premium_service", "invoice"]


class BuyerProfile(BaseModel):
    premium_rate: Rate = Decimal("0")
    premium_base: FeeBase = "item"
    service_rate: Rate = Decimal("0")
    service_base: FeeBase = "item"
    tax_rate: Rate = Decimal("0")
    tax_base: FeeBase = "item_premium_service"
    fixed_marketplace_fee: Money = Decimal("0")
    per_lot_fee: Money = Decimal("0")
    per_invoice_fee: Money | None = None
    shipping: Money | None = None
    handling: Money | None = None
    source: str = "Owner-configured profile; not independently verified"
    as_of: date | None = None
    verification_status: ValueStatus = ValueStatus.ESTIMATED

    @model_validator(mode="after")
    def acyclic_bases(self):
        if self.premium_base not in ("item", "item_shipping"):
            raise ValueError("Premium base must be item or item_shipping")
        if self.service_base not in ("item", "item_shipping"):
            raise ValueError("Service base must be item or item_shipping")
        if self.per_invoice_fee is not None and self.verification_status != ValueStatus.CONFIRMED:
            raise ValueError("Per-invoice fees require independent confirmation")
        if self.verification_status == ValueStatus.CONFIRMED and (
            not self.as_of or not self.source
        ):
            raise ValueError("Confirmed profile requires dated source evidence")
        return self


class Preferences(BaseModel):
    ring_low: Money = Decimal("15")
    ring_high: Money = Decimal("25")
    resale_low: Money = Decimal("35")
    resale_high: Money = Decimal("60")
    min_profit: Money = Decimal("15")
    min_roi: Annotated[Decimal, Field(ge=0, le=100)] = Decimal("0.60")
    resale_channel: str = "whatnot_planning"
    resale_profiles: dict[str, FeeProfile] = Field(
        default_factory=lambda: {"whatnot_planning": FeeProfile()}
    )
    buyer_profiles: dict[str, BuyerProfile] = Field(default_factory=dict)
    spot_usd_per_troy_oz: Money | None = None
    spot_timestamp: datetime | None = None
    spot_source: str | None = None
    refiner_payout: Rate = Decimal("0.80")
    require_verified_material: bool = True
    melt_max_ratio: Annotated[Decimal, Field(gt=0, le=1)] = Decimal("0.85")

    @model_validator(mode="before")
    @classmethod
    def legacy_metadata(cls, value):
        if isinstance(value, dict) and "resale_profiles" in value:
            value = dict(value)
            profiles = {}
            for name, profile in value["resale_profiles"].items():
                if isinstance(profile, dict) and "source" not in profile:
                    profile = {
                        **profile,
                        "source": "Legacy Phase 001 assumptions; recalibration required",
                        "as_of": None,
                        "verification_status": "ESTIMATED",
                    }
                profiles[name] = profile
            value["resale_profiles"] = profiles
        return value

    @model_validator(mode="after")
    def coherent(self):
        if self.ring_low > self.ring_high or self.resale_low > self.resale_high:
            raise ValueError("Range lower bounds must not exceed upper bounds")
        if self.resale_channel not in self.resale_profiles:
            raise ValueError("Selected resale channel must have a fee profile")
        if self.spot_usd_per_troy_oz is not None and (
            not self.spot_timestamp or not self.spot_source
        ):
            raise ValueError("Spot price requires a timestamp and source")
        return self


ResearchText = Annotated[str, StringConstraints(min_length=1, max_length=2000)]


def _research_identity(source, source_listing_id, source_url):
    domain = "shopgoodwill.com" if source == "ShopGoodwill" else "hibid.com"
    host = source_url.host or ""
    if (
        source_url.scheme != "https"
        or source_url.username
        or source_url.password
        or not (host == domain or host.endswith("." + domain))
    ):
        raise ValueError("Research URL must belong to the named marketplace")
    pattern = r"/item/([^/]+)/?" if source == "ShopGoodwill" else r"/lot/([^/]+)(?:/.*)?"
    identity = re.fullmatch(pattern, source_url.path, re.IGNORECASE)
    if not identity or identity[1] != source_listing_id:
        raise ValueError("Research source item ID must match its direct listing URL")


class ResearchAttachment(BaseModel):
    """A separately received private original, bound to an unchanged owner report."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    artifact_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    source: Literal["ShopGoodwill", "HiBid"]
    source_listing_id: str = Field(min_length=1, max_length=200)
    source_url: HttpUrl
    observation_id: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,100}$")
    received_at: datetime
    original_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    bytes: int = Field(ge=1, le=10_000_000)
    kind: Literal["SCREENSHOT", "AUCTION_TERMS"]
    display_name: str = Field(min_length=1, max_length=200)
    vision_photo_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")
    reviewed_at: datetime | None = None
    review_note: str | None = Field(default=None, min_length=1, max_length=2000)

    @model_validator(mode="after")
    def attachment_boundaries(self):
        _research_identity(self.source, self.source_listing_id, self.source_url)
        for value in (self.received_at, self.reviewed_at):
            if value is not None and value.utcoffset() is None:
                raise ValueError("Attachment timestamps require an explicit UTC offset")
        if self.display_name in (".", "..") or any(
            char in "/\\" or ord(char) < 32 or ord(char) == 127 for char in self.display_name
        ):
            raise ValueError("Attachment display name must be a plain basename")
        if (self.kind == "SCREENSHOT") != (self.vision_photo_id is not None):
            raise ValueError("Screenshots require a normalized photo; terms cannot refer to one")
        if self.kind == "AUCTION_TERMS" and self.bytes > 1_000_000:
            raise ValueError("Auction terms exceed the private text size limit")
        if (self.reviewed_at is not None) != bool(self.review_note and self.review_note.strip()):
            raise ValueError("Attachment review requires both a timestamp and a note")
        return self


class ResearchObservation(BaseModel):
    """Append-only owner reports; source claims do not establish verified financial facts."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    observation_id: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,100}$")
    source: Literal["ShopGoodwill", "HiBid"]
    source_listing_id: str = Field(min_length=1, max_length=200)
    source_url: HttpUrl
    recorded_at: datetime
    observed_date: date
    observed_at: datetime | None = None
    basis: Literal["OWNER_REPORTED_SCREENSHOTS_AND_TERMS"] = "OWNER_REPORTED_SCREENSHOTS_AND_TERMS"
    artifacts_received: Literal[False] = False
    note: str = Field(default="", max_length=5000)
    availability: Literal["ACTIVE_AT_OBSERVATION", "ENDED", "UNKNOWN"] = "UNKNOWN"
    currency: str = Field(default="XXX", pattern=r"^[A-Z]{3}$")
    item_quote: Money | None = None
    quote_kind: Literal["CURRENT_BID", "MINIMUM_BID", "ASKING_PRICE", "UNKNOWN"] = "UNKNOWN"
    next_minimum_bid: Money | None = None
    bid_count: int | None = Field(default=None, ge=0)
    bid_increment: Money | None = None
    realized_hammer: Money | None = None
    realized_currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    shipping_quote: Money | None = None
    handling_quote: Money | None = None
    buyer_premium_quote: Money | None = None
    sales_tax_quote: Money | None = None
    other_fees_quote: Money | None = None
    buyer_premium_rate: Rate | None = None
    sales_tax_rate: Rate | None = None
    tax_basis: Literal["UNKNOWN", "ITEM_ONLY", "ITEM_PLUS_PREMIUM"] = "UNKNOWN"
    shipping_state: Literal[
        "ZIP_QUOTE_OBSERVED",
        "FINAL_CONFIRMED",
        "GENERIC_ONLY",
        "RESTRICTIONS_REVIEW",
        "UNAVAILABLE",
        "UNKNOWN",
    ] = "UNKNOWN"
    final_fulfillment_confirmed: bool = False
    destination_postal_code: Literal["99669"] = "99669"
    shipping_method: str | None = Field(default=None, max_length=200)
    shipping_restrictions: list[ResearchText] = Field(default_factory=list, max_length=15)
    reported_end_at: datetime | None = None
    reported_end_zone: str | None = Field(default=None, max_length=100)
    auction_start_date: date | None = None
    auction_end_date: date | None = None
    seller_name: str | None = Field(default=None, max_length=300)
    seller_location: str | None = Field(default=None, max_length=500)
    auctioneer_name: str | None = Field(default=None, max_length=300)
    gross_weight_g: Annotated[Decimal, Field(gt=0)] | None = None
    material_claims: list[ResearchText] = Field(default_factory=list, max_length=15)
    reported_marks: list[ResearchText] = Field(default_factory=list, max_length=15)
    seller_test_claims: list[ResearchText] = Field(default_factory=list, max_length=15)
    piece_descriptions: list[ResearchText] = Field(default_factory=list, max_length=20)
    priority: Literal["HIGH", "NORMAL", "LOW", "HISTORICAL"] = "NORMAL"
    priority_reason: str = Field(default="", max_length=2000)

    @model_validator(mode="after")
    def report_boundaries(self):
        for value in (self.recorded_at, self.observed_at, self.reported_end_at):
            if value is not None and value.utcoffset() is None:
                raise ValueError("Research timestamps require an explicit UTC offset")
        if self.observed_at is not None and self.observed_at.date() != self.observed_date:
            raise ValueError("Observed date must match the explicitly supplied observation time")
        _research_identity(self.source, self.source_listing_id, self.source_url)
        if (self.reported_end_at is None) != (self.reported_end_zone is None):
            raise ValueError("A reported deadline requires its explicit source timezone")
        if self.reported_end_zone:
            try:
                ZoneInfo(self.reported_end_zone)
            except (ValueError, ZoneInfoNotFoundError):
                raise ValueError("Unknown deadline timezone") from None
        if self.realized_hammer is not None and (
            self.availability != "ENDED"
            or self.realized_currency is None
            or self.item_quote is not None
            or self.next_minimum_bid is not None
        ):
            raise ValueError("A realized hammer is historical, with an explicit separate currency")
        if self.availability == "ENDED" and (
            self.item_quote is not None or self.next_minimum_bid is not None
        ):
            raise ValueError("Ended observations cannot supply a current acquisition quote")
        if self.final_fulfillment_confirmed != (self.shipping_state == "FINAL_CONFIRMED"):
            raise ValueError(
                "Final fulfillment requires explicit final-confirmed shipping evidence"
            )
        return self


class ListingInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    source: str = Field(min_length=1, max_length=100)
    source_listing_id: str | None = None
    listing_url: HttpUrl | None = None
    seller_id: str | None = None
    sale_auction_id: str | None = None
    raw_title: str = Field(min_length=1, max_length=500)
    raw_description: str = ""
    item_price: Money | None = None
    currency: str = Field(default="USD", pattern=r"^[A-Z]{3}$")
    buyer_premium: Money | None = None
    buyer_service_fee: Money | None = None
    sales_tax: Money | None = None
    shipping_cost: Money | None = None
    handling_fee: Money | None = None
    buyer_profile: str | None = None
    shipping_status: Shipping = Shipping.UNKNOWN
    destination_postal_code: Literal["99669"] = "99669"
    carrier: str | None = None
    condition: str | None = None
    image_urls: list[HttpUrl] = Field(default_factory=list, max_length=20)
    stated_weight: Annotated[Decimal, Field(gt=0)] | None = None
    weight_unit: Literal["g", "dwt"] = "g"
    weight_kind: Literal["gross", "net_silver"] = "gross"
    weight_evidence: str | None = None
    weight_verified: bool = False
    material_evidence: Literal["unknown", "seller_claim", "verified_sterling", "plated"] = "unknown"
    hallmark_text: str | None = None
    piece_count: int = Field(default=1, ge=1, le=10000)
    listing_time: datetime | None = None
    search_pattern_id: str | None = None
    original_search_phrase: ExactQuery | None = None
    raw_search_phrase: ExactQuery | None = None
    effective_query: ExactQuery | None = None
    category: str = "jewelry"
    style_description: str | None = None
    cultural_attribution: str | None = None
    attribution_evidence: str | None = None
    resale_low: Money | None = None
    resale_high: Money | None = None
    resale_basis: str | None = None
    collector_evidence: str | None = None
    combined_shipping_available: bool | None = None
    synthetic: bool = False
    query_attribution_verified: bool | None = None
    combined_shipping_evidence: str | None = None
    buying_options: list[str] = Field(default_factory=list)
    item_end_time: datetime | None = None
    shipping_evidence: str | None = None
    discovery_mode: Literal["fixture", "sandbox", "production"] | None = None
    pasted_text: (
        Annotated[str, StringConstraints(strip_whitespace=False, max_length=30000)] | None
    ) = None
    ring_size: str | None = None
    material_terms: list[str] = Field(default_factory=list)
    cost_status: dict[str, ValueStatus] = Field(default_factory=dict)
    resale_buyer_shipping: Money | None = None
    resale_buyer_tax: Money | None = None
    resale_shipping_status: ValueStatus = ValueStatus.UNKNOWN
    resale_tax_status: ValueStatus = ValueStatus.UNKNOWN
    selling_category: str = "jewelry"
    commission_tier: str = "standard"
    resale_confirmed: bool = False
    resale_comparable_ids: list[int] = Field(default_factory=list)
    capture_confirmed: bool = False
    invoice_fee_allocation: Money | None = None
    invoice_allocation_confirmed: bool = False
    invoice_allocation_evidence: str | None = None
    research_observations: list[ResearchObservation] = Field(default_factory=list, max_length=50)
    research_attachments: list[ResearchAttachment] = Field(default_factory=list, max_length=50)

    @model_validator(mode="after")
    def evidence(self):
        seen_observations = set()
        for observation in self.research_observations:
            if (
                observation.observation_id in seen_observations
                or observation.source != self.source
                or observation.source_listing_id != self.source_listing_id
                or self.listing_url is None
                or observation.source_url != self.listing_url
            ):
                raise ValueError(
                    "Research observations must uniquely bind this source item and URL"
                )
            seen_observations.add(observation.observation_id)
        seen_artifacts, seen_hashes = set(), set()
        for attachment in self.research_attachments:
            if (
                attachment.artifact_id in seen_artifacts
                or attachment.original_sha256 in seen_hashes
                or attachment.source != self.source
                or attachment.source_listing_id != self.source_listing_id
                or attachment.source_url != self.listing_url
                or attachment.observation_id not in seen_observations
            ):
                raise ValueError("Research attachments must uniquely bind this item and its report")
            seen_artifacts.add(attachment.artifact_id)
            seen_hashes.add(attachment.original_sha256)
        if self.invoice_allocation_confirmed and not self.invoice_allocation_evidence:
            raise ValueError("Invoice allocation confirmation requires evidence")
        if (self.resale_low is None) != (self.resale_high is None):
            raise ValueError("Provide both resale bounds or neither")
        if self.resale_low is not None:
            if self.resale_low > self.resale_high or not self.resale_basis:
                raise ValueError("Resale range needs ordered bounds and an explicit basis")
        if self.cultural_attribution and not self.attribution_evidence:
            raise ValueError("Cultural attribution requires documented maker/provenance evidence")
        if self.weight_verified and not self.weight_evidence:
            raise ValueError("Verified weight requires evidence")
        return self


class ComparableInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    category: str = Field(min_length=1, max_length=100)
    ring_size: str | None = None
    materials: str = Field(min_length=1, max_length=500)
    weight_g: Annotated[Decimal, Field(gt=0)] | None = None
    condition: str = Field(min_length=1, max_length=1000)
    sale_price: Money
    selling_fees: Money | None = None
    sale_date: date
    evidence: str = Field(min_length=1, max_length=3000)
    notes: str = Field(default="", max_length=3000)
    listing_id: int | None = Field(default=None, gt=0)
    synthetic: bool = False


class FeedbackInput(BaseModel):
    action: Literal["Interested", "Purchased", "Passed", "False positive", "Sold"]
    note: str = Field(default="", max_length=2000)
    realized_profit: Annotated[Decimal, Field(max_digits=14, decimal_places=2)] | None = None

    @model_validator(mode="after")
    def sold_profit(self):
        if self.realized_profit is not None and self.action != "Sold":
            raise ValueError("Realized profit is only recorded with Sold")
        return self


def utcnow():
    return datetime.now(UTC)
