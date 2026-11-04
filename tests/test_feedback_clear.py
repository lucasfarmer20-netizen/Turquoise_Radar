"""Neutral feedback reversals use isolated synthetic records, never owner history."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy

import pytest
from sqlalchemy import select

from radar.db import (
    DiscoveredItem,
    DiscoveryObservation,
    DiscoveryRun,
    Feedback,
    Listing,
    SearchExecution,
    SearchPattern,
)
from radar.feedback import record_feedback
from radar.schemas import FeedbackInput
from radar.search_evaluation import evaluate_searches
from radar.workflow import attributed_patterns, pattern_effectiveness


def listing(client, item, *, attributed=False):
    with client.app.state.sessions.begin() as session:
        data = item.model_dump(mode="json") | {
            "raw_title": "SYNTHETIC neutral review fixture",
            "synthetic": not attributed,
        }
        if attributed:
            pattern = SearchPattern(
                id="synthetic-clear",
                phrase="SYNTHETIC clear phrase",
                original_phrase="SYNTHETIC clear phrase",
                canonical_concept="SYNTHETIC",
                anomaly_category="manual",
            )
            session.add(pattern)
            data.update(search_pattern_id=pattern.id, raw_search_phrase=pattern.phrase)
        row = Listing(
            dedup_key="synthetic-clear",
            data=data,
            original_data={"synthetic_immutable_original": True},
            review_state="RESEARCH",
        )
        session.add(row)
        session.flush()
        if attributed:
            candidate = DiscoveredItem(
                identity="synthetic-clear-candidate",
                source=item.source,
                ebay_item_id="synthetic-clear-candidate",
                normalized=data,
                original_data={"synthetic_only": True},
                listing_id=row.id,
            )
            run = DiscoveryRun(mode="production", options={"synthetic_test_only": True})
            session.add_all([candidate, run])
            session.flush()
            execution = SearchExecution(
                run_id=run.id,
                source=item.source,
                mode="production",
                group_ids=[],
                pattern_id=pattern.id,
                original_query=pattern.phrase,
                transmitted_query=pattern.phrase,
                parameters={"synthetic_test_only": True},
            )
            session.add(execution)
            session.flush()
            session.add(
                DiscoveryObservation(
                    item_id=candidate.id,
                    execution_id=execution.id,
                    raw_data={"synthetic_only": True},
                    correction_state="UNKNOWN",
                )
            )
        return row.id


def decide(client, id_, action, receipt=None, kind="listing"):
    return client.post(
        f"/feed/review/{kind}/{id_}",
        data={"decision": action, "expected_feedback_id": receipt or ""},
        headers={"accept": "application/json"},
    )


def test_clear_passed_preserves_audit_and_is_neutral_idempotent_and_reversible(client, item):
    id_ = listing(client, item)
    passed = decide(client, id_, "NO_GO").json()
    with client.app.state.sessions() as session:
        row = session.get(Listing, id_)
        before = (deepcopy(row.data), deepcopy(row.original_data), row.last_seen)
        old = session.get(Feedback, passed["feedback_id"])
        audit = (old.action, old.note, old.created_at, old.realized_profit)
    assert decide(client, id_, "CLEAR").status_code == 409
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(lambda _: decide(client, id_, "CLEAR", passed["feedback_id"]), range(2))
        )
    assert all(result.status_code == 200 for result in results)
    assert len({result.json()["feedback_id"] for result in results}) == 1
    cleared = results[0].json()
    assert cleared["decision"] == "CLEAR" and cleared["action"] == "Cleared"
    assert cleared["state"] == "RESEARCH" and cleared["feedback_id"] != passed["feedback_id"]
    with client.app.state.sessions() as session:
        row = session.get(Listing, id_)
        assert row.review_state == "RESEARCH"
        assert (row.data, row.original_data, row.last_seen) == before
        old = session.get(Feedback, passed["feedback_id"])
        assert (old.action, old.note, old.created_at, old.realized_profit) == audit
        assert [f.action for f in session.scalars(select(Feedback).order_by(Feedback.id))] == [
            "Passed",
            "Cleared",
        ]
    assert decide(client, id_, "GO", passed["feedback_id"]).status_code == 409
    go = decide(client, id_, "GO", cleared["feedback_id"])
    assert go.status_code == 200 and go.json()["state"] == "WATCH"


@pytest.mark.parametrize("original_action", ["Interested", "Passed", "False positive"])
def test_clear_does_not_train_usefulness_or_false_positive_and_relearns_only_explicit_go(
    client,
    item,
    prefs,
    original_action,
):
    id_ = listing(client, item, attributed=True)
    with client.app.state.sessions.begin() as session:
        prior = record_feedback(
            session, session.get(Listing, id_), FeedbackInput(action=original_action)
        )
        session.flush()
        prior_id = prior.id
    clear = decide(client, id_, "CLEAR", prior_id)
    assert clear.status_code == 200
    with client.app.state.sessions() as session:
        result = evaluate_searches(session, prefs)["phrases"][0]
        assert result["reviewed_items"] == 0
        assert all(
            result[key] == 0
            for key in ("interested", "passed", "false_positive", "purchased", "sold")
        )
        group = next(
            row for row in pattern_effectiveness(session) if row["pattern"].id == "synthetic-clear"
        )
        assert group["useful"] == group["false_positives"] == group["purchased"] == 0
        pattern = next(row for row in attributed_patterns(session) if row.id == "synthetic-clear")
        assert pattern.useful_matches == pattern.successful_purchases == 0
    assert decide(client, id_, "GO", clear.json()["feedback_id"]).status_code == 200
    with client.app.state.sessions() as session:
        group = next(
            row for row in pattern_effectiveness(session) if row["pattern"].id == "synthetic-clear"
        )
        assert group["useful"] == 1 and group["false_positives"] == 0
        assert len(list(session.scalars(select(Feedback)))) == 3


def test_clear_unreviewed_candidate_does_not_promote_or_write_feedback(client, item):
    with client.app.state.sessions.begin() as session:
        candidate = DiscoveredItem(
            identity="synthetic-unreviewed",
            source=item.source,
            ebay_item_id="synthetic-id",
            normalized=item.model_copy(update={"synthetic": True}).model_dump(mode="json"),
            original_data={"synthetic_only": True},
        )
        session.add(candidate)
        session.flush()
        id_ = candidate.id
    assert decide(client, id_, "CLEAR", kind="candidate").status_code == 409
    with client.app.state.sessions() as session:
        assert session.get(DiscoveredItem, id_).listing_id is None
        assert list(session.scalars(select(Listing))) == []
        assert list(session.scalars(select(Feedback))) == []


@pytest.mark.parametrize("action", ["Purchased", "Sold"])
def test_clear_cannot_erase_purchase_history_even_if_later_passed(client, item, action):
    id_ = listing(client, item)
    with client.app.state.sessions.begin() as session:
        row = session.get(Listing, id_)
        record_feedback(session, row, FeedbackInput(action=action))
        session.flush()
        passed = record_feedback(session, row, FeedbackInput(action="Passed"))
        session.flush()
        receipt = passed.id
    assert decide(client, id_, "CLEAR", receipt).status_code == 409
    response = client.post(f"/listings/{id_}/feedback", data={"action": "Cleared"})
    assert response.status_code == 409
    with client.app.state.sessions() as session:
        assert [f.action for f in session.scalars(select(Feedback).order_by(Feedback.id))] == [
            action,
            "Passed",
        ]
        assert session.get(Listing, id_).review_state == "PASSED"


def test_legacy_feedback_cannot_bypass_guarded_clear_or_mutate_history(client, item):
    id_ = listing(client, item)
    assert client.post(f"/listings/{id_}/feedback", data={"action": "Cleared"}).status_code == 409
    passed = decide(client, id_, "NO_GO").json()
    assert (
        client.post(
            f"/listings/{id_}/feedback", data={"action": "Cleared", "realized_profit": "15"}
        ).status_code
        == 409
    )
    result = client.post(
        f"/listings/{id_}/feedback", data={"action": "Cleared"}, follow_redirects=False
    )
    assert result.status_code == 409
    assert "dedicated CLEAR button" in result.text
    repeated = client.post(
        f"/listings/{id_}/feedback", data={"action": "Cleared"}, follow_redirects=False
    )
    assert repeated.status_code == 409
    assert "<option>Cleared</option>" not in client.get(f"/listings/{id_}").text
    with client.app.state.sessions() as session:
        assert session.get(Feedback, passed["feedback_id"]).action == "Passed"
        assert len(list(session.scalars(select(Feedback)))) == 1
        assert session.get(Listing, id_).review_state == "PASSED"
    assert decide(client, id_, "CLEAR", passed["feedback_id"]).status_code == 200


def test_neutral_clear_action_cannot_report_realized_profit():
    with pytest.raises(ValueError):
        FeedbackInput(action="Cleared", realized_profit="15")


@pytest.mark.parametrize("target", ["same", "/listings/999999", "https://invalid.test/feed"])
def test_clear_html_returns_only_to_feed_or_the_same_listing(client, item, target):
    id_ = listing(client, item)
    passed = decide(client, id_, "NO_GO").json()
    target = f"/listings/{id_}" if target == "same" else target
    result = client.post(
        f"/feed/review/listing/{id_}",
        data={
            "decision": "CLEAR",
            "expected_feedback_id": passed["feedback_id"],
            "return_to": target,
        },
        follow_redirects=False,
    )
    assert result.status_code == 303
    assert result.headers["location"] == (target if target == f"/listings/{id_}" else "/feed")
