"""Private cross-source research overlay. Leads never merge or authenticate makers."""

import argparse
import html
import itertools
import json
import re
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

from radar.references.directory import connect, current_review, digest, distinct_tokens
from radar.references.directory_sources import SOURCE_FAMILIES, SOURCES
from radar.references.recognition import normalize_text
from radar.references.store import dump, now, private_root

SCHEMA = "jri-cross-source-review-1"
KINDS = {
    "SAME_SOURCE_REPETITION",
    "INDEPENDENT_SOURCE_AGREEMENT",
    "CONFLICTING_ATTRIBUTIONS",
    "SHARED_REUSED_MARK",
    "SIMILAR_UNVERIFIED",
    "UNKNOWN",
}


def names(r):
    # Do not erase initials, suffixes, uncertainty or generational qualifiers.
    return {normalize_text(n) for n in [r["maker_name"], *r.get("aliases", [])] if n}


def lettering(r):
    if r.get("transcription_status") not in {"LEGIBLE", "REPORTED"}:
        return set()
    variants = r.get("transcription_variants") or re.split(r"[;\n]", r.get("transcription") or "")
    return {
        " ".join(t for t in normalize_text(v).split() if t in distinct_tokens(v)) for v in variants
    } - {""}


def features(a, b):
    """Explainable leads: the count/weight is neither probability nor authentication."""
    reasons = []
    if names(a) & names(b):
        reasons.append("NAME_OR_EXPLICIT_ALIAS")
    if lettering(a) & lettering(b):
        reasons.append("REPORTED_LETTERING")
    symbols = distinct_tokens(a.get("description")) & distinct_tokens(b.get("description"))
    if len(symbols - {"HALLMARK", "MARK", "SILVERSMITH", "NAVAJO", "PRINTED"}) >= 2:
        reasons.append("DESCRIPTION_WORDS_ONLY")
    if a.get("shop_identity") and normalize_text(a["shop_identity"]) == normalize_text(
        b.get("shop_identity") or ""
    ):
        reasons.append("REPORTED_SHOP")
    if a.get("period") and a["period"] == b.get("period"):
        reasons.append("REPORTED_PERIOD")
    return reasons


def family(record, review):
    return SOURCE_FAMILIES.get(record["source"], review["independence_group"])


def available(c):
    reviews, rows = {}, []
    for row in c.execute("SELECT * FROM records WHERE active=1 ORDER BY id"):
        if row["source"] not in reviews:
            try:
                reviews[row["source"]] = current_review(c, row["source"])[1]
            except ValueError:
                reviews[row["source"]] = {"facts_allowed": False}
        review = reviews[row["source"]]
        if review["facts_allowed"]:
            r = json.loads(row["data"])
            rows.append((r, family(r, review)))
    return rows


def propose(directory, query="", limit=25):
    if not 1 <= limit <= 25:
        raise ValueError("Pilot limit must be 1–25")
    with connect(directory) as c:
        rows = available(c)
    candidates = []
    for (a, fa), (b, fb) in itertools.combinations(rows, 2):
        if query and not any(
            normalize_text(query)
            in normalize_text(
                " ".join(
                    [
                        r["maker_name"],
                        " ".join(r.get("aliases", [])),
                        r.get("transcription") or "",
                        r.get("description") or "",
                        r.get("shop_identity") or "",
                    ]
                )
            )
            for r in [a, b]
        ):
            continue
        reasons = features(a, b)
        if not reasons or reasons == ["REPORTED_PERIOD"]:
            continue
        candidates.append(
            {
                "left": a["record_id"],
                "right": b["record_id"],
                "makers": [a["maker_name"], b["maker_name"]],
                "reasons": reasons,
                "same_family": fa == fb,
                "decision": "UNKNOWN",
                "authenticated": False,
            }
        )
    candidates.sort(
        key=lambda r: (
            r["same_family"],
            -sum(
                {"NAME_OR_EXPLICIT_ALIAS": 4, "REPORTED_LETTERING": 3, "REPORTED_SHOP": 2}.get(v, 1)
                for v in r["reasons"]
            ),
            r["left"],
            r["right"],
        )
    )
    return candidates[:limit]


def evidence_checked(evidence, records):
    if not evidence:
        raise ValueError("Cited evidence required")
    for e in evidence:
        if e.get("record") not in records or not e.get("quote"):
            raise ValueError("Evidence must cite a participating record")
        value = records[e["record"]].get(e.get("field"))
        if value is None or not e["quote"].strip():
            raise ValueError("Evidence field missing or quote empty")
        text = value if isinstance(value, str) else dump(value)
        if e["quote"] not in text:
            raise ValueError("Evidence quote absent from record observation")


