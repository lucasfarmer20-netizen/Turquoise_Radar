"""Offline detail-hydration evaluation; no HTTP client or production enablement.

The injected fetcher receives a proposed getItem request. Only the offline pilot
uses this adapter. Identity evidence augments raw summaries without rewriting them.
"""

import copy
import hashlib
import json
from datetime import datetime
from typing import Literal
from urllib.parse import quote

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from radar.discovery.config import DiscoveryError
from radar.schemas import utcnow

FIELD = "jri020_identity_evidence"


def summary_hash(summary):
    return hashlib.sha256(
        json.dumps(summary, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


class DetailSeller(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    userId: str = Field(min_length=1, max_length=256, pattern=r"^\S+$")
    username: str = Field(min_length=1, max_length=256, pattern=r"^\S+$")


class IdentityEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["jri020-detail-identity-1"] = "jri020-detail-identity-1"
    source: Literal["eBay Browse getItem"] = "eBay Browse getItem"
    fieldgroups: Literal["ADDITIONAL_SELLER_DETAILS"] = "ADDITIONAL_SELLER_DETAILS"
    item_id: str
    observed_at: AwareDatetime
    summary_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    seller: DetailSeller
    production_validation: Literal["UNVERIFIED"] = "UNVERIFIED"


def evidence_for(summary, detail, observed_at):
    if FIELD in summary:
        raise ValueError("Summary cannot supply its own hydration evidence")
    item_id = summary.get("itemId")
    if not isinstance(item_id, str) or not item_id or detail.get("itemId") != item_id:
        raise ValueError("Detail item identity differs")
    seller = detail.get("seller")
    if not isinstance(seller, dict):
        raise ValueError("Detail seller identity unavailable")
    identity = DetailSeller.model_validate({k: seller.get(k) for k in ("userId", "username")})
    summary_seller = summary.get("seller") or {}
    if (
        summary_seller.get("username") != identity.username
        or summary_seller.get("userId", identity.userId) != identity.userId
    ):
        raise ValueError("Summary/detail seller identity differs")
    return IdentityEvidence(
        item_id=item_id,
        observed_at=observed_at,
        summary_sha256=summary_hash(summary),
        seller=identity,
    )


def retained_identity(raw):
    """Validate typed evidence binding without treating it as production validation."""
    value = IdentityEvidence.model_validate(raw[FIELD])
    summary = {k: v for k, v in raw.items() if k != FIELD}
    if value.summary_sha256 != summary_hash(summary) or value.item_id != summary.get("itemId"):
        raise ValueError("Identity evidence does not bind this summary")
    if value.seller.username != (summary.get("seller") or {}).get("username"):
        raise ValueError("Identity evidence seller differs")
    return value.seller.model_dump()


class HydratedAdapter:
    """Hold each page in memory until every summary is bound to detail identity.

    Cache only within this run, keyed by exact item ID; rebind each independent
    summary and retain every query observation. No retries or fallback IDs.
    """

    def __init__(self, source, fetch_detail, *, clock=utcnow):
        self.source, self.fetch_detail, self.clock = source, fetch_detail, clock
        self.cache = {}
        self.detail_attempts = 0
        self.blocked = False

    async def search(self, *args, **kwargs):
        if self.blocked:
            raise DiscoveryError("IDENTITY_REVIEW", "Hydration stopped; retain quarantine")
        try:
            result = await self.source.search(*args, **kwargs)
            for page in result.pages:
                hydrated = []
                for summary in page.items:
                    item_id = summary.get("itemId")
                    if not isinstance(item_id, str) or not item_id or len(item_id) > 256:
                        raise ValueError("Item identifier unavailable")
                    if item_id not in self.cache:
                        if self.detail_attempts >= 12:
                            raise ValueError("Detail proposal bound exhausted")
                        self.detail_attempts += 1
                        detail = await self.fetch_detail(
                            {
                                "method": "GET",
                                "path": "/buy/browse/v1/item/" + quote(item_id, safe=""),
                                "params": {"fieldgroups": "ADDITIONAL_SELLER_DETAILS"},
                            }
                        )
                        stamp = self.clock()
                        if not isinstance(stamp, datetime) or stamp.utcoffset() is None:
                            raise ValueError("Aware detail timestamp required")
                        # Minimize retained identity data; do not persist other detail fields.
                        evidence = evidence_for(summary, detail, stamp)
                        self.cache[item_id] = (evidence.seller, stamp)
                    seller, stamp = self.cache[item_id]
                    evidence = evidence_for(
                        summary, {"itemId": item_id, "seller": seller.model_dump()}, stamp
                    )
                    hydrated.append(
                        copy.deepcopy(summary) | {FIELD: evidence.model_dump(mode="json")}
                    )
                page.items = hydrated
            return result
        except Exception:
            self.blocked = True
            raise DiscoveryError(
                "IDENTITY_REVIEW", "Detail identity unavailable or inconsistent; retain quarantine"
            ) from None

    async def close(self):
        await self.source.close()
