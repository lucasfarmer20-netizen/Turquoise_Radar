"""Synthetic catalog preparation tests; no source requests or owner data."""

from copy import deepcopy

import pytest
from sqlalchemy import select

from radar.anomalies import PatternInput, add_pattern, seed_patterns
from radar.db import (
    DiscoveryGroup,
    DiscoveryObservation,
    DiscoveryRun,
    SavedSourceSearch,
    SearchExecution,
    SearchPattern,
    Setting,
    database,
)
from radar.discovery.config import DiscoveryError
from radar.discovery.service import budgets, plan, seed_discovery
from radar.intake.registry import SEARCH_GROUPS, STARTER_PHRASES, seed_sources
from radar.sourcing_catalog import catalog_entries, stage_catalog_group


@pytest.fixture
def catalog_sessions(tmp_path):
    engine, sessions = database(f"sqlite:///{tmp_path / 'synthetic-catalog.db'}")
    with sessions.begin() as session:
        seed_patterns(session)
        seed_discovery(session)
        seed_sources(session)
    yield sessions
    engine.dispose()


def stored_rows(session, model):
    return deepcopy(list(session.execute(select(model.__table__)).mappings()))


def test_catalog_retains_all_exact_phrases_order_overlaps_and_starter_metadata():
    entries = catalog_entries()
    expected = list(
        dict.fromkeys(phrase for phrases in SEARCH_GROUPS.values() for phrase in phrases)
    )
    assert len(entries) == len(expected) == 65
    assert [entry["phrase"] for entry in entries] == expected
    assert {entry["phrase"] for entry in entries if entry["starter"]} == set(STARTER_PHRASES)
    by_phrase = {entry["phrase"]: entry for entry in entries}
    assert by_phrase["925 ring"]["groups"] == ["Cheap Rings", "Material and item type"]
    assert by_phrase["turqoise"]["phrase"] == "turqoise"
    assert by_phrase["navaho"]["phrase"] == "navaho"
    entries[0]["groups"].clear()
    assert catalog_entries()[0]["groups"]  # A consumer cannot mutate catalog metadata.


def test_staging_creates_only_disabled_group_and_missing_patterns(catalog_sessions):
    with catalog_sessions.begin() as session:
        session.add(Setting(id=3, data={"enabled": True, "max_queries": 2, "max_results": 6}))
        selected = session.scalar(select(SavedSourceSearch).limit(1))
        selected.selected = selected.configured = True
        session.flush()
        prior_groups = stored_rows(session, DiscoveryGroup)
        prior_searches = stored_rows(session, SavedSourceSearch)
        prior_settings = stored_rows(session, Setting)
        prior_patterns = {row["id"]: row for row in stored_rows(session, SearchPattern)}
        prior_plan = [entry.model_dump() for entry in plan(session, budgets(session))]

        phrases = ["blue stone ring", "nature america", "navaho"]
        row = stage_catalog_group(session, phrases, "Synthetic catalog draft")
        assert row.id.startswith("catalog-")
        assert row.data == {
            "name": "Synthetic catalog draft",
            "enabled": False,
            "queries": phrases,
            "options": {
                "buying_format": "BOTH",
                "min_price": None,
                "max_price": None,
                "sort": "newlyListed",
                "auto_correct": "omit",
            },
        }
        assert [entry.model_dump() for entry in plan(session, budgets(session))] == prior_plan
        assert stored_rows(session, SavedSourceSearch) == prior_searches
        assert stored_rows(session, Setting) == prior_settings
        assert [
            v for v in stored_rows(session, DiscoveryGroup) if v["id"] != row.id
        ] == prior_groups
        after_patterns = {v["id"]: v for v in stored_rows(session, SearchPattern)}
        assert {key: after_patterns[key] for key in prior_patterns} == prior_patterns
        assert session.scalar(select(SearchPattern).where(SearchPattern.phrase == "navaho"))
        assert session.scalar(select(SearchPattern).where(SearchPattern.phrase == "Navaho"))
        for model in (DiscoveryRun, DiscoveryObservation, SearchExecution):
            assert stored_rows(session, model) == []


