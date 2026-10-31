"""Evidence-linked ranking and conservative abstention; no learned acceptance threshold."""

import re
import unicodedata
from difflib import SequenceMatcher

from radar.references.similarity import unit

POLICY = "jri004-legible-distinctive-text-v1-visual-uncalibrated"
GENERIC = {
    "ALPACA",
    "GERMAN",
    "BRASS",
    "BRONZE",
    "PLATED",
    "PLATE",
    "NAVAJO",
    "HOPI",
    "ZUNI",
    "KEWA",
    "DINE",
    "ARGENT",
    "STERLING",
    "SILVER",
    "NICKEL",
    "COPPER",
    "SOLID",
    "GOLD",
    "GOLDFILLED",
    "FILLED",
    "GF",
    "KGF",
    "MADE",
    "IN",
    "USA",
    "US",
    "MEXICO",
    "MEXICAN",
    "TAXCO",
    "INDIAN",
    "NATIVE",
    "AMERICAN",
    "JEWELRY",
    "TRADING",
    "POST",
    "COMPANY",
    "CO",
    "NEW",
    "MEX",
}


def normalize_text(value):
    value = unicodedata.normalize("NFKD", value).upper()
    value = "".join(c for c in value if not unicodedata.combining(c))
    value = re.sub(r"(?<=[A-Z])['’](?=[A-Z])", "", value)
    return " ".join(re.findall(r"[A-Z0-9]+", value))


def informative(value, legibility):
    if legibility != "LEGIBLE":
        return set()
    return {
        t
        for t in normalize_text(value or "").split()
        if len(t) >= 3 and t.isalpha() and t not in GENERIC
    }


def families(ref):
    # Normalize spelling/punctuation only, never resolve initials or infer aliases.
    return sorted(
        {
            normalize_text(re.sub(r"\s*\([^)]*\)", "", name))
            for name in ref["candidate_makers"]
            if name.strip()
        }
    )


def rank(
    entries,
    vector,
    transcript,
    legibility,
    mode,
    quality_flags=(),
    exclude_items=(),
    exclude_assets=(),
    exclude_hashes=(),
):
    if mode not in {"visual", "text", "hybrid"}:
        raise ValueError("Unknown retrieval approach")
    vector = unit(vector)
    query_tokens = informative(transcript, legibility)
    rows = []
    strong_families = set()
    for entry in entries:
        ref = entry["reference"]
        if (
            ref["item_id"] in exclude_items
            or ref["asset_id"] in exclude_assets
            or ref["image_sha256"] in exclude_hashes
        ):
            continue
        other = unit(entry["vector"])
        if len(vector) != len(other):
            raise ValueError("Embedding dimension mismatch")
        visual = max(-1.0, min(1.0, sum(a * b for a, b in zip(vector, other, strict=True))))
        tokens = informative(ref["transcription"], ref["legibility"])
        text = 0.0
        if tokens and query_tokens:
            text = max(
                len(tokens & query_tokens) / len(tokens | query_tokens),
                0.8
                * SequenceMatcher(
                    None, " ".join(sorted(query_tokens)), " ".join(sorted(tokens))
                ).ratio(),
            )
        strong = bool(query_tokens and query_tokens <= tokens)
        if strong:
            strong_families.update(families(ref))
        if mode == "text" and text == 0:
            continue
        score = (
            visual
            if mode == "visual"
            else text
            if mode == "text"
            else (0.5 * ((visual + 1) / 2) + 0.5 * text)
        )
        rows.append(
            dict(
                ref,
                similarity=visual,
                text_score=text,
                ranking_score=score,
                candidate_families=families(ref),
                exact_legible_text_support=strong,
            )
        )
    rows.sort(key=lambda r: (-r["ranking_score"], r["reference_id"]))
    matches, seen = [], set()
    for row in rows:
        if row["item_id"] not in seen:
            matches.append(row)
            seen.add(row["item_id"])
        if len(matches) == 5:
            break
    decision, reason = "UNKNOWN", "NO_DISTINCTIVE_LEGIBLE_TEXT"
    if mode == "visual":
        reason = "VISUAL_ACCEPTANCE_UNCALIBRATED"
    elif quality_flags:
        reason = "INSUFFICIENT_IMAGE_QUALITY"
    elif len(strong_families) > 1:
        reason = "TEXT_MATCHES_MULTIPLE_ATTRIBUTED_FAMILIES"
    elif len(strong_families) == 1 and matches:
        first = matches[0]
        if (
            first["exact_legible_text_support"]
            and set(first["candidate_families"]) == strong_families
        ):
            decision, reason = "CANDIDATE", "DISTINCTIVE_LEGIBLE_TEXT_SUPPORT"
        else:
            reason = "RANKING_AND_TEXT_EVIDENCE_DISAGREE"
    return {
        "approach": mode,
        "decision": decision,
        "reason": reason,
        "matches": matches,
        "policy": POLICY,
        "authenticated": False,
        "calibrated_probability": False,
        "note": "CANDIDATE means an attributed reference to inspect, never authentication. "
        "Visual-only UNKNOWN is an uncalibrated conservative policy, not recognition success.",
    }


