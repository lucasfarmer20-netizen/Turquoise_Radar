"""Disposable synthetic catalog/upgrade tests; no real notification validation."""

from copy import deepcopy
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from radar.db import (
    AlertImport,
    AlertMessage,
    AlertObservation,
    DiscoveredItem,
    Feedback,
    IntakeSource,
    Listing,
    SavedSourceSearch,
    SearchPattern,
    database,
)
from radar.intake import registry
from radar.intake.service import import_alerts, promote_candidate
from radar.intake.views import search_status
from radar.main import create_app

LEGACY_GROUPS = {
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
}
HISTORY = (
    SearchPattern,
    AlertImport,
    AlertMessage,
    AlertObservation,
    DiscoveredItem,
    Listing,
    Feedback,
)


def rows(session, model):
    return deepcopy(
        {row["id"]: dict(row) for row in session.execute(select(model.__table__)).mappings()}
    )


def successes(session):
    return {v["saved"].id: v["real_matches"] for v in search_status(session)}


class PhraseInputs(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.phrases = []
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "input" and attrs.get("id", "").startswith("phrase-"):
            assert "readonly" in attrs
            self.phrases.append(attrs["value"])


def test_fresh_seed_deduplicates_groups_and_never_creates_query_history(tmp_path):
    engine, sessions = database(f"sqlite:///{tmp_path / 'fresh.db'}")
    with sessions.begin() as s:
        registry.seed_sources(s)
        before = rows(s, SavedSourceSearch)
        assert len(before) == len(registry.SOURCES) * 65
        assert not rows(s, SearchPattern)
        for source in registry.SOURCES:
            searches = {v["phrase"]: v for v in before.values() if v["source_id"] == source}
            assert len(searches) == 65
            assert not any(v["selected"] or v["configured"] for v in searches.values())
            assert searches["925 ring"]["group_name"] == "Cheap Rings"
            assert searches["jewelry jar"]["group_name"] == "Sterling Lots"
            assert searches["turqoise"]["group_name"] == "Southwestern Anomalies"
            assert {
                "blue stone ring",
                "silver ring with blue stone",
                "turqouise",
                "turquois",
            } <= searches.keys()
        registry.seed_sources(s)
        registry.seed_sources(s)
        assert rows(s, SavedSourceSearch) == before
        assert not rows(s, SearchPattern)
        assert all(v == 0 for v in successes(s).values())
    engine.dispose()


def test_startup_upgrade_preserves_all_old_rows_custom_sources_and_attribution(
    tmp_path, monkeypatch
):
    url = f"sqlite:///{tmp_path / 'upgrade.db'}"
    with monkeypatch.context() as patch:
        patch.setattr(registry, "SEARCH_GROUPS", LEGACY_GROUPS)
        with TestClient(create_app(url)) as client:
            assert (
                client.post(
                    "/sources",
                    data={
                        "source_id": "custom-old-estate",
                        "name": "SYNTHETIC old estate",
                        "domains": "estate.example.invalid",
                    },
                ).status_code
                == 200
            )
            with client.app.state.sessions.begin() as s:
                assert len(rows(s, SavedSourceSearch)) == (len(registry.SOURCES) + 1) * 21
                for i, saved in enumerate(s.scalars(select(SavedSourceSearch))):
                    saved.selected = i % 2 == 0
                    saved.configured = i % 3 == 0
                for source in ("shopgoodwill", "custom-old-estate"):
                    row = s.get(IntakeSource, source)
                    row.data = {
                        **row.data,
                        "authorization": "SYNTHETIC manual-only permission",
                        "sample_verified": True,
                        "sample_reference": "SYNTHETIC prior review",
                        "sample_kind": "OWNER_PROVIDED",
                        "notifications": "SYNTHETIC reviewed format",
                    }
                    for phrase in ("blue stone ring", " Blue stone ring ", "BLUE STONE RING"):
                        s.add(
                            SavedSourceSearch(
                                source_id=source,
                                phrase=phrase,
                                group_name="Owner original group",
                                selected=True,
                                configured=True,
                            )
                        )
                # Simulate previously verified attribution flags in a synthetic DB only.
                raw = (
                    Path("fixtures/alerts/synthetic-shop-search.eml")
                    .read_bytes()
                    .replace(b"X-Turquoise-Synthetic: true\n", b"")
                )
                import_alerts(s, "shopgoodwill", [raw])
                listing = promote_candidate(s, 1)
                s.add(
                    Feedback(
                        listing_id=listing.id, action="Interested", note="SYNTHETIC owner history"
                    )
                )
                s.flush()
                before = rows(s, SavedSourceSearch)
                sources_before = rows(s, IntakeSource)
                history_before = {m.__tablename__: rows(s, m) for m in HISTORY}
                counts_before = successes(s)
                assert sum(counts_before.values()) == 5
    # Startup, not a bespoke upgrade path, adds missing pairs to built-in and custom sources.
    for _ in range(2):
        with TestClient(create_app(url)) as client:
            with client.app.state.sessions() as s:
                after = rows(s, SavedSourceSearch)
                # Two exact case/space variants on each of two sources remain separate.
                assert len(after) == (len(registry.SOURCES) + 1) * 65 + 4
                assert {id_: after[id_] for id_ in before} == before
                assert rows(s, IntakeSource) == sources_before
                assert {m.__tablename__: rows(s, m) for m in HISTORY} == history_before
                counts_after = successes(s)
                assert {id_: counts_after[id_] for id_ in before} == counts_before
                for id_, row in after.items():
                    if id_ not in before:
                        assert not row["selected"] and not row["configured"]
                        assert counts_after[id_] == 0
                for source in sources_before:
                    assert any(
                        v["source_id"] == source and v["phrase"] == "silver ring with blue stone"
                        for v in after.values()
                    )
                if _ == 0:
                    upgraded = after
                else:
                    assert after == upgraded


def test_new_custom_source_uses_same_duplicate_safe_catalog(client, monkeypatch):
    monkeypatch.setitem(
        registry.SEARCH_GROUPS,
        "Repeated recommendations",
        [
            "blue stone ring",
            "blue stone ring",
            "925 ring",
            "turqoise",
        ],
    )
    assert (
        client.post(
            "/sources",
            data={
                "source_id": "custom-new-estate",
                "name": "SYNTHETIC new estate",
                "domains": "estate.example.invalid",
            },
        ).status_code
        == 200
    )
    with client.app.state.sessions.begin() as s:
        before = rows(s, SavedSourceSearch)
        custom = [v for v in before.values() if v["source_id"] == "custom-new-estate"]
        assert len(custom) == 65
        assert not any(v["selected"] or v["configured"] for v in custom)
        registry.seed_sources(s)
        registry.seed_searches(s, "custom-new-estate")
        assert rows(s, SavedSourceSearch) == before
    page = client.get("/saved-searches?source=custom-new-estate")
    assert PhraseInputs(page.text).phrases == list(registry.STARTER_PHRASES)


@pytest.mark.parametrize("source", ["shopgoodwill", "hibid"])
@pytest.mark.parametrize("catalog", ["all", "starter"])
def test_starter_all_group_filters_and_status_redirect_preserve_context(client, source, catalog):
    with client.app.state.sessions() as s:
        before = rows(s, SavedSourceSearch)
        patterns = rows(s, SearchPattern)
    default = client.get("/saved-searches", params={"source": source})
    assert default.status_code == 200
    assert PhraseInputs(default.text).phrases == list(registry.STARTER_PHRASES)
    assert "Showing 15 of 65" in default.text
    all_page = client.get("/saved-searches", params={"source": source, "catalog": "all"})
    assert len(PhraseInputs(all_page.text).phrases) == 65
    assert "old blue stone ring" in PhraseInputs(all_page.text).phrases
    context = {"source": source, "catalog": catalog, "group": "Plain descriptions and color"}
    page = client.get("/saved-searches", params=context)
    assert len(PhraseInputs(page.text).phrases) == (9 if catalog == "all" else 7)
    id_ = next(
        id_
        for id_, v in before.items()
        if v["source_id"] == source and v["phrase"] == "blue stone ring"
    )
    response = client.post(
        f"/saved-searches/{id_}",
        data={
            **context,
            "selected": "true",
            "configured": "true",
        },
        follow_redirects=False,
    )
    redirect = urlsplit(response.headers["location"])
    assert response.status_code == 303 and redirect.path == "/saved-searches"
    assert parse_qs(redirect.query) == {k: [v] for k, v in context.items()}
    assert redirect.fragment == f"search-{id_}"
    assert (
        PhraseInputs(client.get(response.headers["location"]).text).phrases
        == PhraseInputs(page.text).phrases
    )
    with client.app.state.sessions() as s:
        after = rows(s, SavedSourceSearch)
        assert after[id_] == {**before[id_], "selected": True, "configured": True}
        assert {k: v for k, v in after.items() if k != id_} == {
            k: v for k, v in before.items() if k != id_
        }
        assert rows(s, SearchPattern) == patterns
        assert all(v == 0 for v in successes(s).values())


def test_exact_copy_value_preserves_owner_whitespace_case_and_punctuation(client):
    phrase = '  Blue & "stone" <ring>  '
    with client.app.state.sessions.begin() as s:
        s.add(
            SavedSourceSearch(
                source_id="hibid",
                phrase=phrase,
                group_name="Owner & exact",
                selected=True,
                configured=True,
            )
        )
        s.flush()
        before = rows(s, SavedSourceSearch)
        registry.seed_sources(s)
        assert rows(s, SavedSourceSearch) == before
    response = client.get(
        "/saved-searches",
        params={
            "source": "hibid",
            "group": "Owner & exact",
            "catalog": "all",
        },
    )
    assert PhraseInputs(response.text).phrases == [phrase]
    assert "Copy exact phrase" in response.text
    assert "<ring>" not in response.text
    switched = client.get(
        "/saved-searches",
        params={
            "source": "shopgoodwill",
            "group": "Owner & exact",
            "catalog": "all",
        },
    )
    assert len(PhraseInputs(switched.text).phrases) == 65  # group belongs to the previous source
    assert client.get("/saved-searches?source=absent").status_code == 404


def test_broad_and_old_queries_keep_one_item_and_separate_observations_without_success(client):
    raw = b"""Subject: SYNTHETIC search match
X-Turquoise-Synthetic: true

Saved search: old blue stone ring
Title: SYNTHETIC blue stone ring
https://shopgoodwill.com/item/900009001
Current bid: $12.00
"""
    with client.app.state.sessions.begin() as s:
        import_alerts(
            s, "shopgoodwill", [raw, raw.replace(b"Saved search: old blue", b"Saved search: blue")]
        )
        import_alerts(s, "shopgoodwill", [raw])
        assert len(rows(s, DiscoveredItem)) == 1
        assert len(rows(s, AlertMessage)) == 2
        observations = rows(s, AlertObservation)
        assert {v["query_text"] for v in observations.values()} == {
            "old blue stone ring",
            "blue stone ring",
        }
        assert len(observations) == 2
        history = {m.__tablename__: rows(s, m) for m in HISTORY}
        registry.seed_sources(s)
        assert {m.__tablename__: rows(s, m) for m in HISTORY} == history
        assert all(v == 0 for v in successes(s).values())
