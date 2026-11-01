import asyncio
import base64
import json
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError
from sqlalchemy import select

from radar.db import (
    DiscoveredItem,
    DiscoveryGroup,
    DiscoveryObservation,
    DiscoveryRun,
    Listing,
    SearchExecution,
)
from radar.discovery.config import Budgets, Connection, DiscoveryError, SearchOptions
from radar.discovery.ebay import EbayAdapter, FixtureDiscoveryAdapter, normalize, parse_page
from radar.discovery.service import plan, run_discovery

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures/ebay/discovery.json"


@pytest.fixture(autouse=True)
def no_real_api_transport(monkeypatch):
    async def deny(*args, **kwargs):
        raise AssertionError("Unmocked outbound API request is forbidden in discovery tests")

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", deny)


def raw_item(n="1"):
    return json.loads(FIXTURE.read_text())["items"][n]


def connection(**updates):
    return Connection(
        mode="sandbox",
        client_id="TEST-CLIENT-ID",
        client_secret="TEST-CLIENT-SECRET",
        request_interval=0.1,
        **updates,
    )


async def no_wait(_):
    pass


def test_production_and_source_gates():
    for c in (
        Connection(mode="production", client_id="x", client_secret="y"),
        Connection(mode="production", client_id="x", client_secret="y", production_enabled=True),
        Connection(mode="production", client_id="x", client_secret="y", production_approved=True),
    ):
        with pytest.raises(DiscoveryError, match="Production requires"):
            EbayAdapter(c, production_run_approved=True)
    approved = Connection(
        mode="production",
        client_id="x",
        client_secret="y",
        production_enabled=True,
        production_approved=True,
    )
    with pytest.raises(DiscoveryError):
        EbayAdapter(approved)
    with pytest.raises(DiscoveryError):
        Connection(enabled=False).require("fixture")
    with pytest.raises(DiscoveryError):
        Connection().require("sandbox")
    with pytest.raises(DiscoveryError):
        Connection(mode="sandbox").require("sandbox")
    assert "TEST-CLIENT-SECRET" not in repr(connection())


def test_oauth_cache_and_expiry_with_mocked_credentials():
    async def check():
        calls = []
        now = [100.0]

        def respond(request):
            calls.append(request)
            assert request.url.host == "api.sandbox.ebay.com"
            if request.url.path.endswith("/token"):
                expected = base64.b64encode(b"TEST-CLIENT-ID:TEST-CLIENT-SECRET").decode()
                assert request.headers["Authorization"] == "Basic " + expected
                assert b"grant_type=client_credentials" in request.content
                return httpx.Response(
                    200,
                    json={"access_token": "TEST-BEARER", "expires_in": 120, "token_type": "Bearer"},
                )
            assert request.headers["Authorization"] == "Bearer TEST-BEARER"
            return httpx.Response(200, json={"total": 0})

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            adapter = EbayAdapter(connection(), client=client, sleep=no_wait, clock=lambda: now[0])
            assert await adapter.access_token() == "TEST-BEARER"
            assert await adapter.access_token() == "TEST-BEARER"
            assert len(calls) == 1
            now[0] = 191
            await adapter.access_token()
            assert len(calls) == 2
            assert "TEST-BEARER" not in repr(adapter.token)
            await adapter.search("nature america", SearchOptions(), 1)
            assert calls[-1].url.params["q"] == "nature america"

    asyncio.run(check())


@pytest.mark.parametrize("status", [401, 403])
def test_permission_failure_no_retry_or_secret_disclosure(status):
    async def check():
        calls = []

        def respond(request):
            calls.append(request)
            return httpx.Response(status, text="TEST-CLIENT-SECRET TEST-BEARER echoed upstream")

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            adapter = EbayAdapter(connection(), client=client, sleep=no_wait)
            result = await adapter.search("nature america", SearchOptions(), 5)
            assert len(calls) == 1 and len(result.errors) == 1
            assert str(status) in result.errors[0]
            assert "TEST-CLIENT-SECRET" not in str(result)
            assert "echoed upstream" not in str(result)

    asyncio.run(check())


