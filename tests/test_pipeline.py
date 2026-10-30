import json
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from radar.anomalies import PatternInput, add_pattern, generate_queries
from radar.db import Feedback, Listing, SearchPattern
from radar.schemas import ListingInput, Preferences
from radar.sources import FixtureAdapter, dedup_key, import_rows

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


def test_nature_america_standalone_priority_and_exact_preservation(client):
    with client.app.state.sessions() as s:
        patterns = list(s.scalars(select(SearchPattern)))
        queries = generate_queries(patterns)
        assert queries[0]["query_sent"] == "nature america"
        assert queries[0]["original_phrase"] == "Native American"
        assert queries[0]["transformed_phrase"] == "nature america"
        assert len(generate_queries(patterns, limit=3)) == 3
        assert all(p.successful_purchases == 0 for p in patterns)


def test_query_dedup_and_disable_source_scope(client):
    with client.app.state.sessions.begin() as s:
        data = PatternInput(
            phrase="odd ring",
            original_phrase="ring",
            canonical_concept="ring",
            anomaly_category="manual",
            sources=["approved"],
        )
        p = add_pattern(s, data)
        assert add_pattern(s, data).id == p.id
        assert len(generate_queries([p, p], source="approved")) == 1
        assert generate_queries([p], source="other") == []
        p.active = False
        assert generate_queries([p], source="approved") == []


def test_source_listing_dedup_and_original_preservation(client, item):
    with client.app.state.sessions.begin() as s:
        raw = item.model_dump(mode="json")
        import_rows(s, json.dumps([raw, raw]), "json")
        updated = dict(raw, item_price="19", raw_title="Revised title")
        import_rows(s, json.dumps([updated]), "json")
        rows = list(s.scalars(select(Listing)))
        assert len(rows) == 1
        assert rows[0].data["item_price"] == "19"
        assert rows[0].original_data["item_price"] == "15"
        assert rows[0].last_seen >= rows[0].first_seen


def test_fallback_dedup(item):
    no_id = item.model_copy(update={"source_listing_id": None})
    assert dedup_key(no_id) == dedup_key(no_id.model_copy(update={"item_price": Decimal(22)}))
    assert dedup_key(no_id) != dedup_key(no_id.model_copy(update={"seller_id": "other"}))


def test_entire_synthetic_pipeline_and_routes(client):
    response = client.post(
        "/import", data={"format": "json", "content": (FIXTURES / "demo.json").read_text()}
    )
    assert response.status_code == 200 and "25 new" in response.text
    with client.app.state.sessions.begin() as s:
        from radar.db import Setting

        prefs = Preferences.model_validate_json((FIXTURES / "demo_settings.json").read_text())
        s.add(Setting(id=1, data=prefs.model_dump(mode="json")))
    dashboard = client.get("/")
    assert dashboard.status_code == 200
    assert dashboard.text.count('<article class="listing-card">') == 25
    assert "nature america" in dashboard.text
    assert "BUY CANDIDATE" in dashboard.text
    assert "SYNTHETIC DEMO" in dashboard.text
    detail = client.get("/listings/1")
    assert detail.status_code == 200 and "Native American" in detail.text
    assert "Exact phrase sent" in detail.text
    assert "$21.00" in detail.text and "$16.89" in detail.text
    assert client.get("/health").json()["status"] == "ok"
    for route in ("/patterns", "/entry", "/settings"):
        assert client.get(route).status_code == 200
    assert client.get("/listings/999").status_code == 404
    assert client.get("/static/vendor/htmx.min.js").status_code == 200
    # A repeat import updates observations without duplicate rows/counters.
    assert (
        "0 new"
        in client.post("/import", data={"content": (FIXTURES / "demo.json").read_text()}).text
    )
    with client.app.state.sessions() as s:
        assert len(list(s.scalars(select(Listing)))) == 25
        assert len(list(s.scalars(select(Feedback)))) == 0
        p = s.scalar(select(SearchPattern).where(SearchPattern.phrase == "nature america"))
        assert p.appearances == 1 and p.successful_purchases == 0


@pytest.mark.parametrize(
    "query,expected",
    [
        ("opportunity=STERLING%20%2F%20MELT", 2),
        ("shipping=UNKNOWN", 2),
        ("anomaly=autocorrect", 1),
        ("source=DEMO%20Poshmark", 4),
        ("min_cost=100", 1),
        ("max_cost=0", 0),
    ],
)
def test_filters(client, query, expected):
    from radar.db import Setting

    with client.app.state.sessions.begin() as s:
        s.add(Setting(id=1, data=json.loads((FIXTURES / "demo_settings.json").read_text())))
        import_rows(s, (FIXTURES / "demo.json").read_text(), "json")
    r = client.get("/?" + query, headers={"HX-Request": "true"})
    assert r.status_code == 200
    assert r.text.count('<article class="listing-card">') == expected
    assert "<!doctype" not in r.text


def test_csv_and_file_upload(client):
    r = client.post(
        "/import",
        data={"format": "csv"},
        files={"file": ("example.csv", (FIXTURES / "example.csv").read_bytes(), "text/csv")},
    )
    assert r.status_code == 200 and "1 new" in r.text