def classify(entry, records, families):
    """Human assessments are evidence-linked; domain separation is never independence."""
    if entry.get("identity") not in {"SAME_REPORTED", "DIFFERENT_REPORTED", "UNRESOLVED"}:
        raise ValueError("Explicit identity assessment required")
    if entry.get("mark") not in {"COMPATIBLE", "SAME_STAMP", "SIMILAR", "DIFFERENT", "UNASSESSED"}:
        raise ValueError("Explicit mark assessment required")
    if entry.get("independence") not in {"UNESTABLISHED", "DEPENDENT", "DOCUMENTED_INDEPENDENT"}:
        raise ValueError("Explicit independence assessment required")
    evidence_checked(entry["support"], records)
    if {e["record"] for e in entry["support"]} != set(records):
        raise ValueError("Both sides require cited evidence")
    if not entry.get("uncertainty") or not entry.get("rationale") or not entry.get("reviewer"):
        raise ValueError("Review rationale, reviewer and uncertainty required")
    date = datetime.fromisoformat(entry["reviewed_at"])
    if date.tzinfo is None or date > datetime.now(UTC) + timedelta(minutes=5):
        raise ValueError("Dated attribution review required")
    if entry["mark"] in {"COMPATIBLE", "SAME_STAMP", "SIMILAR"}:
        mark_evidence = entry.get("mark_evidence", [])
        evidence_checked(mark_evidence, records)
        if {e["record"] for e in mark_evidence} != set(records) or any(
            e["field"] not in {"transcription", "description"} or not distinct_tokens(e["quote"])
            for e in mark_evidence
        ):
            raise ValueError("Distinctive mark evidence on both sides required")
    if entry.get("conflicting_evidence"):
        evidence_checked(entry["conflicting_evidence"], records)
    shared = entry.get("shared_evidence", [])
    if shared:
        evidence_checked(shared, records)
        if not any(e["field"] == "shared_stamp_claims" for e in shared):
            raise ValueError("Explicit source-reported shared/reused/co-stamped evidence required")
        return "SHARED_REUSED_MARK"
    if entry["mark"] == "SAME_STAMP" and entry["identity"] == "DIFFERENT_REPORTED":
        if not entry.get("conflicting_evidence"):
            raise ValueError("Conflicting attributions require cited conflicting evidence")
        return "CONFLICTING_ATTRIBUTIONS"
    same_family = len(set(families.values())) == 1
    if entry["independence"] == "DOCUMENTED_INDEPENDENT":
        provenance = entry.get("independent_provenance", [])
        evidence_checked(provenance, records)
        if (
            same_family
            or {e["record"] for e in provenance} != set(records)
            or any(e["field"] != "attribution_provenance" for e in provenance)
            or entry["identity"] != "SAME_REPORTED"
            or entry["mark"] not in {"COMPATIBLE", "SAME_STAMP"}
        ):
            raise ValueError("Independent attribution provenance on both sides required")
        return "INDEPENDENT_SOURCE_AGREEMENT"
    if same_family and entry["identity"] == "SAME_REPORTED":
        return "SAME_SOURCE_REPETITION"
    if entry["mark"] in {"COMPATIBLE", "SIMILAR", "SAME_STAMP"}:
        return "SIMILAR_UNVERIFIED"
    return "UNKNOWN"


def review(directory, entries):
    if not 1 <= len(entries) <= 25:
        raise ValueError("One bounded pilot of at most 25 relationships required")
    result, pairs = [], set()
    with connect(directory) as c:
        eligible = {r["record_id"]: (r, f) for r, f in available(c)}
        for entry in entries:
            ids = [entry["left"], entry["right"]]
            pair = tuple(sorted(ids))
            if len(set(ids)) != 2 or pair in pairs or any(i not in eligible for i in ids):
                raise ValueError("Unique pair of current eligible records required")
            pairs.add(pair)
            records = {i: eligible[i][0] for i in ids}
            families = {i: eligible[i][1] for i in ids}
            decision = classify(entry, records, families)
            result.append(
                {
                    "assessment": entry,
                    "decision": decision,
                    "records": records,
                    "families": families,
                    "authenticated": False,
                    "source_review_ids": {
                        i: current_review(c, r["source"])[0] for i, r in records.items()
                    },
                    "observation_hashes": {i: digest(dump(r).encode()) for i, r in records.items()},
                }
            )
    pack = {"schema": SCHEMA, "private": True, "relationships": result}
    raw = (dump(pack) + "\n").encode()
    path = private_root(Path(directory) / "research" / "reviews") / (digest(raw) + ".json")
    if path.exists() and path.read_bytes() != raw:
        raise ValueError("Refusing to replace reviewed evidence")
    path.write_bytes(raw)
    path.chmod(0o600)
    with connect(directory, write=True) as c:
        if not c.execute(
            "SELECT 1 FROM events WHERE event='CROSS_SOURCE_REVIEW' AND subject=?", (path.stem,)
        ).fetchone():
            c.execute(
                "INSERT INTO events(event,subject,data,created_at) VALUES (?,?,?,?)",
                (
                    "CROSS_SOURCE_REVIEW",
                    path.stem,
                    dump({"relationships": len(result), "schema": SCHEMA}),
                    now(),
                ),
            )
    return path