def test_retry_after_and_bounded_rate_handling():
    async def check():
        calls = []
        waits = []

        async def wait(seconds):
            waits.append(seconds)

        def respond(request):
            calls.append(request)
            if len(calls) == 1:
                return httpx.Response(429, headers={"Retry-After": "3"})
            return httpx.Response(200, json={"access_token": "TEST-TOKEN", "expires_in": 3600})

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            a = EbayAdapter(connection(), client=client, sleep=wait)
            assert await a.access_token() == "TEST-TOKEN"
        assert len(calls) == 2 and 3 in waits
        calls.clear()
        waits.clear()

        def limited(request):
            calls.append(request)
            return httpx.Response(429, headers={"Retry-After": "120"})

        async with httpx.AsyncClient(transport=httpx.MockTransport(limited)) as client:
            a = EbayAdapter(connection(), client=client, sleep=wait)
            with pytest.raises(DiscoveryError, match="defer"):
                await a.access_token()
        assert len(calls) == 1 and not waits

    asyncio.run(check())


def test_retry_exhaustion_and_network_timeout():
    async def check():
        calls = []

        def unavailable(request):
            calls.append(request)
            return httpx.Response(503, headers={"Retry-After": "0"})

        async with httpx.AsyncClient(transport=httpx.MockTransport(unavailable)) as client:
            a = EbayAdapter(connection(retry_limit=2), client=client, sleep=no_wait)
            with pytest.raises(DiscoveryError):
                await a.access_token()
        assert len(calls) == 3

        def timeout(request):
            raise httpx.ReadTimeout("TEST-CLIENT-SECRET", request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(timeout)) as client:
            result = await EbayAdapter(
                connection(retry_limit=0), client=client, sleep=no_wait
            ).search("x", SearchOptions(), 1)
            assert "NETWORK" in result.errors[0] and "TEST-CLIENT-SECRET" not in result.errors[0]

    asyncio.run(check())


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {},
        {"access_token": "x", "expires_in": 0},
        {"access_token": "x", "expires_in": 100, "token_type": 7},
    ],
)
def test_malformed_oauth(payload):
    async def check():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload))
        ) as c:
            with pytest.raises(DiscoveryError):
                await EbayAdapter(connection(), client=c, sleep=no_wait).access_token()

    asyncio.run(check())


@pytest.mark.parametrize(
    "payload",
    [
        {},
        [],
        {"total": -1},
        {"total": "1"},
        {"itemSummaries": {}},
        {"itemSummaries": [None]},
        {"itemSummaries": [], "autoCorrections": {"q": 4}},
    ],
)
def test_malformed_search(payload):
    with pytest.raises(DiscoveryError):
        parse_page(payload, "nature america", 0)


