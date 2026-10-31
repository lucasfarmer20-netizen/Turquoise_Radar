"""Private JRI administration. Live collection requires the explicit --live flag."""

import argparse
import json
import os
from pathlib import Path

from radar.references.ingest import collect
from radar.references.library import (
    add_evidence,
    add_mark,
    add_pair,
    crop,
    exclude,
    export_dataset,
    import_manifest,
    purge,
    search,
)
from radar.references.network import StopCollection
from radar.references.schema import Source
from radar.references.sources import seed
from radar.references.store import connect, dump, exclusive, get_source
from radar.references.targeted import review_item, review_report, triage


def main():
    os.umask(0o077)
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root", type=Path, default=Path("data/jri"))
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("init", "sources", "report", "purge"):
        sub.add_parser(name)
    targeted = sub.add_parser("triage")
    targeted.add_argument("--source")
    targeted.add_argument("--limit", type=int, default=50)
    item_review = sub.add_parser("review-item")
    item_review.add_argument("file", type=Path)
    sub.add_parser("review-report")
    review = sub.add_parser("review-source")
    review.add_argument("file", type=Path)
    batch = sub.add_parser("collect")
    batch.add_argument("source")
    batch.add_argument("--query", default="jewelry")
    batch.add_argument("--live", action="store_true")
    for name, default in [
        ("max-items", 300),
        ("max-images", 400),
        ("max-pages", 30),
        ("page-size", 25),
        ("max-requests", 2000),
        ("max-bytes", 1024 * 1024 * 1024),
    ]:
        batch.add_argument("--" + name, type=int, default=default)
    imp = sub.add_parser("import-manifest")
    imp.add_argument("source")
    imp.add_argument("file", type=Path)
    imp.add_argument("--live", action="store_true")
    imp.add_argument("--max-items", type=int, default=100)
    imp.add_argument("--max-images", type=int, default=300)
    find = sub.add_parser("search")
    find.add_argument("query")
    find.add_argument("--source")
    for name in ("add-evidence", "add-mark", "add-pair"):
        cmd = sub.add_parser(name)
        cmd.add_argument("file", type=Path)
    remove = sub.add_parser("exclude")
    group = remove.add_mutually_exclusive_group(required=True)
    group.add_argument("--source")
    group.add_argument("--item")
    group.add_argument("--asset")
    cr = sub.add_parser("crop")
    cr.add_argument("asset")
    cr.add_argument("box", type=int, nargs=4)
    ex = sub.add_parser("export")
    ex.add_argument("version")
    ex.add_argument("--include-internal-fair-use", action="store_true")
    ex.add_argument("--split-mode", choices=["artist", "item"], default="artist")
    args = p.parse_args()
    if args.cmd in {"collect", "import-manifest"} and not args.live:
        p.error("Network collection requires --live; no implicit activation")
    root = args.root.resolve()
    if args.cmd == "collect":
        print(
            dump(
                collect(
                    root,
                    args.source,
                    args.query,
                    **{
                        k: getattr(args, k)
                        for k in [
                            "max_items",
                            "max_images",
                            "max_pages",
                            "page_size",
                            "max_requests",
                            "max_bytes",
                        ]
                    },
                )
            )
        )
        return
    if args.cmd == "import-manifest":
        print(dump(import_manifest(root, args.source, args.file, args.max_items, args.max_images)))
        return
    if args.cmd == "export":
        print(
            dump(
                export_dataset(
                    root,
                    args.version,
                    include_fair_use=args.include_internal_fair_use,
                    split_mode=args.split_mode,
                )
            )
        )
        return
    if args.cmd == "crop":
        print(dump({"crop_id": crop(root, args.asset, args.box)}))
        return
    if args.cmd == "purge":
        print(dump({"files_removed": purge(root)}))
        return
    with exclusive(root), connect(root) as c:
        if args.cmd == "init":
            seed(c)
            print(dump({"initialized": True, "live_calls": 0}))
        elif args.cmd == "review-source":
            from radar.references.store import put_source

            with c:
                put_source(c, Source.model_validate_json(args.file.read_text()))
            print(dump({"review_saved": True}))
        elif args.cmd == "sources":
            print(dump([json.loads(r["data"]) for r in c.execute("SELECT data FROM sources")]))
        elif args.cmd == "triage":
            print(dump(triage(c, args.source, args.limit)))
        elif args.cmd == "review-item":
            print(dump(review_item(c, json.loads(args.file.read_text()))))
        elif args.cmd == "review-report":
            print(dump(review_report(c)))
        elif args.cmd == "search":
            print(dump(search(c, args.query, args.source)))
        elif args.cmd == "exclude":
            exclude(c, source_id=args.source, item_id=args.item, asset_id=args.asset)
            print(dump({"excluded": True}))
        elif args.cmd == "add-evidence":
            data = json.loads(args.file.read_text())
            add_evidence(c, data.pop("item_id"), data)
            print(dump({"evidence_saved": True}))
        elif args.cmd == "add-pair":
            print(dump({"pair_id": add_pair(c, json.loads(args.file.read_text()))}))
        elif args.cmd == "add-mark":
            print(dump({"mark_id": add_mark(c, json.loads(args.file.read_text()))}))
        elif args.cmd == "report":
            sources = []
            for row in c.execute("SELECT id FROM sources"):
                id_ = row["id"]
                sources.append(
                    {
                        "source": id_,
                        "classification": get_source(c, id_).classification,
                        "enabled": get_source(c, id_).enabled,
                        "items": c.execute(
                            "SELECT count(*) FROM items WHERE source=?", (id_,)
                        ).fetchone()[0],
                        "images": c.execute(
                            "SELECT count(*) FROM assets a JOIN items i ON i.id=a.item_id "
                            "WHERE i.source=? AND a.removed=0 AND i.excluded=0",
                            (id_,),
                        ).fetchone()[0],
                    }
                )
            print(
                dump(
                    {
                        "sources": sources,
                        "batches": [
                            dict(r) | {"data": json.loads(r["data"])}
                            for r in c.execute("SELECT * FROM batches ORDER BY id")
                        ],
                        "unique_originals": c.execute(
                            "SELECT count(DISTINCT original_hash) FROM assets WHERE removed=0"
                        ).fetchone()[0],
                        "duplicate_pairs": c.execute("SELECT count(*) FROM duplicates").fetchone()[
                            0
                        ],
                        "unreviewed_attribution_is_not_ground_truth": True,
                    }
                )
            )


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError):
        print(dump({"status": "STOPPED", "category": "LOCAL_VALIDATION_OR_STORAGE"}))
        raise SystemExit(1) from None
    except StopCollection as exc:
        print(dump({"status": "PAUSED", "category": exc.category, "http_status": exc.status}))
        raise SystemExit(1) from None
