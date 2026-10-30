"""Only manual and local fixture acquisition is implemented."""

import csv
import hashlib
import io
import json
from abc import ABC, abstractmethod
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

from pydantic import ValidationError
from sqlalchemy import select

from radar.anomalies import match_pattern
from radar.db import Listing, SearchPattern
from radar.schemas import ListingInput, utcnow


@dataclass(frozen=True)
class SourceCapabilities:
    name: str
    enabled: bool = False
    exact_match_supported: bool = False
    reveals_effective_query: bool = False
    polling_seconds: int = 3600
    requests_per_minute: int = 1
    max_retries: int = 2
    access_method: str = "NOT IMPLEMENTED; authorization evaluation required"


class SourceAdapter(ABC):
    capabilities: SourceCapabilities

    @abstractmethod
    async def retrieve(self, exact_query: str) -> list[ListingInput]:
        """Send unchanged query; preserve raw/effective query and original data."""


SOURCE_ROSTER = [
    SourceCapabilities(name=n)
    for n in ("Poshmark", "Mercari", "EstateSales.net", "EstateSales.org", "CTBids")
]


class FixtureAdapter(SourceAdapter):
    capabilities = SourceCapabilities(name="fixtures", enabled=True, access_method="Local CSV/JSON")

    def __init__(self, rows):
        self.rows = rows

    async def retrieve(self, exact_query: str):
        return [
            ListingInput.model_validate(row)
            for row in self.rows
            if row.get("raw_search_phrase") == exact_query
        ]


def dedup_key(item):
    source = item.source.casefold().strip()
    if item.source_listing_id:
        identity = "id:" + item.source_listing_id
    elif item.listing_url:
        u = urlsplit(str(item.listing_url))
        identity = "url:" + urlunsplit((u.scheme, u.netloc.lower(), u.path, u.query, ""))
    else:
        identity = "fallback:" + json.dumps(
            [
                item.seller_id,
                item.sale_auction_id,
                item.raw_title.casefold(),
                item.raw_description.casefold(),
            ],
            ensure_ascii=False,
        )
    return hashlib.sha256((source + "|" + identity).encode()).hexdigest()


def parse_import(content, format_):
    if format_ == "json":
        rows = json.loads(content)
        if not isinstance(rows, list):
            raise ValueError("JSON import must be a list of listing objects")
    elif format_ == "csv":
        rows = list(csv.DictReader(io.StringIO(content)))
        for row in rows:
            for key in list(row):
                if row[key] == "":
                    del row[key]
            if "image_urls" in row:
                row["image_urls"] = json.loads(row["image_urls"])
    else:
        raise ValueError("Format must be json or csv")
    if len(rows) > 1000:
        raise ValueError("Import is limited to 1000 rows")
    validated = []
    for i, row in enumerate(rows, 1):
        if not isinstance(row, dict):
            raise ValueError(f"Row {i} is not an object")
        try:
            validated.append((ListingInput.model_validate(row), row))
        except ValidationError as e:
            raise ValueError(
                f"Row {i}: "
                + "; ".join(f"{'.'.join(map(str, err['loc']))}: {err['msg']}" for err in e.errors())
            ) from e
    return validated


def upsert_listing(session, item, original):
    matched = match_pattern(item, list(session.scalars(select(SearchPattern))))
    if matched and not item.search_pattern_id:
        item = item.model_copy(update={"search_pattern_id": matched.id})
    key = dedup_key(item)
    row = session.scalar(select(Listing).where(Listing.dedup_key == key))
    is_new = row is None
    if row is None:
        row = Listing(dedup_key=key, original_data=original, data=item.model_dump(mode="json"))
        session.add(row)
        if matched:
            matched.appearances += 1
    else:
        row.data = item.model_dump(mode="json")
        row.last_seen = utcnow()
    session.flush()
    return row, is_new


def import_rows(session, content, format_):
    rows = parse_import(content, format_)
    created = updated = 0
    for item, raw in rows:
        _, is_new = upsert_listing(session, item, raw)
        created += int(is_new)
        updated += int(not is_new)
    return {"created": created, "updated": updated, "rows": len(rows)}
