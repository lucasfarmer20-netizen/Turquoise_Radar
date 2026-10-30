from radar.jewelry.schemas import VisionResult

WEIGHTS = {
    "POSSIBLE_HALLMARK": 24,
    "CONSTRUCTION": 16,
    "CRAFTSMANSHIP": 14,
    "MIXED_LOT_DETAIL": 16,
    "OVERLOOKED_DETAIL": 12,
    "PLATING_CONCERN": -18,
    "DAMAGE": -15,
    "POOR_IMAGE": -20,
    "CONFLICT": -15,
}


def visual_score(result: VisionResult):
    # Each signal contributes at most once. Repeating claims cannot inflate priority.
    breakdown = []
    signals = set()
    for signal, weight in WEIGHTS.items():
        evidence = [f for f in result.findings if f.signal == signal and f.confidence != "UNKNOWN"]
        if weight > 0:
            evidence = [
                f
                for f in evidence
                if f.confidence in ("HIGH", "MEDIUM") and f.evidence_kind == "DIRECT"
            ]
        if evidence:
            signals.add(signal)
            breakdown.append(
                dict(signal=signal, points=weight, finding_keys=[f.key for f in evidence])
            )
    positives = any(row["points"] > 0 for row in breakdown)
    score = max(0, min(100, 10 + sum(row["points"] for row in breakdown))) if positives else 0
    categories = []
    if not positives or "POOR_IMAGE" in signals:
        categories.append(
            "INCONCLUSIVE"
            if not result.findings or "POOR_IMAGE" in signals
            else "LOW VISUAL INTEREST"
        )
    if score >= 60:
        categories.append("HIDDEN VALUE CANDIDATE")
    if "POSSIBLE_HALLMARK" in signals:
        categories.append("STERLING HALLMARK REVIEW")
    if "CRAFTSMANSHIP" in signals or "CONSTRUCTION" in signals:
        categories.append("COLLECTOR DETAIL REVIEW")
    if result.sub_items:
        categories.append("MIXED LOT INVESTIGATION")
    if result.classification == "RING" and positives:
        categories.append("RING RESTOCK REVIEW")
    if not categories:
        categories.append("LOW VISUAL INTEREST")
    return dict(
        score=score,
        categories=categories,
        breakdown=breakdown,
        base=10 if positives else 0,
        meaning="Visual research priority only; no appraisal, authenticity or financial rating",
    )
