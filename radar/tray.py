"""Describe-vs-depict gap for mixed estate tray lots.

Auction houses audit the claims they make, not the ones they omit: a lot photo
showing many pieces is routinely catalogued by naming two or three of them. The
unnamed remainder draws no bidder attention, so it is where an underpriced lot
hides.

This module measures that gap. Both sides of it are deterministic. The depicted
side uses only the distinguishable sub-items the vision model was permitted to
isolate -- never a total piece count, which the vision prompt forbids, and never
an estimate of what is hidden or overlapping. The described side is a literal
scan of the listing title and description for item nouns.

Nothing here estimates weight, purity, melt or resale value, and an apparent
metal tone is a colour observation only -- never a material, karat or purity
claim. The output is a research priority for the owner to review, not a
valuation and not a bidding instruction.
"""

import re

from radar.jewelry.schemas import VisionResult

# Canonical item nouns that appear in jewelry-lot catalogue text. Matching is
# literal and plural-tolerant; no stemming, so an unexpected word simply fails
# to match and the sub-item is treated as unnamed, which is the cautious
# direction for a review queue.
ITEM_TERMS = {
    "ring": r"rings?",
    "bracelet": r"bracelets?",
    "cuff": r"cuffs?",
    "bangle": r"bangles?",
    "pendant": r"pendants?",
    "necklace": r"necklaces?",
    "chain": r"chains?",
    "earring": r"ear[\s-]?rings?",
    "brooch": r"brooch(?:es)?",
    "pin": r"pins?",
    "bolo": r"bolos?(?:\s+ties?)?",
    "concho": r"conchos?|conchas?",
    "buckle": r"buckles?",
    "watch": r"watch(?:es)?",
    "coin": r"coins?|dimes?|quarters?|nickels?|half[\s-]?dollars?",
    "charm": r"charms?",
    "locket": r"lockets?",
    "tie_clip": r"tie[\s-]?(?:clips?|bars?|tacks?)",
    "ingot": r"ingots?|bars?|blanks?",
    "bead": r"beads?|heishi",
    "strand": r"strands?",
    "squash_blossom": r"squash[\s-]?blossoms?",
    "naja": r"najas?",
    "fetish": r"fetish(?:es)?",
    "button": r"buttons?",
    "stud": r"studs?",
}

# A listing that says any of these has named gold, including "gold filled",
# "gold tone" and "gold plated". Naming it at all removes the surprise, so the
# undescribed-gold flag correctly does not fire.
GOLD_TERMS = r"gold|gilt|gilded|vermeil|\d{1,2}\s?k\b|\d{1,2}\s?kt\b|karat|carat"

MAX_UNNAMED_POINTS = 48
UNNAMED_POINTS = 12
GOLD_TONE_POINTS = 30
BASE_POINTS = 10


def _normalise(text):
    return re.sub(r"\s+", " ", (text or "")).strip().lower()


def described_terms(text):
    """Return the canonical item nouns the listing text literally names."""
    haystack = _normalise(text)
    if not haystack:
        return frozenset()
    return frozenset(
        term
        for term, pattern in ITEM_TERMS.items()
        if re.search(rf"\b(?:{pattern})\b", haystack, re.I)
    )


def label_terms(label):
    """Return the canonical item nouns a sub-item label mentions."""
    return described_terms(label)


def names_gold(text):
    """True when the listing text itself mentions gold in any form."""
    haystack = _normalise(text)
    return bool(haystack and re.search(rf"\b(?:{GOLD_TERMS})\b", haystack, re.I))


def listing_text(context):
    """Join the permitted listing fields.

    An email-derived description is already blanked upstream to keep private
    receipt evidence out of provider context; this falls back to the title
    rather than reaching for the original message.
    """
    context = context or {}
    return " ".join(
        part for part in (context.get("title"), context.get("seller_description")) if part
    )


def tray_signal(context, result: VisionResult):
    """Score how much of a depicted lot the listing never names.

    Returns a research-priority record. A sub-item counts as unnamed when its
    label mentions no item noun the listing also mentions, including the case
    where the label mentions no recognised noun at all -- the listing plainly
    did not name it either.
    """
    text = listing_text(context)
    named = described_terms(text)
    listing_names_gold = names_gold(text)

    unnamed = []
    gold_tone_unnamed = []
    for item in result.sub_items:
        terms = label_terms(item.label)
        if terms & named:
            continue
        unnamed.append(item.key)
        if item.tone == "GOLD_TONE" and not listing_names_gold:
            gold_tone_unnamed.append(item.key)

    breakdown = []
    if unnamed:
        points = min(len(unnamed) * UNNAMED_POINTS, MAX_UNNAMED_POINTS)
        breakdown.append(
            dict(signal="UNNAMED_SUB_ITEM", points=points, sub_item_keys=list(unnamed))
        )
    if gold_tone_unnamed:
        breakdown.append(
            dict(
                signal="UNDESCRIBED_GOLD_TONE",
                points=GOLD_TONE_POINTS,
                sub_item_keys=list(gold_tone_unnamed),
            )
        )

    score = (
        min(100, BASE_POINTS + sum(row["points"] for row in breakdown)) if breakdown else 0
    )

    categories = []
    if unnamed:
        categories.append("TRAY LOT REVIEW")
    if gold_tone_unnamed:
        categories.append("UNDESCRIBED GOLD TONE")
    if result.classification == "MIXED_LOT" and not unnamed:
        categories.append("LOT FULLY ENUMERATED")
    if not categories:
        categories.append("NO TRAY SIGNAL")

    return dict(
        score=score,
        categories=categories,
        breakdown=breakdown,
        described_terms=sorted(named),
        distinguishable_sub_items=len(result.sub_items),
        unnamed_sub_items=len(unnamed),
        unnamed_sub_item_keys=list(unnamed),
        undescribed_gold_tone_keys=list(gold_tone_unnamed),
        listing_names_gold=listing_names_gold,
        meaning=(
            "Research priority only. Counts cover distinguishable sub-items the model"
            " could isolate, never total or hidden pieces, and never weight, purity"
            " or value. Apparent tone is colour, not material."
        ),
    )