def test_pagination_exact_query_correction_and_off_never_sent():
    async def check():
        calls = []

        def respond(request):
            calls.append(request)
            if request.url.path.endswith("/token"):
                return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
            assert request.url.params["q"] == "  nature america  "
            assert request.url.params["auto_correct"] == "KEYWORD"
            return httpx.Response(
                200,
                json={
                    "itemSummaries": [raw_item("1")],
                    "autoCorrections": {"q": "Native American"},
                    "total": 3,
                    "next": "https://evil.invalid/do-not-follow",
                },
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as c:
            a = EbayAdapter(connection(), client=c, sleep=no_wait)
            result = await a.search(
                "  nature america  ", SearchOptions(auto_correct="KEYWORD"), 3, 2, 1
            )
            assert len(result.pages) == 2 and all(
                p.correction_state == "CORRECTED" for p in result.pages
            )
        requests = [r for r in calls if r.method == "GET"]
        assert [r.url.params["offset"] for r in requests] == ["0", "1"]
        assert all(r.url.host == "api.sandbox.ebay.com" for r in calls)
        assert all("99669" in r.headers["X-EBAY-C-ENDUSERCTX"] for r in requests)
        assert "auto_correct" not in SearchOptions().params("nature america", 3)
        with pytest.raises(ValidationError):
            SearchOptions(auto_correct="OFF")

    asyncio.run(check())


@pytest.mark.parametrize(
    "buying_format,expected",
    [
        ("AUCTION", "buyingOptions:{AUCTION}"),
        ("FIXED_PRICE", "buyingOptions:{FIXED_PRICE}"),
        ("BOTH", "buyingOptions:{FIXED_PRICE|AUCTION}"),
    ],
)
def test_search_filters_and_sort(buying_format, expected):
    p = SearchOptions(buying_format=buying_format, min_price="5", max_price="25").params(
        "Navaho", 5
    )
    assert expected in p["filter"] and "price:[5..25]" in p["filter"]
    assert "priceCurrency:USD" in p["filter"] and p["sort"] == "newlyListed" and p["q"] == "Navaho"


def test_normalization_no_fabricated_shipping_weight_or_resale():
    item = normalize(raw_item(), "fixture", "nature america", "Native American")
    assert item.source_listing_id == "v1|SYNTHETIC-001|0"
    assert (
        item.raw_search_phrase == "nature america"
        and item.original_search_phrase == "Native American"
    )
    assert item.shipping_cost == 7 and item.shipping_status == "UNKNOWN"
    assert item.cost_status["shipping_cost"] == "ESTIMATED"
    assert item.resale_low is None and item.resale_high is None and item.stated_weight is None
    assert item.buyer_premium is None and item.sales_tax is None
    assert item.image_urls and item.synthetic and item.discovery_mode == "fixture"
    auction = normalize(raw_item("6"), "sandbox", "turqoise", "turquoise")
    assert auction.item_price == Decimal("12.50") and auction.buying_options == ["AUCTION"]
    assert auction.item_end_time is not None
    unknown = normalize(raw_item("7"), "fixture", "x", "x")
    assert unknown.item_price is None
    assert unknown.shipping_cost is None and unknown.shipping_status == "UNKNOWN"
    free = raw_item()
    free["shippingOptions"][0]["shippingCost"]["value"] = "0"
    free_item = normalize(free, "fixture", "x", "x")
    assert free_item.shipping_cost == 0 and free_item.shipping_status == "UNKNOWN"
    with pytest.raises(DiscoveryError):
        normalize({"title": "No identity"}, "fixture", "q", "q")


def test_credentials_scrubbed_before_persistence():
    async def check():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _: httpx.Response(200))
        ) as c:
            a = EbayAdapter(connection(), client=c, sleep=no_wait)
            clean = a.public_data(
                {
                    "client_secret": "TEST-CLIENT-SECRET",
                    "nested": {"access_token": "whatever"},
                    "title": "echo TEST-CLIENT-ID",
                }
            )
            assert "TEST-CLIENT" not in json.dumps(clean)
            assert clean["nested"]["access_token"] == "[REDACTED]"
            encoded = base64.b64encode(b"TEST-CLIENT-ID:TEST-CLIENT-SECRET").decode()
            assert encoded not in a.public_data("echo Basic " + encoded)
            assert "TEST-CLIENT-SECRET" not in json.dumps(
                a.public_data({"TEST-CLIENT-SECRET": "key echo"})
            )

    asyncio.run(check())


def test_fixture_plan_run_dedup_history_and_review(client):
    before = client.get("/discovery")
    assert before.status_code == 200 and "PRODUCTION DISABLED" in before.text
    assert "No discovery sessions" in before.text
    with client.app.state.sessions() as s:
        entries = plan(s, Budgets())
        assert (
            entries[0].query == "nature america" and entries[0].original_query == "Native American"
        )
        assert len(entries) == 4
    r = client.post("/discovery/run", data={"mode": "fixture", "auto_correct": "omit"})
    assert r.status_code == 200 and "NON-LIVE" in r.text
    with client.app.state.sessions() as s:
        executions = list(s.scalars(select(SearchExecution)))
        run = s.get(DiscoveryRun, 1)
        items = list(s.scalars(select(DiscoveredItem)))
        obs = list(s.scalars(select(DiscoveryObservation)))
        assert len(executions) == 4 and sum(e.results_returned for e in executions) <= 12
        assert run.new_count == len(items) > 0 and run.duplicate_count > 0
        assert run.status == "COMPLETED" and run.completed_at
        assert len(obs) == run.new_count + run.duplicate_count
        assert not list(s.scalars(select(Listing)))  # explicit owner handoff only
        nature = next(e for e in executions if e.transmitted_query == "nature america")
        assert (
            nature.parameters["q"] == "nature america" and "auto_correct" not in nature.parameters
        )
        assert nature.correction_state == "UNKNOWN"
        multiply_seen = next(i for i in items if sum(o.item_id == i.id for o in obs) > 1)
        iid = multiply_seen.id
    history = client.get(f"/discovery/items/{iid}")
    assert history.status_code == 200 and "Every discovery match" in history.text
    r = client.post(f"/discovery/items/{iid}/review")
    assert r.status_code == 200 and "Independent marketplace discovery history" in r.text
    assert "nature america" in r.text
    assert client.post(f"/discovery/items/{iid}/review").status_code == 200
    with client.app.state.sessions() as s:
        listings = list(s.scalars(select(Listing)))
        assert len(listings) == 1 and listings[0].review_state == "RESEARCH"
        assert listings[0].data["resale_low"] is None
        assert listings[0].original_data["provider"] == "eBay Browse"
    assert (
        client.post(
            "/discovery/run", data={"mode": "fixture", "auto_correct": "KEYWORD"}
        ).status_code
        == 200
    )
    with client.app.state.sessions() as s:
        run = s.get(DiscoveryRun, 2)
        assert run.new_count == 0 and run.duplicate_count > 0
        nature = list(
            s.scalars(
                select(SearchExecution).where(SearchExecution.transmitted_query == "nature america")
            )
        )[-1]
        assert nature.correction_state == "CORRECTED" and nature.corrected_queries == [
            "Native American"
        ]
        assert nature.transmitted_query == "nature america"


