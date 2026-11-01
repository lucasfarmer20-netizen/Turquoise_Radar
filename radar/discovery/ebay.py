"""Official Browse/OAuth endpoints only. Production is explicitly gated, never auto-enabled."""

import asyncio
import base64
import json
import re
import time
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path

import httpx
from pydantic import BaseModel, Field, SecretStr, ValidationError

from radar.discovery.config import Connection, DiscoveryError, SearchOptions
from radar.schemas import ListingInput
from radar.sources import SourceAdapter, SourceCapabilities

BASE_URLS = {"sandbox": "https://api.sandbox.ebay.com", "production": "https://api.ebay.com"}
OAUTH_ERROR_CODES = frozenset(
    {
        "invalid_request",
        "invalid_client",
        "invalid_grant",
        "unauthorized_client",
        "unsupported_grant_type",
        "invalid_scope",
        "server_error",
        "temporarily_unavailable",
    }
)
SCOPE = "https://api.ebay.com/oauth/api_scope"
SOURCE_NAMES = {"fixture": "eBay FIXTURE", "sandbox": "eBay SANDBOX", "production": "eBay"}


class SearchPage(BaseModel):
    items: list[dict]
    corrected_query: str | None = None
    correction_state: str = "UNKNOWN"
    offset: int


class SearchResult(BaseModel):
    pages: list[SearchPage] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


def parse_page(data, query, offset):
    if not isinstance(data, dict) or not isinstance(data.get("itemSummaries", []), list):
        raise DiscoveryError("MALFORMED", "Browse search returned an invalid response structure")
    items = data.get("itemSummaries", [])
    if any(not isinstance(item, dict) for item in items):
        raise DiscoveryError("MALFORMED", "Browse search returned an invalid item structure")
    total = data.get("total")
    if total is not None and (not isinstance(total, int) or isinstance(total, bool) or total < 0):
        raise DiscoveryError("MALFORMED", "Browse search returned an invalid total")
    # Missing summaries are legitimate for explicit zero results, not arbitrary error objects.
    if "itemSummaries" not in data and total != 0:
        raise DiscoveryError("MALFORMED", "Browse response lacks search results")
    correction = data.get("autoCorrections")
    corrected = correction.get("q") if isinstance(correction, dict) else None
    if corrected is not None and not isinstance(corrected, str):
        raise DiscoveryError("MALFORMED", "Browse correction is not text")
    state = (
        "UNKNOWN"
        if corrected is None
        else ("REPORTED_UNCHANGED" if corrected == query else "CORRECTED")
    )
    return SearchPage(items=items, corrected_query=corrected, correction_state=state, offset=offset)


