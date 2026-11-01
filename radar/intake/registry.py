from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select

from radar.db import IntakeSource, SavedSourceSearch

SOURCES = {
    "shopgoodwill": (
        "ShopGoodwill",
        ["shopgoodwill.com"],
        "Personal Shopper: owner-reported; actual search-match format unconfirmed",
    ),
    "hibid": (
        "HiBid",
        ["hibid.com"],
        "Saved searches/notifications: owner-reported; daily digest format unconfirmed",
    ),
    "invaluable": (
        "Invaluable",
        ["invaluable.com"],
        "Owner receives saved-search, outbid and spotlight emails; review each observed format",
    ),
    "liveauctioneers": (
        "LiveAuctioneers",
        ["liveauctioneers.com"],
        "Observed outbid and ended-item emails with recommendations; search alerts unverified",
    ),
    "estatesales-org": (
        "EstateSales.org",
        ["estatesales.org"],
        "Notification format/capabilities unverified",
    ),
    "estatesales-net": (
        "EstateSales.net",
        ["estatesales.net"],
        "Notification format/capabilities unverified",
    ),
    "ctbids": ("CTBids", ["ctbids.com"], "Notification format/capabilities unverified"),
    "poshmark": (
        "Poshmark",
        ["poshmark.com"],
        "Owner text/CSV/JSON supported; notification format unverified",
    ),
    "mercari": (
        "Mercari",
        ["mercari.com"],
        "Owner text/CSV/JSON supported; notification format unverified",
    ),
    "other": (
        "Other authorized estate",
        ["example.invalid"],
        "Owner must configure a permitted source domain and sample",
    ),
}
SEARCH_GROUPS = {
    "Cheap Rings": [
        "old blue stone ring",
        "green stone silver ring",
        "sterling ring lot",
        "925 ring",
        "old silver ring",
        "western ring",
        "turqoise ring",
    ],
    "Sterling Lots": [
        "sterling jewelry lot",
        "925 lot",
        "old silver jewelry",
        "mixed estate jewelry",
        "silver scrap jewelry",
        "heavy sterling",
        "jewelry jar",
    ],
    "Southwestern Anomalies": [
        "nature america",
        "navaho",
        "navjo",
        "turquiose",
        "turqoise",
        "silver blue rock",
        "old Indian jewelry",
    ],
    "Plain descriptions and color": [
        "blue stone ring",
        "green stone ring",
        "blue ring",
        "silver ring with blue stone",
        "blue stone bracelet",
        "blue stone pendant",
        "blue stone necklace",
        "blue stone earrings",
        "blue stone brooch",
    ],
    "Material and item type": [
        "silver ring",
        "sterling ring",
        "925 ring",
        "silver bracelet",
        "sterling bracelet",
        "silver cuff",
        "silver pendant",
        "silver necklace",
        "silver earrings",
        "925 jewelry",
    ],
    "Lots and mixed jewelry": [
        "jewelry lot",
        "mixed jewelry lot",
        "silver jewelry lot",
        "sterling jewelry lot",
        "925 lot",
        "estate jewelry lot",
        "jewelry jar",
        "jewelry bag",
        "scrap silver jewelry",
    ],
    "Explicit turquoise and regional descriptions": [
        "turquoise jewelry",
        "turquoise ring",
        "turquoise bracelet",
        "turquoise cuff",
        "turquoise pendant",
        "turquoise necklace",
        "turquoise earrings",
        "southwestern jewelry",
        "western jewelry",
    ],
    "Spelling variants": ["turqoise", "turquiose", "turqouise", "turquois", "navaho", "navjo"],
    "Vague or incomplete descriptions": [
        "stone ring",
        "stone bracelet",
        "stone necklace",
        "stone jewelry",
        "silver tone jewelry",
        "costume jewelry lot",
        "unmarked jewelry",
        "unidentified jewelry",
        "blue rock jewelry",
    ],
}

# Display recommendations only; never owner selection, configuration or query history.
STARTER_PHRASES = (
    "blue stone ring",
    "silver ring with blue stone",
    "green stone ring",
    "silver ring",
    "sterling ring",
    "blue stone bracelet",
    "silver cuff",
    "blue stone pendant",
    "blue stone necklace",
    "blue stone earrings",
    "sterling jewelry lot",
    "mixed jewelry lot",
    "jewelry jar",
    "turquoise jewelry",
    "turqoise",
)


class SourceDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=100)
    domains: list[str] = Field(min_length=1, max_length=10)
    methods: list[str] = Field(
        default_factory=lambda: ["owner_eml", "pasted_text", "csv_json", "manual"]
    )
    authorization: str = "No automated website or mailbox access authorized"
    notifications: str = "Unverified"
    available_data: str = "Only explicitly supplied fields; missing values stay unknown"
    shipping_uncertainty: str = (
        "99669 availability, shipping and handling require independent evidence"
    )
    fee_profile: str | None = None
    exact_queries: str = (
        "Retained only when explicitly supplied; source exact-match semantics unverified"
    )
    implementation: str = "Operational safe link/label intake; real formats require owner review"
    sample_kind: Literal["SYNTHETIC", "OWNER_PROVIDED"] = "SYNTHETIC"
    sample_verified: bool = False
    sample_reference: str | None = None
    reviewed_email_formats: dict[str, str] = Field(default_factory=dict)
    item_path_prefix: str | None = None
    field_labels: dict[str, list[str]] = Field(default_factory=dict)
    restrictions: str = (
        "No scraping, redirects, external resources, accounts, mailbox OAuth or polling"
    )

    @model_validator(mode="after")
    def verified_rules(self):
        import re

        for domain in self.domains:
            if not re.fullmatch(
                r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?\.[a-z]{2,}", domain
            ) or domain.endswith((".local", ".localhost")):
                raise ValueError(
                    "Use public DNS source domains only, without scheme, port or wildcard"
                )
        if (self.item_path_prefix or self.field_labels) and not (
            self.sample_verified and self.sample_reference
        ):
            raise ValueError(
                "Source-specific rules require owner verification against permitted sample evidence"
            )
        if self.sample_verified and not self.sample_reference:
            raise ValueError("Verified formats require a permitted sample reference")
        if self.reviewed_email_formats and (
            not self.sample_verified
            or not self.sample_reference
            or any(not value.strip() for value in self.reviewed_email_formats.values())
        ):
            raise ValueError("Reviewed email formats require verified, referenced sample evidence")
        if self.item_path_prefix and not re.fullmatch(
            r"/[A-Za-z0-9/_-]{1,100}/", self.item_path_prefix
        ):
            raise ValueError("Item prefix must be a simple slash-delimited path")
        allowed = {"title", "price", "seller", "auction", "item_id", "query", "closing"}
        if set(self.field_labels) - allowed or any(len(v) > 5 for v in self.field_labels.values()):
            raise ValueError("Unsupported or excessive field-label configuration")
        if any(
            not re.fullmatch(r"[A-Za-z0-9 _-]{1,50}", label)
            for values in self.field_labels.values()
            for label in values
        ):
            raise ValueError("Labels must be short literal text, not regular expressions")
        return self


def auction_email_platform(source):
    """Identify these two email platforms by registered domains, preserving custom IDs/names."""
    domains = source.domains if isinstance(source, SourceDefinition) else source.get("domains", [])
    found = [
        platform
        for platform in ("invaluable", "liveauctioneers")
        if any(d == platform + ".com" or d.endswith("." + platform + ".com") for d in domains)
    ]
    return found[0] if len(found) == 1 else None


def duplicate_auction_platform(session, definition, *, exclude_id=None):
    """Creation and edits share the same domain ownership check, including mixed aliases."""

    def platforms(source):
        domains = (
            source.domains if isinstance(source, SourceDefinition) else source.get("domains", [])
        )
        return {
            platform
            for platform in ("invaluable", "liveauctioneers")
            if any(d == platform + ".com" or d.endswith("." + platform + ".com") for d in domains)
        }

    proposed = platforms(definition)
    return any(
        row.id != exclude_id and proposed & platforms(row.data)
        for row in session.scalars(select(IntakeSource))
    )


def seed_searches(session, source_id):
    """Add missing exact phrases; preserve historical groups, IDs and owner choices."""
    existing = set(
        session.scalars(
            select(SavedSourceSearch.phrase).where(SavedSourceSearch.source_id == source_id)
        )
    )
    for group, phrases in SEARCH_GROUPS.items():
        for phrase in phrases:
            if phrase not in existing:
                session.add(
                    SavedSourceSearch(
                        source_id=source_id,
                        phrase=phrase,
                        group_name=group,
                        selected=False,
                        configured=False,
                    )
                )
                existing.add(phrase)
    session.flush()


def seed_sources(session):
    for source_id, (name, domains, notifications) in SOURCES.items():
        if not session.get(IntakeSource, source_id):
            if source_id in ("invaluable", "liveauctioneers") and any(
                auction_email_platform(row.data) == source_id
                for row in session.scalars(select(IntakeSource))
            ):
                continue  # An existing custom source owns this platform's history.
            definition = SourceDefinition(name=name, domains=domains, notifications=notifications)
            session.add(IntakeSource(id=source_id, data=definition.model_dump(mode="json")))
    session.flush()
    for source_id in session.scalars(select(IntakeSource.id)):
        seed_searches(session, source_id)