def test_group_toggle_budget_and_queries_editable_without_case_correction(client):
    with client.app.state.sessions() as s:
        g = s.get(DiscoveryGroup, "southwestern")
        assert "nature america" in g.data["queries"] and "Navaho" in g.data["queries"]
    r = client.post(
        "/discovery/groups/cheap-rings",
        data={
            "name": "Owner rings",
            "enabled": "true",
            "queries": "  TurqOuise  \nnature america",
            "buying_format": "AUCTION",
            "max_price": "25",
            "sort": "price",
        },
    )
    assert r.status_code == 200
    with client.app.state.sessions() as s:
        g = s.get(DiscoveryGroup, "cheap-rings")
        assert g.data["queries"][0] == "  TurqOuise  "
    assert (
        client.post(
            "/discovery/settings",
            data={
                "enabled": "true",
                "max_queries": "1",
                "max_results": "2",
                "max_pages_per_query": "1",
                "page_size": "1",
            },
        ).status_code
        == 200
    )
    client.post("/discovery/run", data={"mode": "fixture"})
    with client.app.state.sessions() as s:
        assert len(list(s.scalars(select(SearchExecution)))) == 1
        assert len(list(s.scalars(select(DiscoveryObservation)))) <= 2
    client.post("/discovery/settings", data={"max_queries": "1"})  # unchecked disables
    assert client.post("/discovery/run", data={"mode": "fixture"}).status_code == 422
    assert (
        client.post(
            "/discovery/settings", data={"enabled": "true", "max_queries": "10000"}
        ).status_code
        == 422
    )
    assert client.get("/discovery/plan?auto_correct=OFF").status_code == 422


def test_missing_credentials_production_disabled_and_environment_off(client, monkeypatch):
    assert client.post("/discovery/run", data={"mode": "sandbox"}).status_code == 422
    assert (
        client.post(
            "/discovery/run", data={"mode": "production", "approve_production": "yes"}
        ).status_code
        == 422
    )
    monkeypatch.setenv("EBAY_SOURCE_ENABLED", "false")
    assert client.post("/discovery/run", data={"mode": "fixture"}).status_code == 422
    assert "DISABLED" in client.get("/discovery").text


def test_mocked_failure_run_records_safe_errors_and_zero_success(client):
    class Broken:
        async def search(self, *args):
            raise RuntimeError("secret-credential-must-not-persist")

        async def close(self):
            pass

    run_id = asyncio.run(
        run_discovery(client.app.state.sessions, Connection(), "fixture", adapter=Broken())
    )
    with client.app.state.sessions() as s:
        run = s.get(DiscoveryRun, run_id)
        assert run.status == "FAILED" and run.errors
        assert "secret-credential" not in str(run.errors)
        assert not list(s.scalars(select(DiscoveredItem)))


def test_no_enabled_groups_means_no_run(client):
    with client.app.state.sessions.begin() as s:
        for g in s.scalars(select(DiscoveryGroup)):
            g.data = {**g.data, "enabled": False}
    assert client.post("/discovery/run", data={"mode": "fixture"}).status_code == 422
    with client.app.state.sessions() as s:
        assert not list(s.scalars(select(DiscoveryRun)))


def test_fixture_auction_filter_price_and_page_budget():
    async def check():
        a = FixtureDiscoveryAdapter()
        result = await a.search(
            "nature america", SearchOptions(buying_format="AUCTION", max_price="25"), 5
        )
        assert len(result.pages[0].items) == 1 and result.pages[0].items[0]["buyingOptions"] == [
            "AUCTION"
        ]
        result = await a.search("nature america", SearchOptions(buying_format="BOTH"), 4, 2, 1)
        assert len(result.pages) == 2 and sum(len(p.items) for p in result.pages) == 2

    asyncio.run(check())