def test_import_atomic_validation(client, item):
    valid = item.model_dump(mode="json")
    r = client.post("/import", data={"content": json.dumps([valid, dict(valid, item_price="-1")])})
    assert r.status_code == 422
    with client.app.state.sessions() as s:
        assert list(s.scalars(select(Listing))) == []
    assert client.post("/import", data={"content": "[null]"}).status_code == 422
    assert client.post("/import", data={"content": "not json"}).status_code == 422
    assert client.get("/?min_cost=NaN").status_code == 422


def test_manual_entry_and_xss_escaping(client):
    r = client.post(
        "/listings", data={"source": "manual", "raw_title": "<script>alert(1)</script>"}
    )
    assert r.status_code == 200
    assert "&lt;script&gt;" in r.text
    assert "<script>alert(1)</script>" not in r.text
    assert client.post("/listings", data={"source": "manual", "raw_title": ""}).status_code == 422
    assert (
        client.post(
            "/listings",
            data={"payload": '{"source":"m","raw_title":"x","listing_url":"javascript:alert(1)"}'},
        ).status_code
        == 422
    )


def test_feedback_and_learned_counters(client, item):
    raw = item.model_dump(mode="json") | {
        "raw_search_phrase": "nature america",
        "original_search_phrase": "Native American",
    }
    client.post("/listings", data={"payload": json.dumps(raw)})
    for action in ("Interested", "Purchased", "Purchased", "Passed", "False positive"):
        assert client.post("/listings/1/feedback", data={"action": action}).status_code == 200
    for profit in ("20", "20", "25"):
        assert (
            client.post(
                "/listings/1/feedback", data={"action": "Sold", "realized_profit": profit}
            ).status_code
            == 200
        )
    with client.app.state.sessions() as s:
        p = s.scalar(select(SearchPattern).where(SearchPattern.phrase == "nature america"))
        assert p.useful_matches == 1 and p.successful_purchases == 1
        assert p.realized_profit == "25"
    assert (
        client.post("/listings/1/feedback", data={"action": "Buy automatically"}).status_code == 422
    )
    assert (
        client.post(
            "/listings/1/feedback", data={"action": "Purchased", "realized_profit": "20"}
        ).status_code
        == 422
    )
    assert client.post("/listings/999/feedback", data={"action": "Interested"}).status_code == 404


def test_settings_and_patterns(client):
    prefs = Preferences().model_dump(mode="json")
    prefs["min_profit"] = "20"
    assert client.post("/settings", data={"payload": json.dumps(prefs)}).status_code == 200
    from html import unescape

    assert '"20"' in unescape(client.get("/settings").text)
    prefs["spot_usd_per_troy_oz"] = "30"
    assert client.post("/settings", data={"payload": json.dumps(prefs)}).status_code == 422
    r = client.post(
        "/patterns",
        data={
            "phrase": "my odd ring",
            "original_phrase": "ring",
            "canonical_concept": "ring",
            "anomaly_category": "manual",
            "sources": "manual",
        },
    )
    assert r.status_code == 200 and "my odd ring" in r.text
    with client.app.state.sessions() as s:
        p = s.scalar(select(SearchPattern).where(SearchPattern.phrase == "my odd ring"))
        pid = p.id
    assert client.post(f"/patterns/{pid}/toggle").status_code == 200
    with client.app.state.sessions() as s:
        assert s.get(SearchPattern, pid).active is False


def test_cross_origin_mutation_denied(client):
    assert (
        client.post("/listings", headers={"Origin": "https://foreign.invalid"}).status_code == 403
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"cultural_attribution": "Navajo"},
        {"weight_verified": True},
        {"resale_low": "20"},
        {"item_price": "NaN"},
        {"shipping_cost": "-1"},
    ],
)
def test_input_evidence_and_value_validation(changes):
    with pytest.raises(ValidationError):
        ListingInput.model_validate({"source": "manual", "raw_title": "Test", **changes})


def test_fixture_adapter_exact_query():
    import asyncio

    adapter = FixtureAdapter(json.loads((FIXTURES / "demo.json").read_text()))
    found = asyncio.run(adapter.retrieve("nature america"))
    assert len(found) == 2  # duplicate fixture observations, dedup occurs on ingestion
    assert all(i.raw_search_phrase == "nature america" for i in found)
    assert asyncio.run(adapter.retrieve("Native American")) == []


def test_exact_query_preserves_case_spaces_and_effective_query(client, item):
    raw = item.model_dump(mode="json") | {
        "raw_search_phrase": "  nature america  ",
        "original_search_phrase": " Native American ",
        "effective_query": "Nature America",
    }
    client.post("/listings", data={"payload": json.dumps(raw)})
    with client.app.state.sessions() as s:
        listing = s.get(Listing, 1)
        assert listing.data["raw_search_phrase"] == "  nature america  "
        assert listing.data["original_search_phrase"] == " Native American "
        assert listing.data["effective_query"] == "Nature America"
        assert listing.data["search_pattern_id"] is None  # no silent trim to match
