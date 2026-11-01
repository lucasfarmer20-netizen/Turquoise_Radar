"""Explainable likely matches. One permitted source suffices; no authentication gate."""

import re
from difflib import SequenceMatcher

from radar.references.directory import digest, distinct_tokens
from radar.references.recognition import normalize_text

POLICY = "jri005-owner-likely-maker-v1"
SYMBOLS = {
    "bell": {"BELL"},
    "bear": {"BEAR"},
    "bird": {"BIRD", "EAGLE"},
    "feather": {"FEATHER"},
    "human profile": {"PROFILE", "HEAD"},
    "sunburst": {"SUNBURST", "RADIATING"},
    "arrow": {"ARROW", "ARROWS"},
    "butterfly": {"BUTTERFLY"},
    "rainbow arc": {"RAINBOW", "ARC"},
    "animal": {"DOG", "HORSE", "ANIMAL"},
    "triangle": {"TRIANGLE", "TRIANGULAR"},
}
CONSTRUCTION = {
    "INLAY",
    "CHANNEL",
    "TWISTED",
    "WIRE",
    "CAST",
    "STAMPED",
    "OVERLAY",
    "APPLIED",
    "BEZEL",
    "SPLIT",
    "SHANK",
    "SCALLOPED",
    "HAMMERED",
}
FILLER = {"BY", "HANDMADE", "COPYRIGHT"}


def letters(text):
    return " ".join(
        t
        for t in normalize_text(text or "").split()
        if t in distinct_tokens(text) and t not in FILLER
    )


def maker_key(name):
    # Remove only presentation qualifiers, not surnames, initials or Sr/Jr.
    name = re.sub(r"\s*\((?:gallery|source) attribution\)", "", name, flags=re.I)
    return normalize_text(name)


def signature(query):
    text = letters(query.get("transcription")) if query.get("legibility") != "UNREADABLE" else ""
    symbols = sorted(query.get("symbols", []))
    if len(text.replace(" ", "")) < 2 and not symbols:
        photo_hash = query.get("photo_hash") or ""
        return (
            digest(("photo:" + photo_hash).encode())
            if re.fullmatch(r"[a-f0-9]{64}", photo_hash)
            else None
        )
    return digest((text + ":" + ",".join(symbols)).encode())