def test_multi_query_feedback_attribution_preserved_without_fixture_success_boost(client):
    from radar.workflow import attributed_patterns, pattern_effectiveness

    client.post("/discovery/run", data={"mode": "fixture"})
    with client.app.state.sessions() as s:
        observations = list(s.scalars(select(DiscoveryObservation)))
        iid = next(
            o.item_id for o in observations if sum(v.item_id == o.item_id for v in observations) > 1
        )
    client.post(f"/discovery/items/{iid}/review")
    client.post("/listings/1/feedback", data={"action": "Purchased"})
    with client.app.state.sessions() as s:
        stats = pattern_effectiveness(s)
        assert len(stats) > 1
        assert all(g["kind"] == "NON-LIVE QUERY" for g in stats)
        assert all(g["purchased"] == 1 for g in stats)
        # Discovery fixture observations cannot inflate real query ranking/performance.
        assert all(p.successful_purchases == 0 for p in attributed_patterns(s))


def test_reported_unchanged_is_not_unknown_but_not_literal_matching_guarantee():
    page = parse_page(
        {"itemSummaries": [raw_item()], "autoCorrections": {"q": "nature america"}},
        "nature america",
        0,
    )
    assert page.correction_state == "REPORTED_UNCHANGED"
    assert page.corrected_query == "nature america"


def test_malformed_item_in_run_and_oversized_upstream_budget(client):
    from radar.discovery.ebay import SearchPage, SearchResult

    class BadItems:
        async def search(self, *args):
            return SearchResult(pages=[SearchPage(items=[{}, *([raw_item()] * 30)], offset=0)])

        async def close(self):
            pass

    rid = asyncio.run(
        run_discovery(client.app.state.sessions, Connection(), "fixture", adapter=BadItems())
    )
    with client.app.state.sessions() as s:
        run = s.get(DiscoveryRun, rid)
        assert run.invalid_count > 0 and run.new_count == 1
        executions = list(s.scalars(select(SearchExecution)))
        assert sum(e.results_returned for e in executions) <= 12
        assert "MALFORMED_ITEM" in " ".join(run.errors)


def test_redirection_never_follows_credentials():
    async def check():
        requests = []

        def respond(request):
            requests.append(request)
            return httpx.Response(302, headers={"Location": "https://evil.invalid/token"})

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as c:
            with pytest.raises(DiscoveryError, match="302"):
                await EbayAdapter(connection(), client=c, sleep=no_wait).access_token()
        assert len(requests) == 1 and requests[0].url.host == "api.sandbox.ebay.com"

    asyncio.run(check())


def test_permission_and_rate_failures_abort_remaining_queries(client):
    from radar.discovery.ebay import SearchResult

    class Denied:
        def __init__(self):
            self.calls = 0

        async def search(self, *args):
            self.calls += 1
            return SearchResult(errors=["PERMISSION: eBay HTTP 403: permissions rejected"])

        async def close(self):
            pass

    a = Denied()
    rid = asyncio.run(run_discovery(client.app.state.sessions, Connection(), "fixture", adapter=a))
    with client.app.state.sessions() as s:
        assert a.calls == 1 and s.get(DiscoveryRun, rid).status == "FAILED"
        assert len(list(s.scalars(select(SearchExecution)))) == 1


def test_invalid_json_response_never_exposes_body():
    async def check():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(200, text="<html>TEST-CLIENT-SECRET</html>")
            )
        ) as c:
            with pytest.raises(DiscoveryError, match="invalid JSON") as error:
                await EbayAdapter(connection(), client=c, sleep=no_wait).access_token()
            assert "TEST-CLIENT-SECRET" not in str(error.value)

    asyncio.run(check())


