"""Synthetic qualification workflow; no external source access."""

import pytest

from radar.db import IntakeSource


def assessment(**updates):
    return (
        dict(
            status="USEFUL",
            checked_at="2026-11-09T12:00:00+00:00",
            access_evidence="Synthetic permitted owner sample",
            test_evidence="Synthetic import reviewed; no real yield claimed",
            freshness_notes="Snapshot only",
            fee_notes="Unknown",
            shipping_notes="99669 unverified",
            next_step="Review one permitted sample",
        )
        | updates
    )


def test_register_qualify_and_compare_without_enabling_access(client):
    assert (
        client.post(
            "/sources",
            data=dict(
                source_id="custom-review", name="Review Source", domains="review.example.org"
            ),
        ).status_code
        == 200
    )
    with client.app.state.sessions() as s:
        before = dict(s.get(IntakeSource, "custom-review").data)
    assert client.post("/sources/custom-review/qualification", data=assessment()).status_code == 200
    with client.app.state.sessions() as s:
        after = dict(s.get(IntakeSource, "custom-review").data)
    assert {k: v for k, v in after.items() if k != "qualification"} == {
        k: v for k, v in before.items() if k != "qualification"
    }
    assert not after["sample_verified"]
    page = client.get("/sources").text
    assert "Qualification: USEFUL" in page
    assert "Observed real inventory: 0 items" in page
    assert "/feed/evaluation" in page
    assert "Register and qualify sources" in client.get("/feed/evaluation").text


@pytest.mark.parametrize(
    "updates",
    [
        dict(checked_at=""),
        dict(checked_at="2026-11-09T12:00:00"),
        dict(test_evidence=" "),
        dict(access_evidence=""),
        dict(status="APPROVED"),
        dict(fee_notes="x" * 2001),
    ],
)
def test_invalid_review_is_not_saved(client, updates):
    with client.app.state.sessions() as s:
        before = dict(s.get(IntakeSource, "shopgoodwill").data)
    assert (
        client.post("/sources/shopgoodwill/qualification", data=assessment(**updates)).status_code
        == 422
    )
    with client.app.state.sessions() as s:
        assert s.get(IntakeSource, "shopgoodwill").data == before


def test_notes_are_escaped_and_unknown_source_rejected(client):
    assert client.post("/sources/missing/qualification", data=assessment()).status_code == 404
    assert (
        client.post(
            "/sources/hibid/qualification",
            data=assessment(next_step="<script>synthetic()</script>"),
        ).status_code
        == 200
    )
    page = client.get("/sources").text
    assert "<script>synthetic()</script>" not in page
    assert "&lt;script&gt;synthetic()&lt;/script&gt;" in page
