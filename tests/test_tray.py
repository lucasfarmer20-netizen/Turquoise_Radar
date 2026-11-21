"""Describe-vs-depict gap scoring for mixed estate tray lots."""

import pytest
from pydantic import ValidationError

from radar.jewelry.schemas import Finding, SubItem, VisionResult
from radar.tray import described_terms, names_gold, tray_signal

IMAGE = "a" * 32


def finding(key, signal="MIXED_LOT_DETAIL"):
    return Finding(
        key=key,
        category="RESEARCH",
        description="Distinguishable piece research lead.",
        image_id=IMAGE,
        visible_evidence="Separate object visible in the tray photo.",
        confidence="MEDIUM",
        evidence_kind="DIRECT",
        uncertainty="Overlapping pieces remain uncounted.",
        signal=signal,
    )


def sub_item(key, label, tone="UNKNOWN"):
    return SubItem(
        key=key,
        label=label,
        image_id=IMAGE,
        location_hint="Tray region",
        finding_keys=[key],
        uncertainty="Research lead, not an individual listing.",
        tone=tone,
    )


def result(sub_items, classification="MIXED_LOT"):
    return VisionResult(
        classification=classification,
        summary="Tray lot review.",
        visible_southwestern_style="Southwestern styling present; attribution unverified.",
        hallmark_candidate="UNKNOWN: back views needed",
        findings=[finding(item.key) for item in sub_items],
        sub_items=sub_items,
    )


def test_described_terms_reads_plurals_and_compounds():
    terms = described_terms("Sterling jewelry grouping - cuff, 4 bracelets and a bolo tie")
    assert {"cuff", "bracelet", "bolo"} <= terms
    assert "ring" not in terms


def test_names_gold_covers_karat_and_qualified_forms():
    assert names_gold("14k gold filled chain")
    assert names_gold("Gilt brooch")
    assert names_gold("18 kt band")
    assert not names_gold("Sterling jewelry grouping - Native American cuff")


def test_fully_enumerated_lot_produces_no_gap():
    signal = tray_signal(
        {"title": "Lot of 2 sterling rings and a bracelet", "seller_description": ""},
        result([sub_item("a", "silver ring"), sub_item("b", "wide bracelet")]),
    )
    assert signal["score"] == 0
    assert signal["unnamed_sub_items"] == 0
    assert signal["categories"] == ["LOT FULLY ENUMERATED"]


def test_unnamed_sub_items_raise_priority():
    signal = tray_signal(
        {"title": "Sterling jewelry grouping - cuff", "seller_description": ""},
        result(
            [
                sub_item("a", "silver cuff"),
                sub_item("b", "panel link bracelet"),
                sub_item("c", "oval pendant"),
            ]
        ),
    )
    assert signal["unnamed_sub_items"] == 2
    assert signal["unnamed_sub_item_keys"] == ["b", "c"]
    assert "TRAY LOT REVIEW" in signal["categories"]
    assert signal["score"] == 10 + 24


def test_undescribed_gold_tone_is_the_strongest_signal():
    listed = tray_signal(
        {"title": "Sterling jewelry grouping - cuff", "seller_description": ""},
        result([sub_item("a", "oval blank", tone="GOLD_TONE")]),
    )
    assert listed["undescribed_gold_tone_keys"] == ["a"]
    assert "UNDESCRIBED GOLD TONE" in listed["categories"]
    assert listed["score"] == 10 + 12 + 30
    assert listed["listing_names_gold"] is False


def test_listing_that_mentions_gold_suppresses_the_tone_flag():
    signal = tray_signal(
        {"title": "Estate grouping with gold filled oval blanks", "seller_description": ""},
        result([sub_item("a", "oval blank", tone="GOLD_TONE")]),
    )
    assert signal["undescribed_gold_tone_keys"] == []
    assert signal["listing_names_gold"] is True
    assert "UNDESCRIBED GOLD TONE" not in signal["categories"]


def test_unrecognised_label_counts_as_unnamed():
    signal = tray_signal(
        {"title": "Sterling jewelry grouping", "seller_description": ""},
        result([sub_item("a", "foreground ornament")]),
    )
    assert signal["unnamed_sub_items"] == 1


def test_blanked_email_description_falls_back_to_title():
    # Alert-derived descriptions are blanked upstream to keep receipt evidence
    # out of provider context; the gap must still read the permitted title.
    signal = tray_signal(
        {"title": "Lot of sterling rings", "seller_description": ""},
        result([sub_item("a", "silver ring")]),
    )
    assert signal["described_terms"] == ["ring"]
    assert signal["unnamed_sub_items"] == 0


def test_missing_context_is_handled_without_inventing_text():
    signal = tray_signal(None, result([sub_item("a", "silver ring")]))
    assert signal["described_terms"] == []
    assert signal["unnamed_sub_items"] == 1


def test_score_stays_bounded_and_unnamed_points_are_capped():
    items = [sub_item(f"k{i}", "foreground ornament", tone="GOLD_TONE") for i in range(8)]
    signal = tray_signal({"title": "Estate tray", "seller_description": ""}, result(items))
    assert signal["score"] == 10 + 48 + 30
    assert signal["score"] <= 100


def test_no_sub_items_produces_no_tray_signal():
    signal = tray_signal(
        {"title": "Single sterling ring", "seller_description": ""},
        result([], classification="RING"),
    )
    assert signal["score"] == 0
    assert signal["categories"] == ["NO TRAY SIGNAL"]


def test_tone_defaults_to_unknown_so_cached_findings_stay_valid():
    item = SubItem(
        key="a",
        label="silver ring",
        image_id=IMAGE,
        location_hint="Tray region",
        finding_keys=["a"],
        uncertainty="Research lead only.",
    )
    assert item.tone == "UNKNOWN"


def test_tone_rejects_values_outside_the_contract():
    with pytest.raises(ValidationError):
        sub_item("a", "silver ring", tone="14K")


def test_tone_does_not_loosen_the_unsupported_claim_guard():
    with pytest.raises(ValidationError):
        sub_item("a", "genuine sterling ring", tone="SILVER_TONE")
