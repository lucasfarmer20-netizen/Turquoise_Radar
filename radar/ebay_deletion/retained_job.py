"""Maintenance for the original cohort plus explicitly registered worker sellers."""

import re

import httpx

from radar.discovery.image_pilot import _write_json
from radar.ebay_deletion.pilot_job import (
    SELECTOR,
    enrolled_state,
    exchange,
    inventory,
    sellers_in,
)
from radar.ebay_deletion.pilot_match import cohort_hash
from radar.ebay_deletion.processing import sanitize_database
from radar.ebay_deletion.protocol import digest


def validated(answer):
    if not isinstance(answer, dict) or set(answer) != {"retained"}:
        raise ValueError("Invalid retained answer")
    states = answer["retained"]
    if (
        not isinstance(states, dict)
        or len(states) > 100
        or any(
            not isinstance(k, str)
            or not re.fullmatch("[0-9a-f]{64}", k)
            or v not in ("CLEAR", "MATCH", "HOLD")
            for k, v in states.items()
        )
    ):
        raise ValueError("Invalid retained states")
    return states


def register(client, endpoint, token, sellers):
    states = validated(
        exchange(client, endpoint, token, {"action": "register_retained", "sellers": sellers})
    )
    if any(states.get(cohort_hash([seller])) != "CLEAR" for seller in sellers):
        raise ValueError("New seller deletion qualification not clear")


def cycle(root, client, endpoint, token):
    """Caller holds the existing exclusive worker lock and stops the feed first."""
    manifest = inventory(root)
    original = enrolled_state(root)
    unavailable = False
    try:
        answer = exchange(
            client, endpoint, token, {"action": "check", "cohort": original["cohort"]}
        )
        if (
            not isinstance(answer, dict)
            or set(answer) != {"cohort", "states"}
            or answer["cohort"] != original["cohort"]
            or not isinstance(answer["states"], list)
            or len(answer["states"]) != len(original["sellers"])
            or any(v not in ("CLEAR", "MATCH", "HOLD") for v in answer["states"])
        ):
            raise ValueError("Original cohort check invalid")
    except (ValueError, httpx.HTTPError):
        answer, unavailable = {"states": [None] * len(original["sellers"])}, True
    try:
        more = validated(exchange(client, endpoint, token, {"action": "check_retained"}))
    except (ValueError, httpx.HTTPError):
        more, unavailable = {}, True

    current = sellers_in(root)
    statuses = []
    for seller in current:
        if seller in original["sellers"]:
            status = answer["states"][original["sellers"].index(seller)]
        else:
            status = more.get(cohort_hash([seller]))
        statuses.append((seller, status))
    # All definite removals precede any unresolved identity/outage status.
    for seller, status in statuses:
        if status != "MATCH":
            continue
        outcome, _ = sanitize_database(
            root / "pilot.db",
            {"userId": digest(SELECTOR, "userId", seller["userId"])},
            SELECTOR,
            photo_reviews=manifest["photo_reviews"],
        )
        if outcome.startswith("REVIEW_") or any(
            s["userId"] == seller["userId"] for s in sellers_in(root)
        ):
            raise ValueError("Removal incomplete")
        for index, old in enumerate(original["sellers"]):
            if old is not None and old["userId"] == seller["userId"]:
                original["sellers"][index] = None
        _write_json(root / "job.json", original)
    if unavailable or any(status not in ("CLEAR", "MATCH") for _, status in statuses):
        raise ValueError("Retained identity unresolved")
