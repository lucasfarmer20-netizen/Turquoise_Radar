"""Exact catalog-to-planner preparation; never enable or execute discovery."""

import json
from hashlib import sha256

from radar.db import DiscoveryGroup
from radar.discovery.config import DiscoveryError, GroupInput, SearchOptions
from radar.discovery.service import save_group
from radar.intake.registry import SEARCH_GROUPS, STARTER_PHRASES

MAX_STAGED_PHRASES = 20


def catalog_entries():
    """Return fresh display records, preserving catalog order and overlapping groups."""
    entries = {}
    for group, phrases in SEARCH_GROUPS.items():
        for phrase in phrases:
            entry = entries.setdefault(
                phrase, {"phrase": phrase, "groups": [], "starter": phrase in STARTER_PHRASES}
            )
            if group not in entry["groups"]:
                entry["groups"].append(group)
    return list(entries.values())


def stage_catalog_group(session, phrases, name):
    """Create an idempotent disabled draft without changing existing owner choices.

    The caller owns the transaction. Validation completes before any write. A
    matching earlier draft is reused only while it still equals this submission;
    an owner-edited or enabled draft is never reset by a repeated form submission.
    """
    if not isinstance(phrases, list) or not 1 <= len(phrases) <= MAX_STAGED_PHRASES:
        raise ValueError("Select between 1 and 20 exact catalog phrases")
    allowed = {entry["phrase"] for entry in catalog_entries()}
    if any(not isinstance(phrase, str) or phrase not in allowed for phrase in phrases):
        raise ValueError("Choose exact phrases from the existing search catalog")
    if len(set(phrases)) != len(phrases):
        raise ValueError("Select each exact catalog phrase only once")
    if not isinstance(name, str) or not name.strip() or len(name) > 100:
        raise ValueError("Use a nonblank draft name of at most 100 characters")

    group = GroupInput(
        name=name,
        enabled=False,
        queries=phrases,
        options=SearchOptions(buying_format="BOTH", sort="newlyListed", auto_correct="omit"),
    )
    data = group.model_dump(mode="json")
    identity = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    group_id = "catalog-" + sha256(identity.encode()).hexdigest()[:24]
    existing = session.get(DiscoveryGroup, group_id)
    if existing:
        if existing.data != data:
            raise DiscoveryError(
                "CONFLICT",
                "This catalog draft was changed after staging; use another name for a new draft",
            )
        return existing

    row = DiscoveryGroup(id=group_id, data=data)
    session.add(row)
    session.flush()
    # Existing pattern rows retain their original phrase, scope, active flag,
    # priority and feedback counters; save_group adds only missing exact phrases.
    save_group(session, group_id, group)
    return row
