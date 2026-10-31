"""Hash-only evaluation boundary. Private test pixels never belong in reference storage."""

import json
from pathlib import Path

NAME = "evaluation-exclusions.json"


def read_holdouts(root):
    path = Path(root) / NAME
    if not path.exists():
        return {"schema": "jri-holdouts-1", "hashes": [], "cases": []}
    if path.is_symlink() or path.stat().st_size > 4 * 1024 * 1024:
        raise ValueError("Invalid evaluation boundary")
    data = json.loads(path.read_text())
    if data.get("schema") != "jri-holdouts-1" or any(
        not isinstance(h, str) or len(h) != 64 or any(c not in "0123456789abcdef" for c in h)
        for h in data["hashes"]
    ):
        raise ValueError("Invalid evaluation boundary")
    return data


def assert_reference_allowed(root, *hashes):
    if set(hashes) & set(read_holdouts(root)["hashes"]):
        raise ValueError("Evaluation photograph leakage: reference/index/export use forbidden")


def register(root, case_id, hashes):
    """Caller holds the reference writer lock; registration is append-only."""
    from radar.references.similarity import write_json

    data = read_holdouts(root)
    data["hashes"] = sorted(set(data["hashes"]) | set(hashes))
    data["cases"].append({"case_id": case_id, "hashes": sorted(set(hashes))})
    write_json(Path(root) / NAME, data)