def verify(directory):
    from radar.references.research_facts import verify as verify_facts

    counts = Counter()
    with connect(directory) as c:
        paths = sorted((Path(directory) / "research" / "reviews").glob("*.json"))
        expected = {
            r[0] for r in c.execute("SELECT subject FROM events WHERE event='CROSS_SOURCE_REVIEW'")
        }
        if {p.stem for p in paths} != expected:
            raise ValueError("Research review missing or unregistered; resume original review")
        for p in paths:
            if p.is_symlink() or digest(p.read_bytes()) != p.stem:
                raise ValueError("Research review hash mismatch")
            data = json.loads(p.read_bytes())
            if data["schema"] != SCHEMA or not 1 <= len(data["relationships"]) <= 25:
                raise ValueError("Invalid research review")
            for row in data["relationships"]:
                for key, r in row["records"].items():
                    sha = digest(dump(r).encode())
                    observation = c.execute(
                        "SELECT data FROM observations WHERE record_id=? AND hash=?", (key, sha)
                    ).fetchone()
                    if (
                        not observation
                        or json.loads(observation[0]) != r
                        or sha != row["observation_hashes"][key]
                    ):
                        raise ValueError("Research observation missing or changed")
                    source_review = c.execute(
                        "SELECT data FROM reviews WHERE id=?", (row["source_review_ids"][key],)
                    ).fetchone()
                    if (
                        not source_review
                        or family(r, json.loads(source_review[0])) != row["families"][key]
                    ):
                        raise ValueError("Research source family changed")
                if row["decision"] != classify(row["assessment"], row["records"], row["families"]):
                    raise ValueError("Research decision inconsistent with review")
                counts[row["decision"]] += 1
    return {
        "relationships": sum(counts.values()),
        "decisions": dict(counts),
        **verify_facts(directory),
    }


def link(url, label):
    p = urlsplit(url or "")
    if p.scheme != "https" or not p.hostname or p.username or p.password:
        return html.escape(label)
    return f'<a rel="noreferrer" href="{html.escape(url, quote=True)}">{html.escape(label)}</a>'