def test_staging_preserves_owner_pattern_scope_priority_and_history(catalog_sessions):
    with catalog_sessions.begin() as session:
        pattern = add_pattern(
            session,
            PatternInput(
                phrase="blue stone ring",
                original_phrase="Owner's retained concept",
                canonical_concept="Synthetic existing owner concept",
                anomaly_category="owner-specific",
                sources=["HiBid"],
                active=False,
                priority_weight=17,
            ),
        )
        pattern.appearances = 11
        pattern.useful_matches = 4
        pattern.successful_purchases = 2
        pattern.realized_profit = "13.49"
        session.flush()
        before = stored_rows(session, SearchPattern)
        row = stage_catalog_group(session, ["blue stone ring"], "Synthetic restricted phrase")
        assert stored_rows(session, SearchPattern) == before
        row.data = {**row.data, "enabled": True}
        assert "blue stone ring" not in [entry.query for entry in plan(session, budgets(session))]


def test_repeated_submission_is_idempotent_and_never_resets_edited_group(catalog_sessions):
    with catalog_sessions.begin() as session:
        phrases = ["mixed jewelry lot", "silver ring with blue stone"]
        first = stage_catalog_group(session, phrases, "Synthetic repeat")
        before_groups = stored_rows(session, DiscoveryGroup)
        before_patterns = stored_rows(session, SearchPattern)
        second = stage_catalog_group(session, list(phrases), "Synthetic repeat")
        assert first is second
        assert stored_rows(session, DiscoveryGroup) == before_groups
        assert stored_rows(session, SearchPattern) == before_patterns
        first.data = {**first.data, "enabled": True, "queries": ["owner-edited phrase"]}
        edited = deepcopy(first.data)
        with pytest.raises(DiscoveryError, match="changed after staging") as error:
            stage_catalog_group(session, phrases, "Synthetic repeat")
        assert error.value.code == "CONFLICT"
        assert first.data == edited
        assert stored_rows(session, SearchPattern) == before_patterns
        another = stage_catalog_group(session, phrases, "Synthetic repeat 2")
        assert another.id != first.id and another.data["enabled"] is False
        assert first.data == edited


@pytest.mark.parametrize(
    "phrases,name",
    [
        ([], "Draft"),
        ("blue stone ring", "Draft"),
        (None, "Draft"),
        (["blue stone ring"] * 2, "Draft"),
        (["blue stone ring", "not in catalog"], "Draft"),
        ([" blue stone ring"], "Draft"),
        (["Blue stone ring"], "Draft"),
        (["blue stone ring", None], "Draft"),
        (["blue stone ring", ["nested"]], "Draft"),
        ([entry["phrase"] for entry in catalog_entries()[:21]], "Draft"),
        (["blue stone ring"], ""),
        (["blue stone ring"], "  "),
        (["blue stone ring"], None),
        (["blue stone ring"], 12),
        (["blue stone ring"], "X" * 101),
    ],
)
def test_invalid_submissions_do_not_write_even_when_caller_catches_error(
    catalog_sessions, phrases, name
):
    with catalog_sessions.begin() as session:
        before_groups = stored_rows(session, DiscoveryGroup)
        before_patterns = stored_rows(session, SearchPattern)
        with pytest.raises(ValueError):
            stage_catalog_group(session, phrases, name)
        assert stored_rows(session, DiscoveryGroup) == before_groups
        assert stored_rows(session, SearchPattern) == before_patterns


def test_twenty_exact_phrases_allowed_and_submission_order_preserved(catalog_sessions):
    phrases = [entry["phrase"] for entry in catalog_entries()[:20]][::-1]
    with catalog_sessions.begin() as session:
        row = stage_catalog_group(session, phrases, "Synthetic full draft")
        assert row.data["queries"] == phrases
        assert row.data["enabled"] is False
