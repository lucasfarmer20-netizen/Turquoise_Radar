"""Offline, hash-pinned factual selections. No crawler, images or inferred identities."""

import json
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

from radar.references.directory import approved_url, connect, current_review, digest, upsert
from radar.references.persistence import safe_name
from radar.references.store import dump, private_root

SCHEMA = "jri-reviewed-facts-1"


def payload(root, name, expected):
    path = Path(root) / safe_name(name)
    if any(p.is_symlink() for p in [path, *path.parents]):
        raise ValueError("Symlink evidence forbidden")
    raw = path.read_bytes()
    if len(raw) > 4 * 1024 * 1024 or digest(raw) != expected:
        raise ValueError("Factual evidence hash mismatch or size exceeded")
    return raw


def packet(root, expected):
    raw = payload(root, "facts.json", expected)
    data = json.loads(raw)
    if data.get("schema") != SCHEMA or not 1 <= len(data["entries"]) <= 25:
        raise ValueError("Bounded reviewed facts packet required")
    for name, sha in data["files"].items():
        payload(root, name, sha)
    seen = set()
    for entry in data["entries"]:
        r = entry["record"]
        if r["locator"] in seen:
            raise ValueError("Duplicate factual locator")
        seen.add(r["locator"])
        text = payload(root, entry["text_file"], data["files"][entry["text_file"]]).decode()
        # An offline reviewer supplies the factual interpretation; exact excerpts
        # keep source words separate from that interpretation. No image inference.
        if not entry["quotes"] or any(not q or q not in text for q in entry["quotes"]):
            raise ValueError("Evidence quote absent from pinned source text")
        if (
            not entry.get("review_note")
            or r.get("image_urls")
            or r.get("transcription_status") not in {"REPORTED", "PARTIAL", "NOT_REVIEWED"}
            or not r.get("uncertainty")
            or not entry.get("observed_at")
        ):
            raise ValueError("Reviewed text-only source attribution required")
        observed = datetime.fromisoformat(entry["observed_at"])
        if observed.tzinfo is None or observed > datetime.now(UTC) + timedelta(minutes=5):
            raise ValueError("Timezone-aware nonfuture source observation required")
    return data


def import_facts(directory, source_root, expected):
    """All-or-nothing DB transaction; deterministic evidence copy supports reruns."""
    data = packet(source_root, expected)
    target = Path(directory) / "research" / "facts" / expected
    with connect(directory, write=True) as c:
        rid, review = current_review(c, data["source"])
        if not review["facts_allowed"] or rid != data["review_id"]:
            raise ValueError("Current matching factual review required")
        for e in data["entries"]:
            approved_url(e["record"]["source_url"], review)
        ids = []
        for entry in data["entries"]:
            r = dict(entry["record"])
            r["factual_evidence"] = {
                "packet_sha256": expected,
                "quotes": entry["quotes"],
                "review_note": entry["review_note"],
                "observed_at": entry["observed_at"],
            }
            ids.append(upsert(c, r, rid, data["source"]))
        private_root(target)
        for name in ["facts.json", *data["files"]]:
            dest = target / safe_name(name)
            dest.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            src = Path(source_root) / name
            if dest.exists() and dest.read_bytes() != src.read_bytes():
                raise ValueError("Refusing to replace factual evidence")
            if not dest.exists():
                shutil.copyfile(src, dest)
                dest.chmod(0o600)
    return {"records": ids, "images_downloaded": 0, "packet_sha256": expected}


def verify(directory):
    count = 0
    with connect(directory) as c:
        roots = sorted((Path(directory) / "research" / "facts").glob("*"))
        expected = {
            json.loads(r[0])["factual_evidence"]["packet_sha256"]
            for r in c.execute(
                "SELECT data FROM observations WHERE json_type(data,'$.factual_evidence')='object'"
            )
        }
        if {r.name for r in roots} != expected:
            raise ValueError("Factual packet missing or incomplete import; resume original packet")
        for root in roots:
            data = packet(root, root.name)
            if not c.execute("SELECT 1 FROM reviews WHERE id=?", (data["review_id"],)).fetchone():
                raise ValueError("Missing factual source review")
            for e in data["entries"]:
                r = dict(e["record"])
                key = digest((data["source"] + ":" + digest(r["locator"].encode())).encode())
                observations = c.execute(
                    "SELECT data FROM observations WHERE record_id=?", (key,)
                ).fetchall()
                if not any(
                    (o := json.loads(row[0])).get("factual_evidence", {}).get("packet_sha256")
                    == root.name
                    and all(o.get(k) == v for k, v in r.items())
                    for row in observations
                ):
                    raise ValueError("Imported factual observation missing or changed")
                count += 1
    return {"reviewed_factual_records": count}


def main():
    import argparse

    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("directory", type=Path)
    p.add_argument("packet", type=Path)
    p.add_argument("--sha256", required=True)
    a = p.parse_args()
    print(dump(import_facts(a.directory, a.packet, a.sha256)))


if __name__ == "__main__":
    main()
