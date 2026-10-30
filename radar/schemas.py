from datetime import UTC, date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Literal

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

    @model_validator(mode="after")
    def evidence(self):
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
