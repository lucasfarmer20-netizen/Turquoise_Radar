"""Curated exact phrases; bounded generation; never silently correct queries."""

from decimal import Decimal
from hashlib import sha256

from pydantic import BaseModel, Field
from sqlalchemy import select

from radar.db import SearchPattern

CURATED = [
    ("nature america", "Native American", "autocorrect", 100),
    ("turqoise", "turquoise", "misspelling", 90),
    ("turquiose", "turquoise", "misspelling", 88),
    ("turquise", "turquoise", "misspelling", 85),
    ("sterlng", "sterling", "misspelling", 85),
    ("stering", "sterling", "misspelling", 83),
    ("Navaho", "Navajo", "misspelling", 80),
    ("Navjo", "Navajo", "misspelling", 80),
    ("Zunni", "Zuni", "misspelling", 80),
    ("old blue stone ring", "turquoise ring", "generic", 95),
    ("silver ring blue rock", "turquoise ring", "generic", 90),
    ("green stone silver ring", "stone ring", "generic", 85),
    ("grandma's silver jewelry", "estate jewelry", "generic", 85),
    ("old Indian jewelry", "Southwestern style jewelry", "generic", 80),
    ("heavy silver bracelet", "silver cuff", "generic", 80),
    ("silver jewelry lot", "silver lot", "generic", 95),
    ("western silver ring", "Southwestern style ring", "generic", 90),
    ("925 blue stone", "silver stone jewelry", "generic", 85),
    ("stamped silver cuff", "silver cuff", "generic", 85),
    ("mystery silver jewelry", "estate jewelry", "generic", 80),
    ("metal bracelet turquoise", "turquoise bracelet", "material_terminology", 75),
    ("vintage blue stone cuff", "stone cuff", "material_terminology", 85),
    ("silver colored jewelry", "silver jewelry", "material_terminology", 65),
    ("old stamped jewelry", "hallmarked jewelry", "material_terminology", 75),
    ("costume jewelry estate lot", "estate silver lot", "category_mismatch", 85),
]


class PatternInput(BaseModel):
    phrase: str = Field(min_length=1, max_length=200)
    original_phrase: str = Field(min_length=1, max_length=200)
    canonical_concept: str = Field(min_length=1, max_length=200)
    anomaly_category: str = Field(min_length=1, max_length=80)
    sources: list[str] = Field(default_factory=list)
    active: bool = True
    priority_weight: int = Field(default=80, ge=0, le=100)


def add_pattern(session, data):
    existing = session.scalar(select(SearchPattern).where(SearchPattern.phrase == data.phrase))
    if existing:
        return existing
    pattern = SearchPattern(id=sha256(data.phrase.encode()).hexdigest()[:16], **data.model_dump())
    session.add(pattern)
    session.flush()
    return pattern


def seed_patterns(session):
    for phrase, canonical, category, priority in CURATED:
        add_pattern(
            session,
            PatternInput(
                phrase=phrase,
                original_phrase=canonical,
                canonical_concept=canonical,
                anomaly_category=category,
                priority_weight=priority,
            ),
        )


def learned_priority(p):
    return (
        p.priority_weight
        + min(15, p.useful_matches * 2 + p.successful_purchases * 3)
        + max(-5, min(5, int(Decimal(p.realized_profit) / 20)))
    )


def generate_queries(patterns, source=None, limit=40):
    result, seen = [], set()
    for p in sorted(patterns, key=lambda p: (-learned_priority(p), p.id)):
        if not p.active or (p.sources and source not in p.sources) or p.phrase in seen:
            continue
        seen.add(p.phrase)
        result.append(
            {
                "pattern_id": p.id,
                "original_phrase": p.original_phrase,
                "transformed_phrase": p.phrase,
                "query_sent": p.phrase,
            }
        )
        if len(result) >= max(1, min(limit, 100)):
            break
    return result


def match_pattern(item, patterns):
    if item.search_pattern_id:
        return next((p for p in patterns if p.id == item.search_pattern_id), None)
    if item.raw_search_phrase is not None:
        return next((p for p in patterns if p.phrase == item.raw_search_phrase), None)
    text = (item.raw_title + " " + item.raw_description).casefold()
    return max(
        (p for p in patterns if p.active and p.phrase.casefold() in text),
        key=learned_priority,
        default=None,
    )
