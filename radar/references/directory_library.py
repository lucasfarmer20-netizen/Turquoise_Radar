"""Reuse existing eligible local mark evidence without copying images or rebuilding its index."""

from collections import defaultdict
from datetime import UTC, datetime
from urllib.parse import urlsplit

from radar.references.directory import connect, digest, put_review, upsert
from radar.references.persistence import verify
from radar.references.similarity import references


def import_library(root, library, *, include_fair_use=False):
    verify(library)
    groups = defaultdict(list)
    for r in references(library, include_fair_use=include_fair_use):
        groups[r["source"]].append(r)
    count = 0
    for source, refs in groups.items():
        # Repeated source-composited crops of the same object are one record.
        items = defaultdict(list)
        for r in refs:
            items[r["item_id"]].append(r)
        source_id = "library-" + source
        review = {
            "source": source_id,
            "hosts": sorted({urlsplit(r["source_url"]).hostname for r in refs}),
            "paths": ["/"],
            "terms_urls": sorted({r["source_url"] for r in refs}),
            "evidence": "Existing currently eligible JRI source/asset review snapshots and media "
            "hashes verified by references(); no new source/image acquisition.",
            "rationale": "Reuse source-attributed factual mark evidence already present in the "
            "private library for local matching. Preserve original source identity and rights. "
            "New companion image links remain external-only; no training or provider upload.",
            "facts_allowed": True,
            "image_policy": "EXTERNAL_LINK_ONLY",
            "reviewed_at": datetime.now(UTC).isoformat(),
            "independence_group": source + "-directory",
            "record_limit": len(items),
        }
        rid = put_review(root, review)
        with connect(root, write=True) as c:
            for item, views in items.items():
                r = views[0]
                names = r["candidate_makers"]
                name = "; ".join(names)
                kind = (
                    "TRADING_POST"
                    if any("Trading" in n or "Trading Post" in n for n in names)
                    else "UNRESOLVED"
                )
                record = {
                    "locator": "legacy-item:" + item,
                    "maker_name": name,
                    "aliases": [],
                    "identity_kind": kind,
                    "cultural_context": None,
                    "source_caption": r["title"],
                    "transcription": r["transcription"],
                    "transcription_status": (
                        "NOT_REVIEWED"
                        if not r["transcription"]
                        else "LEGIBLE"
                        if r["legibility"] == "LEGIBLE"
                        else "PARTIAL"
                    ),
                    "description": r["mark_description"] or "",
                    "image_urls": [],
                    "source_url": r["source_url"],
                    "uncertainty": "SOURCE_ATTRIBUTED",
                    "materials": [],
                    "period": None,
                    "contradictions": [],
                    "local_reference_ids": sorted(v["reference_id"] for v in views),
                    "original_source": source,
                }
                upsert(c, record, rid, source_id)
                count += 1
    return {
        "reused_existing_records": count,
        "new_images": 0,
        "historical_index_changed": False,
        "source_family_digest": digest(str(sorted(groups)).encode()),
    }