class EbayAdapter(SourceAdapter):
    capabilities = SourceCapabilities(
        name="eBay Browse",
        enabled=True,
        exact_match_supported=False,
        reveals_effective_query=True,
        requests_per_minute=60,
        access_method="Official Browse API + application OAuth",
    )

    def __init__(
        self,
        connection: Connection,
        *,
        production_run_approved=False,
        client=None,
        sleep=asyncio.sleep,
        clock=time.monotonic,
    ):
        if connection.mode not in BASE_URLS:
            raise DiscoveryError("MODE_DISABLED", "API adapter requires sandbox or production mode")
        connection.require(connection.mode, production_run_approved)
        self.connection = connection
        self.base = BASE_URLS[connection.mode]
        self.client = client or httpx.AsyncClient(
            timeout=connection.timeout, follow_redirects=False
        )
        self.owns_client = client is None
        self.sleep, self.clock = sleep, clock
        self.token = SecretStr("")
        self.token_expiry = 0
        self.last_request = None

    def public_data(self, value):
        """Raw search observations are retained, but credential-like fields/values never are."""
        secrets = [
            self.connection.client_id.get_secret_value(),
            self.connection.client_secret.get_secret_value(),
            self.token.get_secret_value(),
        ]
        pair = (
            self.connection.client_id.get_secret_value()
            + ":"
            + self.connection.client_secret.get_secret_value()
        )
        if all(secrets[:2]):
            secrets.append(base64.b64encode(pair.encode()).decode())
        if isinstance(value, dict):
            return {
                self.public_data(k): (
                    "[REDACTED]"
                    if k.casefold()
                    in (
                        "authorization",
                        "access_token",
                        "refresh_token",
                        "client_secret",
                        "client_id",
                    )
                    else self.public_data(v)
                )
                for k, v in value.items()
            }
        if isinstance(value, list):
            return [self.public_data(v) for v in value]
        if isinstance(value, str):
            for secret in secrets:
                if secret:
                    if len(secret) < 8:
                        # A tiny mock/opaque token is a secret as a standalone value,
                        # not every occurrence of its letters in schema keys/words.
                        value = re.sub(
                            r"(?<![A-Za-z0-9])" + re.escape(secret) + r"(?![A-Za-z0-9])",
                            "[REDACTED]",
                            value,
                        )
                    else:
                        value = value.replace(secret, "[REDACTED]")
        return value

    async def _request(self, method, path, **kwargs):
        for attempt in range(self.connection.retry_limit + 1):
            if self.last_request is not None:
                wait = self.connection.request_interval - (self.clock() - self.last_request)
                if wait > 0:
                    await self.sleep(wait)
            self.last_request = self.clock()
            try:
                response = await self.client.request(
                    method,
                    self.base + path,
                    timeout=self.connection.timeout,
                    follow_redirects=False,
                    **kwargs,
                )
            except httpx.RequestError:
                if attempt < self.connection.retry_limit:
                    await self.sleep(min(2**attempt, self.connection.max_retry_delay))
                    continue
                raise DiscoveryError(
                    "NETWORK", "eBay network request failed; check permitted access"
                ) from None
            if (
                path == "/identity/v1/oauth2/token"
                and 400 <= response.status_code < 500
                and response.status_code != 429
            ):
                try:
                    body = response.json()
                except ValueError:
                    body = None
                code = body.get("error") if isinstance(body, dict) else None
                code = code if isinstance(code, str) and code in OAUTH_ERROR_CODES else "UNKNOWN"
                raise DiscoveryError(
                    "PERMISSION" if response.status_code in (401, 403) else "HTTP",
                    f"OAuth HTTP {response.status_code}: {code}",
                )
            if response.status_code in (401, 403):
                raise DiscoveryError(
                    "PERMISSION",
                    f"eBay HTTP {response.status_code}: credentials or permissions rejected",
                )
            if response.status_code == 429 or 500 <= response.status_code < 600:
                delay = min(2**attempt, self.connection.max_retry_delay)
                retry = response.headers.get("Retry-After")
                if retry:
                    try:
                        delay = float(retry)
                    except ValueError:
                        try:
                            stamp = parsedate_to_datetime(retry)
                            delay = (stamp - datetime.now(UTC)).total_seconds()
                        except (ValueError, TypeError, OverflowError):
                            delay = min(2**attempt, self.connection.max_retry_delay)
                if delay > self.connection.max_retry_delay:
                    raise DiscoveryError(
                        "RATE_LIMIT",
                        "eBay retry wait exceeds local run limit; defer the next manual run",
                    )
                if attempt < self.connection.retry_limit:
                    await self.sleep(max(0, delay))
                    continue
                raise DiscoveryError(
                    "RATE_LIMIT", "eBay rate/service limit exhausted bounded retries"
                )
            if response.status_code != 200:
                raise DiscoveryError(
                    "HTTP", f"eBay returned HTTP {response.status_code}; request stopped"
                )
            try:
                data = response.json()
            except ValueError:
                raise DiscoveryError("MALFORMED", "eBay returned invalid JSON") from None
            if not isinstance(data, dict):
                raise DiscoveryError("MALFORMED", "eBay returned an invalid object")
            return data
        raise AssertionError("Unreachable retry state")

    async def access_token(self):
        if self.token.get_secret_value() and self.clock() < self.token_expiry:
            return self.token.get_secret_value()
        data = await self._request(
            "POST",
            "/identity/v1/oauth2/token",
            auth=httpx.BasicAuth(
                self.connection.client_id.get_secret_value(),
                self.connection.client_secret.get_secret_value(),
            ),
            data={"grant_type": "client_credentials", "scope": SCOPE},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        token, expires = data.get("access_token"), data.get("expires_in")
        if (
            not isinstance(token, str)
            or not token.strip()
            or not isinstance(expires, int)
            or isinstance(expires, bool)
            or expires <= 0
            or not isinstance(data.get("token_type", "Bearer"), str)
            or data.get("token_type", "Bearer").lower()
            not in ("bearer", "application access token")
        ):
            raise DiscoveryError(
                "MALFORMED", "OAuth response is missing a valid bearer token or lifetime"
            )
        self.token = SecretStr(token)
        self.token_expiry = self.clock() + max(0, expires - 30)
        return token

    async def search(self, query, options: SearchOptions, limit, page_limit=2, page_size=3):
        result = SearchResult()
        offset, received = 0, 0
        try:
            for _ in range(page_limit):
                size = min(page_size, limit - received)
                if size <= 0:
                    break
                token = await self.access_token()
                data = await self._request(
                    "GET",
                    "/buy/browse/v1/item_summary/search",
                    params=options.params(query, size, offset),
                    headers={
                        "Authorization": "Bearer " + token,
                        "X-EBAY-C-MARKETPLACE-ID": "EBAY_US",
                        "X-EBAY-C-ENDUSERCTX": "contextualLocation=country%3DUS%2Czip%3D99669",
                    },
                )
                page = parse_page(self.public_data(data), query, offset)
                # Enforce local budgets even if an upstream response ignores its requested limit.
                page.items = page.items[:size]
                result.pages.append(page)
                received += len(page.items)
                offset += size
                if not page.items or not (
                    data.get("next")
                    or isinstance(data.get("total"), int)
                    and offset < data["total"]
                ):
                    break
        except DiscoveryError as e:
            result.errors.append(f"{e.code}: {e}")
        return result

    async def retrieve(self, exact_query):
        result = await self.search(exact_query, SearchOptions(), limit=12)
        return [
            normalize(raw, self.connection.mode, exact_query, exact_query, page.corrected_query)
            for page in result.pages
            for raw in page.items
        ]

    async def close(self):
        if self.owns_client:
            await self.client.aclose()


class FixtureDiscoveryAdapter(SourceAdapter):
    capabilities = SourceCapabilities(
        name="eBay FIXTURE",
        enabled=True,
        exact_match_supported=False,
        reveals_effective_query=True,
        access_method="Local synthetic Browse fixtures",
    )

    def __init__(self, path=None):
        path = path or Path(__file__).resolve().parents[2] / "fixtures/ebay/discovery.json"
        self.data = json.loads(Path(path).read_text())

    async def search(self, query, options, limit, page_limit=2, page_size=3):
        entry = self.data["queries"].get(query, {"ids": []})
        items = [self.data["items"][key] for key in entry["ids"]]

        def wanted(item):
            choices = item.get("buyingOptions", [])
            if options.buying_format != "BOTH" and options.buying_format not in choices:
                return False
            price = item.get("currentBidPrice") if choices == ["AUCTION"] else item.get("price")
            from decimal import Decimal

            if options.min_price is not None or options.max_price is not None:
                if not price or price.get("currency") != "USD":
                    return False
                amount = Decimal(price["value"])
                if options.min_price is not None and amount < options.min_price:
                    return False
                if options.max_price is not None and amount > options.max_price:
                    return False
            return True

        items = [i for i in items if wanted(i)]
        # Fixture order is deterministic; never presented as current marketplace ordering.
        result = SearchResult()
        for offset in range(0, min(len(items), limit), page_size):
            if len(result.pages) >= page_limit:
                break
            payload = {
                "itemSummaries": items[offset : min(offset + page_size, limit)],
                "total": len(items),
            }
            if options.auto_correct == "KEYWORD" and entry.get("corrected_query"):
                payload["autoCorrections"] = {"q": entry["corrected_query"]}
            result.pages.append(parse_page(payload, query, offset))
        if not result.pages:
            result.pages.append(parse_page({"total": 0}, query, 0))
        return result

    async def retrieve(self, exact_query):
        result = await self.search(exact_query, SearchOptions(), 12)
        return [
            normalize(raw, "fixture", exact_query, exact_query, page.corrected_query)
            for page in result.pages
            for raw in page.items
        ]

    async def close(self):
        pass


def normalize(raw, mode, query, original_query, corrected=None, pattern_id=None):
    """Summary shipping is not destination proof, even with requested 99669 context."""
    try:
        item_id, title = raw.get("itemId"), raw.get("title")
        if (
            not isinstance(item_id, str)
            or not item_id.strip()
            or not isinstance(title, str)
            or not title.strip()
        ):
            raise ValueError("Missing item identity/title")
        options = raw.get("buyingOptions", [])
        price = raw.get("currentBidPrice") if options == ["AUCTION"] else raw.get("price")
        price = price or {}
        shipping = raw.get("shippingOptions") or []
        shipping_price = shipping[0].get("shippingCost", {}) if shipping else {}
        shipping_value = (
            shipping_price.get("value")
            if shipping_price.get("currency") == price.get("currency", "XXX")
            else None
        )
        image = raw.get("image") or {}
        seller = raw.get("seller") or {}
        categories = raw.get("categories") or []
        category = (
            " / ".join(str(c.get("categoryName") or c.get("categoryId")) for c in categories)
            if categories
            else "UNKNOWN"
        )
        return ListingInput(
            source=SOURCE_NAMES[mode],
            source_listing_id=item_id,
            raw_title=title,
            raw_description=raw.get("shortDescription") or "",
            listing_url=raw.get("itemWebUrl"),
            item_price=price.get("value"),
            currency=price.get("currency", "XXX"),
            seller_id=seller.get("username"),
            buying_options=options,
            condition=raw.get("condition"),
            category=category,
            image_urls=[image["imageUrl"]] if image.get("imageUrl") else [],
            listing_time=raw.get("itemCreationDate"),
            item_end_time=raw.get("itemEndDate"),
            search_pattern_id=pattern_id,
            original_search_phrase=original_query,
            raw_search_phrase=query,
            effective_query=corrected,
            shipping_cost=shipping_value,
            shipping_status="UNKNOWN",
            shipping_evidence="Browse summary shipping is not confirmed for Soldotna 99669",
            cost_status={"item_price": "CONFIRMED", "shipping_cost": "ESTIMATED"},
            material_evidence="unknown",
            synthetic=mode != "production",
            discovery_mode=mode,
        )
    except (ValueError, TypeError, AttributeError, KeyError, ValidationError):
        raise DiscoveryError(
            "MALFORMED_ITEM", "Browse item could not be normalized; no values invented"
        ) from None
