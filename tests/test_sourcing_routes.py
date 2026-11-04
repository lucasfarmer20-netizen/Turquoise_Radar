"""Sourcing UI tests use synthetic local rows and prohibit source execution."""

from datetime import UTC, datetime, timedelta
from html.parser import HTMLParser
from urllib.parse import parse_qs, urlsplit

import pytest
from sqlalchemy import event, select

from radar.db import (
    DiscoveredItem,
    DiscoveryGroup,
    DiscoveryObservation,
    DiscoveryRun,
    Feedback,
    Listing,
    SavedSourceSearch,
    SearchExecution,
    Setting,
)
from radar.discovery.service import budgets, plan
from radar.intake.registry import SEARCH_GROUPS, STARTER_PHRASES

NOW = datetime(2026, 11, 3, 12, tzinfo=UTC)


class Tags(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.tags = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


def owner_listing(session, item, key, state="NEW"):
    row = Listing(
        dedup_key=key,
        data=item.model_dump(mode="json"),
        original_data={},
        review_state=state,
        last_seen=NOW,
    )
    session.add(row)
    session.flush()
    return row


def candidate(session, item, key, listing=None, state="NEW"):
    row = DiscoveredItem(
        identity=key,
        source=item.source,
        ebay_item_id=key,
        normalized=item.model_dump(mode="json"),
        original_data={},
        listing_id=listing.id if listing else None,
        inbox_status=state,
        last_seen=NOW,
    )
    session.add(row)
    session.flush()
    return row


def observation(session, candidate, mode, query="  turqoise ring  ", corrected=None):
    run = DiscoveryRun(mode=mode, options={})
    session.add(run)
    session.flush()
    execution = SearchExecution(
        run_id=run.id,
        source="eBay",
        mode=mode,
        group_ids=[],
        original_query=query,
        transmitted_query=query,
        parameters={},
    )
    session.add(execution)
    session.flush()
    session.add(
        DiscoveryObservation(
            execution_id=execution.id,
            item_id=candidate.id,
            raw_data={"seller": {"username": "SYNTHETIC RAW DATA NEVER RENDER"}},
            corrected_query=corrected,
            correction_state="CORRECTED" if corrected else "UNKNOWN",
            observed_at=NOW,
        )
    )
    session.flush()


def titles(response):
    assert response.status_code == 200
    return {card["item"].raw_title for card in response.context["cards"]}


def test_default_real_feed_and_explicit_nonlive_filters(client, item):
    with client.app.state.sessions.begin() as session:
        for name, modes in (
            ("Synthetic production evidence", ["production"]),
            ("Synthetic fixture evidence", ["fixture"]),
            ("Synthetic sandbox evidence", ["sandbox"]),
            ("Synthetic mixed evidence", ["production", "fixture"]),
        ):
            row = candidate(session, item.model_copy(update={"raw_title": name}), name)
            for mode in modes:
                observation(session, row, mode)
    response = client.get("/feed")
    assert titles(response) == {"Synthetic production evidence"}
    assert "Synthetic fixture evidence" not in response.text
    assert response.context["counts"] == {"real": 1, "fixture": 1, "sandbox": 1, "mixed": 1}
    for dataset in ("fixture", "sandbox", "mixed"):
        page = client.get("/feed", params={"dataset": dataset})
        assert titles(page) == {f"Synthetic {dataset} evidence"}
        assert f"{dataset.upper()} · NON-LIVE" in page.text


def test_closed_and_ended_items_remain_available_in_explicit_archive(client, item, monkeypatch):
    monkeypatch.setattr("radar.sourcing_feed.utcnow", lambda: NOW)
    with client.app.state.sessions.begin() as session:
        owner_listing(session, item.model_copy(update={"raw_title": "Open synthetic"}), "open")
        owner_listing(
            session, item.model_copy(update={"raw_title": "Passed synthetic"}), "passed", "PASSED"
        )
        ended = item.model_copy(
            update={"raw_title": "Ended synthetic", "item_end_time": NOW - timedelta(hours=1)}
        )
        candidate(session, ended, "ended")
        candidate(
            session,
            item.model_copy(update={"raw_title": "Irrelevant synthetic"}),
            "irrelevant",
            state="IRRELEVANT",
        )
    assert titles(client.get("/feed")) == {"Open synthetic"}
    assert titles(client.get("/feed?scope=archive")) == {
        "Passed synthetic",
        "Ended synthetic",
        "Irrelevant synthetic",
    }
    assert titles(client.get("/feed?scope=archive&state=PASSED")) == {"Passed synthetic"}
    assert len(titles(client.get("/feed?scope=all"))) == 4


def test_linked_card_uses_owner_state_and_latest_feedback_without_merging_manual_item(client, item):
    with client.app.state.sessions.begin() as session:
        owner = owner_listing(
            session,
            item.model_copy(update={"raw_title": "Canonical synthetic owner"}),
            "owner",
            "WATCH",
        )
        first = candidate(session, item, "linked-1", owner, state="IRRELEVANT")
        second = candidate(session, item, "linked-2", owner)
        observation(session, first, "production")
        observation(session, second, "production", query="blue stone ring")
        manual = owner_listing(session, item, "manual")
        session.add_all(
            [
                Feedback(listing_id=owner.id, action="Passed", created_at=NOW - timedelta(days=1)),
                Feedback(listing_id=owner.id, action="Interested", created_at=NOW),
            ]
        )
    response = client.get("/feed")
    assert len(response.context["cards"]) == 2
    cards = {card["listing_id"]: card for card in response.context["cards"]}
    assert cards[owner.id]["state"] == "WATCH"
    assert cards[owner.id]["candidate_ids"] == [first.id, second.id]
    assert cards[manual.id]["candidate_ids"] == []
    assert "Latest owner feedback: <strong>Interested</strong>" in response.text
    assert "Latest owner feedback: <strong>Passed</strong>" not in response.text
    assert "2 recorded observations" in response.text
    assert "owner capture" in response.text


def test_exact_query_correction_currency_and_unknown_cost_render_separately(client, item):
    data = item.model_dump(mode="json")
    data.update(currency="CAD", buying_options=["AUCTION"], shipping_status="UNKNOWN")
    data["cost_status"]["shipping_cost"] = "UNKNOWN"
    with client.app.state.sessions.begin() as session:
        row = candidate(session, type(item).model_validate(data), "foreign")
        observation(
            session, row, "production", query="  turqoise <ring>  ", corrected="turquoise ring"
        )
    response = client.get("/feed")
    assert response.status_code == 200
    assert "<code>  turqoise &lt;ring&gt;  </code>" in response.text
    assert "SOURCE CORRECTED · PRODUCTION" in response.text
    assert "Correction: CORRECTED → <code>turquoise ring</code>" in response.text
    assert "15.00 CAD" in response.text
    assert "Delivered acquisition cost</dt><dd>UNKNOWN" in response.text
    assert "Unknown cost components: shipping cost." in response.text
    assert "observed price is not a final winning bid" in response.text
    assert "Confirm Alaska 99669 shipping eligibility" in response.text
    assert "SYNTHETIC RAW DATA NEVER RENDER" not in response.text


def test_candidate_promotion_and_existing_owner_feedback_actions_work_from_feed(client, item):
    with client.app.state.sessions.begin() as session:
        row = candidate(session, item, "promote")
    before = client.get("/feed")
    assert f'action="/inbox/candidates/{row.id}/review"' in before.text
    promoted = client.post(f"/inbox/candidates/{row.id}/review", follow_redirects=False)
    assert promoted.status_code == 303
    with client.app.state.sessions() as session:
        listing_id = session.get(DiscoveredItem, row.id).listing_id
    assert promoted.headers["location"] == f"/listings/{listing_id}"
    after = client.get("/feed")
    assert len(after.context["cards"]) == 1
    assert f'action="/listings/{listing_id}/feedback"' in after.text
    assert (
        client.post(f"/listings/{listing_id}/feedback", data={"action": "Interested"}).status_code
        == 200
    )
    assert client.get("/feed").context["cards"][0]["state"] == "WATCH"
    assert (
        client.post(f"/listings/{listing_id}/feedback", data={"action": "Passed"}).status_code
        == 200
    )
    assert titles(client.get("/feed")) == set()
    archived = client.get("/feed?scope=archive")
    assert archived.context["cards"][0]["latest_feedback"]["action"] == "Passed"


def test_browse_candidate_uses_discovery_promotion_and_preserves_provider_envelope(client, item):
    original = {"itemId": "SYNTHETIC-ITEM", "seller": {"username": "SYNTHETIC-SELLER"}}
    with client.app.state.sessions.begin() as session:
        row = candidate(
            session,
            item.model_copy(update={"source": "eBay", "discovery_mode": "production"}),
            "browse-promotion",
        )
        row.original_data = original
        observation(session, row, "production")
    response = client.get("/feed")
    action = f"/discovery/items/{row.id}/review"
    assert f'action="{action}"' in response.text
    assert f'href="/discovery/items/{row.id}"' in response.text
    assert client.post(action, follow_redirects=False).status_code == 303
    with client.app.state.sessions() as session:
        linked = session.get(DiscoveredItem, row.id)
        promoted = session.get(Listing, linked.listing_id)
        assert promoted.original_data == {
            "provider": "eBay Browse",
            "mode": "production",
            "item": original,
        }
        assert linked.original_data == original


@pytest.mark.parametrize("path", ["/feed", "/feed/prepare", "/feed/evaluation"])
def test_feed_get_is_read_only_private_and_never_executes_source_adapters(
    client, item, monkeypatch, path
):
    def prohibited(*args, **kwargs):
        pytest.fail("A local feed GET must never execute discovery")

    monkeypatch.setattr("radar.discovery.service.run_discovery", prohibited)
    monkeypatch.setattr("radar.discovery.ebay.EbayAdapter.search", prohibited)
    monkeypatch.setattr("radar.discovery.ebay.FixtureDiscoveryAdapter.search", prohibited)
    with client.app.state.sessions.begin() as session:
        owner_listing(
            session,
            type(item).model_validate(
                {**item.model_dump(mode="json"), "image_urls": ["https://example.com/tracker.png"]}
            ),
            "private",
        )
    statements = []

    def record(connection, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(client.app.state.engine, "before_cursor_execute", record)
    try:
        response = client.get(path)
    finally:
        event.remove(client.app.state.engine, "before_cursor_execute", record)
    assert response.status_code == 200
    assert statements and all(
        statement.lstrip().upper().startswith("SELECT") for statement in statements
    )
    assert response.headers["cache-control"] == "private, no-store"
    assert "img-src 'self';" in response.headers["content-security-policy"]
    assert "https:" not in response.headers["content-security-policy"]
    assert not [attrs for tag, attrs in Tags(response.text).tags if tag == "img"]
    assert "tracker.png" not in response.text


def test_evaluation_links_real_evidence_without_treating_title_cues_as_execution(client, item):
    with client.app.state.sessions.begin() as session:
        row = candidate(
            session,
            item.model_copy(update={"raw_title": "TEST sterling jewelry lot <script>"}),
            "evaluation-evidence",
        )
        observation(session, row, "production", query="sterling jewelry lot")
        candidate_id = row.id
    response = client.get("/feed/evaluation")
    assert response.status_code == 200
    assert "OBSERVED SOURCE MATCH" in response.text
    assert "UNTESTED SEARCH" in response.text
    assert 'href="/discovery/items/' + str(candidate_id) + '"' in response.text
    assert "TEST sterling jewelry lot &lt;script&gt;" in response.text
    assert "No phrase is enabled or executed from this page" in response.text


def test_pagination_preserves_dataset_scope_source_state_and_sort_filters(client, item):
    with client.app.state.sessions.begin() as session:
        for number in range(26):
            owner_listing(
                session,
                item.model_copy(
                    update={"source": "Synthetic source", "raw_title": f"Synthetic {number}"}
                ),
                f"page-{number}",
                "WATCH",
            )
        owner_listing(session, item, "other-source", "WATCH")
    filters = dict(
        dataset="real", scope="all", source="Synthetic source", state="WATCH", sort="priority"
    )
    first = client.get("/feed", params=filters)
    assert len(first.context["cards"]) == 24
    following = first.context["following"]
    assert parse_qs(urlsplit(following).query) == {
        **{k: [v] for k, v in filters.items()},
        "page": ["2"],
    }
    second = client.get(following)
    assert len(second.context["cards"]) == 2
    assert not (
        {c["key"] for c in first.context["cards"]} & {c["key"] for c in second.context["cards"]}
    )
    assert parse_qs(urlsplit(second.context["previous"]).query)["source"] == ["Synthetic source"]
    assert second.context["following"] is None


def test_preparation_exposes_all_exact_catalog_choices_and_owner_source_coverage(client):
    with client.app.state.sessions.begin() as session:
        row = session.scalar(
            select(SavedSourceSearch).where(SavedSourceSearch.source_id == "ctbids")
        )
        row.selected = row.configured = True
    response = client.get("/feed/prepare")
    inputs = [
        attrs
        for tag, attrs in Tags(response.text).tags
        if tag == "input" and attrs.get("name") == "phrase"
    ]
    expected = list(
        dict.fromkeys(phrase for phrases in SEARCH_GROUPS.values() for phrase in phrases)
    )
    assert [attrs["value"] for attrs in inputs] == expected
    assert len(inputs) == 65 and all(attrs["type"] == "checkbox" for attrs in inputs)
    assert response.text.count('class="feed-starter"') == len(STARTER_PHRASES)
    source = next(source for source in response.context["sources"] if source["row"].id == "ctbids")
    assert source["selected"] == source["configured"] == 1
    assert "first real Browse run needs separate owner approval" in response.text


def test_post_stages_disabled_exact_group_preserves_plan_settings_and_is_idempotent(client):
    with client.app.state.sessions.begin() as session:
        session.add(Setting(id=3, data={"enabled": True, "max_queries": 2, "max_results": 6}))
        prior_plan = [entry.model_dump() for entry in plan(session, budgets(session))]
        prior_groups = {row.id: row.data for row in session.scalars(select(DiscoveryGroup))}
        prior_settings = {row.id: row.data for row in session.scalars(select(Setting))}
    data = {"name": "Synthetic exact catalog", "phrase": ["navaho", "nature america"]}
    response = client.post("/feed/prepare", data=data, follow_redirects=False)
    assert response.status_code == 303
    group_id = parse_qs(urlsplit(response.headers["location"]).query)["group"][0]
    confirmation = client.get(response.headers["location"])
    assert "Disabled — review before enabling" in confirmation.text
    with client.app.state.sessions() as session:
        row = session.get(DiscoveryGroup, group_id)
        assert row.data["enabled"] is False
        assert row.data["queries"] == data["phrase"]
        assert row.data["options"]["buying_format"] == "BOTH"
        assert {r.id: r.data for r in session.scalars(select(Setting))} == prior_settings
        assert {
            r.id: r.data for r in session.scalars(select(DiscoveryGroup)) if r.id != group_id
        } == prior_groups
        assert [entry.model_dump() for entry in plan(session, budgets(session))] == prior_plan
        assert not session.scalar(select(DiscoveryRun))
    assert (
        client.post("/feed/prepare", data=data, follow_redirects=False).headers["location"]
        == response.headers["location"]
    )


def test_post_edited_draft_conflicts_without_overwriting_owner_choices(client):
    data = {"name": "Synthetic edit conflict", "phrase": ["blue stone ring"]}
    response = client.post("/feed/prepare", data=data, follow_redirects=False)
    group_id = parse_qs(urlsplit(response.headers["location"]).query)["group"][0]
    with client.app.state.sessions.begin() as session:
        row = session.get(DiscoveryGroup, group_id)
        row.data = {**row.data, "enabled": True, "name": "Owner renamed"}
    conflict = client.post("/feed/prepare", data=data)
    assert conflict.status_code == 409
    with client.app.state.sessions() as session:
        assert session.get(DiscoveryGroup, group_id).data["name"] == "Owner renamed"
        assert session.get(DiscoveryGroup, group_id).data["enabled"] is True


@pytest.mark.parametrize("phrases", [[], ["blue stone ring", "not a catalog phrase"]])
def test_invalid_prepare_form_rejected_without_new_group(client, phrases):
    with client.app.state.sessions() as session:
        before = list(session.scalars(select(DiscoveryGroup.id)))
    response = client.post("/feed/prepare", data={"name": "Synthetic invalid", "phrase": phrases})
    assert response.status_code == 422
    with client.app.state.sessions() as session:
        assert list(session.scalars(select(DiscoveryGroup.id))) == before


@pytest.mark.parametrize(
    "headers", [{"Origin": "https://untrusted.example"}, {"Sec-Fetch-Site": "cross-site"}]
)
def test_cross_origin_catalog_mutation_is_rejected(client, headers):
    response = client.post(
        "/feed/prepare",
        data={"name": "Synthetic blocked", "phrase": "blue stone ring"},
        headers=headers,
    )
    assert response.status_code == 403
    with client.app.state.sessions() as session:
        assert not any(
            row.id.startswith("catalog-") for row in session.scalars(select(DiscoveryGroup))
        )


@pytest.mark.parametrize("query", ["dataset=unknown", "page=0"])
def test_invalid_feed_filters_fail_explicitly(client, query):
    assert client.get("/feed?" + query).status_code == 422