def rank(records, query, visual=(), feedback=()):
    """Internal ordering points are heuristics; never displayed as probabilities."""
    text = letters(query.get("transcription")) if query.get("legibility") != "UNREADABLE" else ""
    informative = len(text.replace(" ", "")) >= 2
    generic_only = bool(query.get("transcription", "").strip()) and not informative
    symbol_words = set().union(*(SYMBOLS[k] for k in query.get("symbols", []) if k in SYMBOLS))
    construction = set(normalize_text(query.get("construction", "")).split()) & CONSTRUCTION
    visuals = {r["reference_id"]: r for r in visual}
    groups = {}
    for r in records:
        supports, gaps = [], list(r.get("contradictions", []))
        value, exact, partial = 0.0, False, False
        variants = r.get("transcription_variants") or re.split(
            "[;\n]", r.get("transcription") or ""
        )
        variants = [letters(v) for v in variants if letters(v)]
        usable = r.get("transcription_status") in {"LEGIBLE", "REPORTED", "PARTIAL"}
        if informative and usable:
            for v in variants:
                compact, target = text.replace(" ", ""), v.replace(" ", "")
                if compact == target:
                    exact, value = True, max(value, 60)
                    supports.append(
                        f"Lettering “{query['transcription']}” agrees with reported “{v}”."
                    )
                elif len(compact) >= 4 and (
                    compact in target or target in compact and len(target) >= 4
                ):
                    partial, value = True, max(value, 40)
                    supports.append(f"Part of the reported lettering agrees: “{v}”.")
                    gaps.append(
                        "The query and reference do not show the same complete inscription."
                    )
                elif query.get("legibility") == "PARTIAL" and len(compact) >= 3:
                    similarity = SequenceMatcher(None, compact, target).ratio()
                    if similarity >= 0.7:
                        partial, value = True, max(value, 24 * similarity)
                        supports.append(
                            f"Possible worn-letter reading near “{v}”; inspect each letter."
                        )
                        gaps.append(f"Lettering differs: query “{text}”, reference “{v}”.")
        names = [r["maker_name"], *r.get("aliases", [])]
        if (
            informative
            and len(text.replace(" ", "")) >= 4
            and any(set(text.split()) <= set(normalize_text(n).split()) for n in names)
        ):
            value = max(value, 26)
            supports.append("Visible name text overlaps the published maker attribution.")
            if not usable:
                gaps.append(
                    "One cited variant has no reviewed literal transcription; "
                    "inspect that reference."
                )
        description = re.sub(
            r"\b(?:not|no|without)\b[^.;\n]*", "", r.get("description") or "", flags=re.I
        )
        words = set(normalize_text(description).split())
        shared_symbols = symbol_words & words
        if shared_symbols:
            value += 24
            supports.append(
                "Pictorial description shares: " + ", ".join(sorted(shared_symbols)).lower()
            )
            gaps.append(
                "Symbol agreement uses your description and source text; "
                "symbols were not recognized automatically."
            )
        matches = [visuals[i] for i in r.get("local_reference_ids", []) if i in visuals]
        best = max(matches, key=lambda v: v["similarity"], default=None)
        # A pure STERLING/purity query does not identify a maker, even if its photo
        # resembles a reference. A described symbol can provide the missing distinction.
        if best and (not generic_only or shared_symbols or exact or partial):
            value += 8 + 12 * max(0, best["similarity"])
            supports.append("Local hallmark-photo retrieval places this reference near your crop.")
            gaps.append(
                "Visual closeness is uncalibrated and may reflect lighting or stamp layout."
            )
            if informative and usable and not exact and not partial:
                value -= 12
                gaps.append("Reference lettering does not support the supplied reading.")
        if not supports or value <= 0:
            continue
        shared_construction = construction & words
        if shared_construction:
            value += min(5, len(shared_construction) * 2)
            supports.append(
                "Construction context agrees: " + ", ".join(sorted(shared_construction)).lower()
            )
        if r.get("shared_stamp_claims"):
            gaps.extend(x["claim"] for x in r["shared_stamp_claims"])
        if r.get("identity_kind") == "UNRESOLVED" and r["maker_name"].startswith("UNKNOWN"):
            gaps.append("The source leaves the maker unidentified.")
        key = digest(maker_key(r["maker_name"]).encode())
        strength = (
            "Strong lettering match"
            if exact
            and len(text.replace(" ", "")) >= 4
            and query.get("legibility") == "LEGIBLE"
            and r.get("transcription_status") != "PARTIAL"
            else "Possible lettering match"
            if exact or partial
            else "Pictorial lead"
            if shared_symbols
            else "Visual lead"
            if best
            else "Name-text lead"
        )
        g = groups.setdefault(
            key,
            dict(
                id=key,
                maker=r["maker_name"],
                strength=strength,
                order=value,
                references=[],
                similarities=[],
                discrepancies=[],
                visual=[],
                authenticated=False,
                owner_feedback=[],
                reference_order={},
                reference_evidence={},
                prior_adjustment=0,
            ),
        )
        if value > g["order"]:
            g.update(order=value, strength=strength)
        g["references"].append(r)
        g["reference_order"][r["record_id"]] = value
        g["reference_evidence"][r["record_id"]] = dict(similarities=supports, discrepancies=gaps)
        g["similarities"].extend(supports)
        g["discrepancies"].extend(gaps)
        if best:
            g["visual"].append(best["reference_id"])
    for f in feedback:
        g = groups.get(f["candidate_id"])
        if not g:
            continue  # Feedback alone cannot manufacture a published match.
        g["owner_feedback"].append(f)
        if f["same_case"]:
            g["order"] += {"CONFIRM": 8, "REJECT": -80, "RESEARCH": 0}.get(f["action"], 0)
        else:
            g["prior_adjustment"] += {"CONFIRM": 3, "REJECT": -3}.get(f["action"], 0)
        if f["action"] == "REJECT" and f["same_case"]:
            g["rejected"] = True
    for g in groups.values():
        g["order"] += max(-6, min(6, g.pop("prior_adjustment")))
    result = sorted(groups.values(), key=lambda g: (-g["order"], g["id"]))
    unresolved = [g for g in result if maker_key(g["maker"]).startswith("UNKNOWN ")]
    result = [g for g in result if g not in unresolved]
    rejected = [g for g in result if g.get("rejected")]
    result = [g for g in result if not g.get("rejected")][:5]
    for g in [*result, *rejected, *unresolved]:
        g["references"].sort(key=lambda r: -g["reference_order"][r["record_id"]])
        del g["reference_order"]
        for field in ["similarities", "discrepancies", "visual"]:
            g[field] = list(dict.fromkeys(g[field]))
        del g["order"]
    return dict(
        policy=POLICY,
        candidates=result,
        rejected=rejected,
        unresolved_references=unresolved,
        status="CANDIDATES" if result else "UNKNOWN",
        authenticated=False,
    )