def test_jri011_exact_broad_plan_and_observation_budget_with_mocked_production(client):
    """Synthetic transport only: no real credentials, inventory or production requests."""
    from radar.db import Setting
    from radar.discovery.config import GroupInput
    from radar.discovery.service import budgets, save_group

    phrases = [
        "blue stone ring",
        "silver ring with blue stone",
        "sterling jewelry lot",
        "mixed jewelry lot",
    ]
    with client.app.state.sessions.begin() as s:
        historical = {g.id: g.data for g in s.scalars(select(DiscoveryGroup))}
        for group in s.scalars(select(DiscoveryGroup)):
            group.data = {**group.data, "enabled": False}
        group = GroupInput(
            name="SYNTHETIC JRI-011 plan",
            queries=phrases,
            options=SearchOptions(buying_format="BOTH"),
        )
        s.add(DiscoveryGroup(id="jri011-test", data=group.model_dump(mode="json")))
        s.flush()
        save_group(s, "jri011-test", group)
        s.add(
            Setting(
                id=3,
                data=Budgets(
                    max_queries=4, max_results=12, max_pages_per_query=1, page_size=3
                ).model_dump(),
            )
        )
    with client.app.state.sessions() as s:
        entries = plan(s, budgets(s))
        assert len(entries) == 4 and {e.query for e in entries} == set(phrases)
        assert all(e.group_ids == ["jri011-test"] for e in entries)
        for id_, original in historical.items():
            assert s.get(DiscoveryGroup, id_).data == {**original, "enabled": False}
    calls = []
    tokens = []

    def respond(request):
        assert request.url.host == "api.ebay.com"
        if request.url.path.endswith("/token"):
            tokens.append(request)
            return httpx.Response(
                200, json={"access_token": "SYNTHETIC-BEARER", "expires_in": 3600}
            )
        calls.append(dict(request.url.params))
        assert request.headers["X-EBAY-C-MARKETPLACE-ID"] == "EBAY_US"
        assert "zip%3D99669" in request.headers["X-EBAY-C-ENDUSERCTX"]
        shared = {
            **raw_item(),
            "itemId": "SYNTHETIC-SHARED",
            "title": "SYNTHETIC dual-option ring",
            "buyingOptions": ["AUCTION", "FIXED_PRICE"],
            "price": {"value": "25.00", "currency": "USD"},
            "currentBidPrice": {"value": "5.00", "currency": "USD"},
        }
        distinct = {
            **raw_item("6"),
            "itemId": "SYNTHETIC-" + str(len(calls)),
            "title": "SYNTHETIC auction lot",
        }
        return httpx.Response(
            200,
            json={
                "itemSummaries": [shared, {}, distinct],
                "total": 300,
                "next": "https://invalid.example/do-not-follow",
                "autoCorrections": {"q": "SYNTHETIC SOURCE CORRECTION"},
            },
        )

    async def run():
        config = Connection(
            mode="production",
            client_id="SYNTHETIC-ID",
            client_secret="SYNTHETIC-SECRET",
            production_enabled=True,
            production_approved=True,
        )
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as transport:
            adapter = EbayAdapter(
                config, production_run_approved=True, client=transport, sleep=no_wait
            )
            return await run_discovery(
                client.app.state.sessions, config, "production", approved_run=True, adapter=adapter
            )

    run_id = asyncio.run(run())
    assert len(tokens) == 1 and len(calls) == 4
    assert {p["q"] for p in calls} == set(phrases)
    assert all(
        p["limit"] == "3"
        and p["offset"] == "0"
        and p["filter"] == "buyingOptions:{FIXED_PRICE|AUCTION}"
        and "auto_correct" not in p
        for p in calls
    )
    with client.app.state.sessions() as s:
        executions = list(
            s.scalars(select(SearchExecution).where(SearchExecution.run_id == run_id))
        )
        assert all(e.results_returned == 3 and len(e.parameters["pages"]) == 1 for e in executions)
        assert sum(e.results_returned for e in executions) == 12
        run = s.get(DiscoveryRun, run_id)
        assert (run.new_count, run.duplicate_count, run.invalid_count) == (5, 3, 4)
        assert len(list(s.scalars(select(DiscoveryObservation)))) == 8
        for e in executions:
            assert e.transmitted_query in phrases
            assert e.corrected_queries == ["SYNTHETIC SOURCE CORRECTION"]
        for item in s.scalars(select(DiscoveredItem)):
            assert item.normalized["shipping_status"] == "UNKNOWN"
        shared = s.scalar(
            select(DiscoveredItem).where(DiscoveredItem.ebay_item_id == "SYNTHETIC-SHARED")
        )
        assert shared.original_data["currentBidPrice"]["value"] == "5.00"
        assert shared.original_data["price"]["value"] == "25.00"
        assert shared.normalized["buying_options"] == ["AUCTION", "FIXED_PRICE"]
