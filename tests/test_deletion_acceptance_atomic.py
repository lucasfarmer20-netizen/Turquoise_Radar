"""Deterministic concurrent deliveries, with synthetic bodies and mocked verification."""

import base64
import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from radar.ebay_deletion import receiver
from radar.ebay_deletion.protocol import event
from radar.ebay_deletion.store import Conflict, Queue

SECRET = b"synthetic-atomic-acceptance-secret-000000000000"


def overlap_unprotected_reads(monkeypatch):
    """Both unprotected existence reads finish before either caller can insert.

    A transaction holding the writer reservation serializes these reads, so it
    must not wait for a second read that SQLite correctly prevents from starting.
    This scheduling hook exposes the stale-read race without sleeps or timing
    assertions; all database operations and commits still run against real SQLite.
    """
    readers = threading.Barrier(2)
    connect = sqlite3.connect

    class GatedCursor:
        def __init__(self, cursor):
            self.cursor = cursor

        def fetchone(self):
            row = self.cursor.fetchone()
            if row is None:
                readers.wait(timeout=10)
            return row

    class GatedConnection(sqlite3.Connection):
        def execute(self, sql, parameters=()):
            cursor = super().execute(sql, parameters)
            if sql == "SELECT identifiers FROM events WHERE id=?" and not self.in_transaction:
                return GatedCursor(cursor)
            return cursor

    def gated_connect(*args, **kwargs):
        return connect(*args, **{**kwargs, "factory": GatedConnection})

    monkeypatch.setattr(sqlite3, "connect", gated_connect)


def together(function, values):
    start = threading.Barrier(2)

    def call(value):
        start.wait(timeout=10)
        return function(value)

    with ThreadPoolExecutor(max_workers=2) as workers:
        futures = [workers.submit(call, value) for value in values]
        return [future.result(timeout=15) for future in futures]


@pytest.mark.parametrize("conflicting", [False, True], ids=["identical-retry", "conflict"])
def test_concurrent_acceptance_matches_the_durable_payload(tmp_path, monkeypatch, conflicting):
    queue = Queue(tmp_path / "queue.db")
    first = {"userId": "synthetic-account-a"}
    second = {"userId": "synthetic-account-b"} if conflicting else dict(first)
    overlap_unprotected_reads(monkeypatch)

    def deliver(identifiers):
        try:
            queue.accept("synthetic-event", identifiers)
        except Conflict:
            return "conflict", identifiers
        # A different connection must observe the acknowledged payload immediately.
        persisted = Queue.existing(queue.path).events()
        return "accepted", identifiers, persisted

    results = together(deliver, [first, second])
    statuses = sorted(result[0] for result in results)
    assert statuses == (["accepted", "conflict"] if conflicting else ["accepted", "accepted"])
    durable = Queue.existing(queue.path).events()
    assert len(durable) == 1
    for result in results:
        if result[0] == "accepted":
            assert result[2] == [("synthetic-event", result[1])]
            assert durable == result[2]
    # Restarted queue cannot replace a previously accepted payload.
    with pytest.raises(Conflict):
        Queue.existing(queue.path).accept("synthetic-event", {"userId": "synthetic-third-account"})
    assert Queue.existing(queue.path).events() == durable


def notification(account):
    return {
        "metadata": {"topic": "MARKETPLACE_ACCOUNT_DELETION", "schemaVersion": "1.0"},
        "notification": {
            "notificationId": "synthetic-same-notification",
            "data": {"userId": account},
        },
    }


def test_receiver_conflicting_concurrent_delivery_has_controlled_status(tmp_path, monkeypatch):
    queue = Queue(tmp_path / "receiver.db")

    async def mock_key(_):
        return {"synthetic": True}

    app = receiver.create_receiver(
        endpoint="https://receiver.example.invalid/ebay/account-deletion",
        token="SYNTHETIC-verification-token-00000000000",
        secret=SECRET,
        queue=queue,
        keys=mock_key,
    )
    # Authenticity is mocked for this acceptance regression; all persistence and
    # FastAPI response handling are real. There are no outbound API calls.
    monkeypatch.setattr(receiver, "verify", lambda *_: None)
    signature = base64.b64encode(
        json.dumps(
            {"kid": "synthetic-key", "signature": base64.b64encode(b"synthetic-signature").decode()}
        ).encode()
    ).decode()
    overlap_unprotected_reads(monkeypatch)
    with TestClient(app) as client:

        def deliver(body):
            response = client.post(
                "/ebay/account-deletion",
                json=body,
                headers={"X-EBAY-SIGNATURE": signature},
            )
            return body, response

        results = together(deliver, [notification("synthetic-a"), notification("synthetic-b")])
    assert sorted(response.status_code for _, response in results) == [204, 412]
    durable = Queue.existing(queue.path).events()
    for body, response in results:
        if response.status_code == 204:
            event_id, identifiers = event(json.dumps(body).encode(), SECRET)
            assert durable == [(event_id, identifiers)]
        else:
            assert response.json() == {"error": "Invalid notification"}
            assert "synthetic" not in response.text