def report(directory, review_path, output):
    verify(directory)
    review_path = Path(review_path).resolve()
    if review_path.parent != (Path(directory) / "research" / "reviews").resolve():
        raise ValueError("Stored verified research review required")
    data = json.loads(review_path.read_bytes())
    esc = lambda v: html.escape(str(v))  # noqa: E731
    parts = [
        '<!doctype html><html><head><meta charset="utf-8">',
        '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; '
        "style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'\">",
        "<title>Private hallmark research</title><style>body{font:16px system-ui;"
        "max-width:1200px;margin:2em auto;padding:1em}article{border:1px solid #aaa;"
        "padding:1em;margin:1em 0}td{vertical-align:top;padding:1em;width:50%}"
        "pre{white-space:pre-wrap;overflow-wrap:anywhere}small{color:#555}</style></head><body>",
        "<h1>Private hallmark research</h1><p>All maker identities are source attributions. "
        "UNKNOWN remains an allowed conclusion. Agreement is not authentication; "
        "different websites are not proof of independent attribution. No images load.</p>",
    ]
    with connect(directory) as c:
        eligible = {r["record_id"] for r, _ in available(c)}
        for n, row in enumerate(data["relationships"], 1):
            a = row["assessment"]
            if not set(row["records"]) <= eligible:
                parts.append(
                    f"<article><h2>{n}. UNKNOWN</h2>Source review expired, revoked, "
                    "or record excluded. Refresh review before displaying evidence.</article>"
                )
                continue
            parts.append(f"<article><h2>{n}. {esc(row['decision'])}</h2><table><tr>")
            for r in row["records"].values():
                parts.append(
                    f"<td><h3>{esc(r['maker_name'])}</h3><p>Lettering: "
                    f"{esc(r.get('transcription') or 'UNKNOWN / not transcribed')} "
                    f"({esc(r['transcription_status'])})</p>"
                    f"<p>{esc(r.get('description') or '')}</p>"
                )
                if r.get("citation"):
                    b = r["citation"]
                    parts.append(
                        f"<p>{esc(b['author'])} — {esc(b['title'])}, "
                        f"{esc(b['edition'])} edition, printed p. {esc(b['printed_page'])}; "
                        f"{esc(b['entry_id'])}.</p><small>{esc(b['bibliographic_status'])}</small>"
                    )
                else:
                    parts.append(link(r.get("source_url"), r["source"]))
                    for url in r.get("image_urls", []):
                        parts.append(" · " + link(url, "External mark reference"))
                parts.append(
                    f"<p>Period reported: {esc(r.get('period') or 'UNKNOWN')}; "
                    f"shop: {esc(r.get('shop_identity') or 'UNKNOWN')}</p>"
                    f"<p>Aliases: {esc(r.get('aliases', []))}; source qualifications: "
                    f"{esc(r.get('uncertainty', 'UNKNOWN'))}</p>"
                    f"<p>Record disagreements: {esc(r.get('contradictions', []))}</p>"
                    f"<p>Rights: {esc(r.get('permitted_use') or r['image_policy'])}</p>"
                    f"<small>Source family: {esc(row['families'][r['record_id']])}; "
                    f"record {esc(r['record_id'])}</small>"
                )
                source_review = c.execute(
                    "SELECT data FROM reviews WHERE id=?",
                    (row["source_review_ids"][r["record_id"]],),
                ).fetchone()
                observed = r.get("factual_evidence", {}).get(
                    "observed_at", "See preserved source observation"
                )
                parts.append(
                    f"<p>Source review date: "
                    f"{esc(json.loads(source_review[0])['reviewed_at'])}; "
                    f"web observation: {esc(observed)}</p></td>"
                )
            parts.append(
                "</tr></table>"
                + f"<p>Role: {esc(a.get('pilot_role', 'REVIEWED_RELATIONSHIP'))}</p>"
                + f"<p>{esc(a['rationale'])}</p>"
                f"<p>Uncertainty: {esc(a['uncertainty'])}</p>"
                f"<p>Reviewed {esc(a['reviewed_at'])} by {esc(a['reviewer'])}; "
                f"independence: {esc(a['independence'])}</p>"
            )
            for title, key in [
                ("Supporting evidence", "support"),
                ("Conflicting evidence", "conflicting_evidence"),
            ]:
                parts.append(
                    f"<h4>{title}</h4><pre>{esc(json.dumps(a.get(key, []), indent=2))}</pre>"
                )
            parts.append("</article>")
        paired = {key for row in data["relationships"] for key in row["records"]}
        parts.append("<h2>Unpaired factual references — UNKNOWN</h2>")
        for r, _ in available(c):
            if r.get("factual_evidence") and r["record_id"] not in paired:
                parts.append(
                    f"<article><h3>{esc(r['maker_name'])}</h3>"
                    f"<p>{esc(r.get('transcription') or 'UNKNOWN')} "
                    f"({esc(r['transcription_status'])}) — "
                    f"{esc(r.get('description') or '')}</p>"
                    f"{link(r['source_url'], r['source'])}"
                    "<p>No supported identity counterpart; no artificial pair created.</p>"
                    f"<p>{esc(r['image_policy'])}</p></article>"
                )
        parts.append("<h2>Research entry points and permission status</h2>")
        for name, s in SOURCES.items():
            try:
                _, r = current_review(c, name)
                status = f"{r['reviewed_at']}: {r['rationale']}"
            except ValueError:
                status = "UNKNOWN / current review required; no automated collection authorized."
            parts.append(
                f"<p>{link(s['directory_url'], name)} · "
                f"{link(s['terms_url'], 'Access policy')} — {esc(status)}</p>"
            )
            if s.get("canonical_directory_url"):
                parts.append(link(s["canonical_directory_url"], "Same source, canonical site"))
    parts.append("</body></html>")
    output = Path(output)
    if output.suffix != ".html" or output.is_symlink():
        raise ValueError("Private HTML report filename required")
    private_root(output.parent)
    output.write_text("\n".join(parts))
    output.chmod(0o600)
    return output


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("directory", type=Path)
    sub = p.add_subparsers(dest="command", required=True)
    q = sub.add_parser("propose")
    q.add_argument("--query", default="")
    q.add_argument("--limit", type=int, default=25)
    q = sub.add_parser("review")
    q.add_argument("assessment", type=Path)
    q = sub.add_parser("report")
    q.add_argument("review", type=Path)
    q.add_argument("output", type=Path)
    sub.add_parser("verify")
    a = p.parse_args()
    if a.command == "propose":
        result = propose(a.directory, a.query, a.limit)
    elif a.command == "review":
        result = str(review(a.directory, json.loads(a.assessment.read_text())))
    elif a.command == "report":
        result = str(report(a.directory, a.review, a.output))
    else:
        result = verify(a.directory)
    print(dump(result))


if __name__ == "__main__":
    main()
