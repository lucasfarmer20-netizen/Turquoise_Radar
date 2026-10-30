import os
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

Mode = Literal["fixture", "sandbox", "production"]


class DiscoveryError(Exception):
    """Only fixed, credential-free error messages may cross the adapter boundary."""

    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


class Connection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Mode = "fixture"
    enabled: bool = True
    client_id: SecretStr = Field(default_factory=lambda: SecretStr(""), repr=False)
    client_secret: SecretStr = Field(default_factory=lambda: SecretStr(""), repr=False)
    production_enabled: bool = False
    production_approved: bool = False
    timeout: float = Field(default=15, ge=1, le=60)
    retry_limit: int = Field(default=2, ge=0, le=3)
    request_interval: float = Field(default=1, ge=0.1, le=10)
    max_retry_delay: float = Field(default=10, ge=0, le=30)

    @classmethod
    def from_env(cls):
        return cls(
            mode=os.environ.get("EBAY_MODE", "fixture"),
            enabled=os.environ.get("EBAY_SOURCE_ENABLED", "true").lower() == "true",
            client_id=os.environ.get("EBAY_CLIENT_ID", ""),
            client_secret=os.environ.get("EBAY_CLIENT_SECRET", ""),
            production_enabled=os.environ.get("EBAY_PRODUCTION_ENABLED", "").lower() == "true",
            production_approved=os.environ.get("EBAY_PRODUCTION_APPROVED", "").lower() == "true",
        )

    def require(self, mode, approved_run=False):
        if not self.enabled:
            raise DiscoveryError(
                "DISABLED", "eBay discovery is disabled by environment configuration"
            )
        if mode == "fixture":
            return
        if mode != self.mode:
            raise DiscoveryError(
                "MODE_DISABLED", "Requested API mode is not enabled in environment configuration"
            )
        if mode == "production" and not (
            self.production_enabled and self.production_approved and approved_run
        ):
            raise DiscoveryError(
                "PRODUCTION_DISABLED",
                "Production requires environment approval, enablement and per-run confirmation",
            )
        if not self.client_id.get_secret_value() or not self.client_secret.get_secret_value():
            raise DiscoveryError(
                "CREDENTIALS_MISSING",
                "API credentials are not configured; fixture discovery remains available",
            )

    def status(self):
        return dict(
            mode=self.mode,
            enabled=self.enabled,
            credentials_present=bool(
                self.client_id.get_secret_value() and self.client_secret.get_secret_value()
            ),
            production_enabled=self.mode == "production"
            and self.production_enabled
            and self.production_approved,
            connection_tested=False,
        )


class SearchOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    buying_format: Literal["FIXED_PRICE", "AUCTION", "BOTH"] = "FIXED_PRICE"
    min_price: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    max_price: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    sort: Literal["newlyListed", "price", "-price"] = "newlyListed"
    auto_correct: Literal["omit", "KEYWORD"] = "omit"

    @model_validator(mode="after")
    def range_order(self):
        if (
            self.min_price is not None
            and self.max_price is not None
            and self.min_price > self.max_price
        ):
            raise ValueError("Minimum price must not exceed maximum price")
        return self

    def params(self, query, limit, offset=0):
        modes = "FIXED_PRICE|AUCTION" if self.buying_format == "BOTH" else self.buying_format
        filters = [f"buyingOptions:{{{modes}}}"]
        if self.min_price is not None or self.max_price is not None:
            low = "" if self.min_price is None else str(self.min_price)
            high = "" if self.max_price is None else str(self.max_price)
            filters += [f"price:[{low}..{high}]", "priceCurrency:USD"]
        params = dict(q=query, limit=limit, offset=offset, sort=self.sort, filter=",".join(filters))
        if self.auto_correct == "KEYWORD":
            params["auto_correct"] = "KEYWORD"
        return params


class Budgets(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = True
    max_queries: int = Field(default=4, ge=1, le=20)
    max_results: int = Field(default=12, ge=1, le=200)
    max_pages_per_query: int = Field(default=2, ge=1, le=5)
    page_size: int = Field(default=3, ge=1, le=50)


class GroupInput(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    enabled: bool = True
    queries: list[str] = Field(min_length=1, max_length=30)
    options: SearchOptions = Field(default_factory=SearchOptions)

    @model_validator(mode="after")
    def bounded_queries(self):
        if any(not q.strip() or len(q) > 200 or "\n" in q or "\r" in q for q in self.queries):
            raise ValueError(
                "Each exact query must be a nonblank single line of at most 200 characters"
            )
        if len(self.queries) != len(set(self.queries)):
            raise ValueError("Duplicate exact phrases within a group are not allowed")
        return self
