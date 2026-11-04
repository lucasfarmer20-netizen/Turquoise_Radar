"""Persistent research provenance, not a deletion or historical-restoration exemption.

Setting 1 holds Preferences, Setting 3 holds Discovery budgets, and Setting 4 is
reserved for this scope marker. Only an explicitly approved bootstrap writes it;
startup and rendering never create or repair it. No schema migration is needed.
"""

import json
import os
import re
import sqlite3
from pathlib import Path
from typing import Literal
from urllib.parse import quote

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.engine import make_url

SCOPE_SETTING_ID = 4
NEW_ONLY_SCOPE = "new-only-research-1"
SCOPE_LABEL = "NEW-ONLY, REAL-EMAIL RESEARCH"


class ResearchScope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["new-only-research-1"] = Field(alias="schema")
    scope: Literal["new_only_real_email"]
    label: Literal["NEW-ONLY, REAL-EMAIL RESEARCH"]
    historical_restoration: Literal[False]
    allowed_sources: list[Literal["shopgoodwill", "hibid"]]
    qualified_messages: Literal[5]

    @model_validator(mode="after")
    def source_scope(self):
        if len(self.allowed_sources) != 2 or set(self.allowed_sources) != {"shopgoodwill", "hibid"}:
            raise ValueError("The new-only cohort has exactly two qualified sources")
        return self


def new_only_marker():
    """Return metadata for an approved bootstrap; this function performs no writes."""
    return dict(
        schema=NEW_ONLY_SCOPE,
        scope="new_only_real_email",
        label=SCOPE_LABEL,
        historical_restoration=False,
        allowed_sources=["shopgoodwill", "hibid"],
        qualified_messages=5,
    )


def inspect_scope(database_url=None, expected_scope=None):
    """Read before migrations/seeding; never create a missing expected application."""
    if expected_scope not in (None, NEW_ONLY_SCOPE):
        raise ValueError("Unknown expected research scope")
    url = make_url(database_url or os.getenv("RADAR_DATABASE_URL", "sqlite:///./data/radar.db"))
    path = Path(url.database) if url.database and url.database != ":memory:" else None
    if url.get_backend_name() != "sqlite" or path is None or not path.is_file():
        if expected_scope:
            raise ValueError("An existing marked new-only research database is required")
        return None
    try:
        with sqlite3.connect(f"file:{quote(str(path.resolve()))}?mode=ro", uri=True) as db:
            tables = {
                row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            row = (
                db.execute("SELECT data FROM settings WHERE id=?", (SCOPE_SETTING_ID,)).fetchone()
                if "settings" in tables
                else None
            )
            if row is None:
                if expected_scope:
                    raise ValueError("The required new-only research marker is absent")
                return None
            scope = ResearchScope.model_validate(json.loads(row[0]))
            required = {"listings", "discovered_items", "discovery_runs", "alert_messages"}
            if not required <= tables:
                raise ValueError("New-only research inventory is incomplete")
            for table in ("discovery_runs", "search_executions", "discovery_observations"):
                if table in tables and db.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone():
                    raise ValueError("Discovery history is outside the new-only email scope")
            if any(
                source not in {"ShopGoodwill", "HiBid"}
                for (source,) in db.execute("SELECT source FROM discovered_items")
            ):
                raise ValueError("Candidate source is outside the new-only email scope")
            for table, column in (("listings", "data"), ("discovered_items", "normalized")):
                for (raw,) in db.execute(f"SELECT {column} FROM {table}"):
                    data = json.loads(raw)
                    if (
                        data.get("source") not in {"ShopGoodwill", "HiBid"}
                        or data.get("synthetic", False)
                        or data.get("discovery_mode") is not None
                    ):
                        raise ValueError("Inventory is outside the new-only real-email scope")
            for source, synthetic in db.execute("SELECT source_id,synthetic FROM alert_messages"):
                if source not in scope.allowed_sources or synthetic:
                    raise ValueError("Messages are outside the new-only real-email scope")
            return scope
    except (ValueError, TypeError, AttributeError, sqlite3.Error, OSError):
        # Never include owner values, database paths or raw validation input in diagnostics.
        raise ValueError(
            "New-only research scope validation failed; keep this app closed"
        ) from None


def mutation_allowed(path):
    """After the approved cohort import, permit local review without new sourcing runs.

    Additional intake/source changes need a separately reviewed cohort expansion.
    Existing evidence editing cannot change a Listing's source. Photos use the
    established private upload path; analysis, live or fixture, stays blocked.
    """
    return bool(
        re.fullmatch(
            r"/(?:"
            r"listings/[0-9]+/(?:edit|feedback|state|use-comparables)"
            r"|inbox/candidates/[0-9]+/(?:review|status)"
            r"|inbox/observations/[0-9]+/confirm-query"
            r"|vision/cases/[0-9]+/photos"
            r"|vision/photos/[a-f0-9]{32}/remove"
            r"|feed/prepare|saved-searches/[0-9]+"
            r"|patterns(?:/[^/]+/toggle)?|settings|calibration"
            r")",
            path,
        )
    )