def summarize(cases, labels):
    """Score already-frozen rankings only; labels never enter ranking."""
    by_id = {}
    for label in labels:
        if label["case_id"] in by_id or label["target"] not in {"KNOWN", "UNKNOWN", "UNLABELLED"}:
            raise ValueError("Duplicate or invalid evaluation label")
        if label["target"] == "KNOWN" and not label.get("expected_family"):
            raise ValueError("Known query needs an evidence-linked expected family")
        if label["target"] != "UNLABELLED" and not label.get("evidence"):
            raise ValueError("Labels require evidence and its attribution limits")
        by_id[label["case_id"]] = label
    if set(by_id) - {c["case_id"] for c in cases}:
        raise ValueError("Labels refer to unknown frozen cases")
    groups = {}
    details = []
    for case in cases:
        gold = by_id.get(case["case_id"], {"target": "UNLABELLED"})
        if gold["target"] == "UNLABELLED":
            continue
        expected = normalize_text(gold.get("expected_family") or "")
        for region, approaches in case["rankings"].items():
            for mode, result in approaches.items():
                matches = result["matches"]
                good = [expected in r["candidate_families"] for r in matches] if expected else []
                known = gold["target"] == "KNOWN"
                accepted = result["decision"] == "CANDIDATE"
                first_correct = bool(known and good and good[0])
                row = {
                    "case_id": case["case_id"],
                    "origin": case["origin"],
                    "capture_group": case["capture_group"],
                    "partition": case["partition"],
                    "region": region,
                    "approach": mode,
                    "target": gold["target"],
                    "top1_correct": first_correct,
                    "hit_at_5": bool(known and any(good)),
                    "abstained": not accepted,
                    "incorrect_maker_candidates_at_5": sum(not v for v in good)
                    if known
                    else len(matches),
                    "incorrect_accepted_candidate": bool(accepted and not first_correct),
                    "label_evidence": gold.get("evidence"),
                }
                details.append(row)
                key = (case["origin"], case["partition"], region, mode)
                groups.setdefault(key, []).append(row)
    strata = []
    for (origin, partition, region, mode), rows in sorted(groups.items()):
        known = [r for r in rows if r["target"] == "KNOWN"]
        unknown = [r for r in rows if r["target"] == "UNKNOWN"]
        accepted = [r for r in rows if not r["abstained"]]
        strata.append(
            {
                "origin": origin,
                "partition": partition,
                "region": region,
                "approach": mode,
                "queries": len(rows),
                "capture_groups": len({r["capture_group"] for r in rows}),
                "known_queries": len(known),
                "unknown_queries": len(unknown),
                "top1": sum(r["top1_correct"] for r in known) / len(known) if known else None,
                "top5": sum(r["hit_at_5"] for r in known) / len(known) if known else None,
                "accepted_queries": len(accepted),
                "known_candidate_coverage": sum(not r["abstained"] for r in known) / len(known)
                if known
                else None,
                "incorrect_accepted_candidates": sum(
                    r["incorrect_accepted_candidate"] for r in rows
                ),
                "incorrect_maker_candidates_at_5": sum(
                    r["incorrect_maker_candidates_at_5"] for r in rows
                ),
                "unknown_abstention": sum(r["abstained"] for r in unknown) / len(unknown)
                if unknown
                else None,
            }
        )
    independent = [
        s
        for s in strata
        if s["origin"] == "OWNER_DECLARED_INDEPENDENT"
        and s["partition"] == "EVALUATION"
        and s["region"] == "mark"
        and s["known_queries"]
    ]
    best = max(((s["top1"], s["top5"]) for s in independent), default=None)
    observed_best = [
        {"approach": s["approach"], "top1": s["top1"], "top5": s["top5"]}
        for s in independent
        if (s["top1"], s["top5"]) == best
    ]
    return {
        "strata": strata,
        "details": details,
        "owner_declared_independent_cases": sum(
            c["origin"] == "OWNER_DECLARED_INDEPENDENT" for c in cases
        ),
        "labelled_owner_cases": sum(
            c["origin"] == "OWNER_DECLARED_INDEPENDENT"
            and by_id.get(c["case_id"], {}).get("target") in {"KNOWN", "UNKNOWN"}
            for c in cases
        ),
        "best_on_independent_photos": observed_best or None,
        "independent_improvement_established": False,
        "training_approved": False,
        "note": "Control cases and correlated views are not independent owner-photo results. "
        "Report unknown abstention together with known-query coverage; always UNKNOWN "
        "can reject every unknown while recognizing nothing.",
    }
