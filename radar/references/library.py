"""Private review, marks, derivatives, exclusion and local versioned exports."""

import hashlib
import io
import json
import os
import re
from pathlib import Path

from PIL import Image

from radar.references.ingest import assertion, eligible, identity
from radar.references.media import normalize, write_blob
from radar.references.schema import Assertion, Mark, Pair
from radar.references.store import audit, connect, dump, exclusive, get_source, now


def add_evidence(c, item_id, data):
    if not c.execute("SELECT 1 FROM items WHERE id=?", (item_id,)).fetchone():
        raise ValueError("Unknown item")
    value = Assertion.model_validate(data)
    with c:
        assertion(c, item_id, value)
        audit(c, "EVIDENCE_ADDED", item_id)


def add_mark(c, data):
    mark = Mark.model_validate(data)
    if not c.execute("SELECT 1 FROM items WHERE id=?", (mark.item_id,)).fetchone():
        raise ValueError("Unknown item")
    if mark.photo_id:
        asset = c.execute(
            "SELECT item_id FROM assets WHERE id=? AND removed=0", (mark.photo_id,)
        ).fetchone()
        crop = c.execute(
            "SELECT a.item_id FROM crops c JOIN assets a ON a.id=c.asset_id "
            "WHERE c.id=? AND c.removed=0 AND a.removed=0",
            (mark.photo_id,),
        ).fetchone()
        if not any(r and r["item_id"] == mark.item_id for r in (asset, crop)):
            raise ValueError("Mark photo/crop must belong to this item")
    if (
        mark.variant_of
        and not c.execute("SELECT 1 FROM marks WHERE id=?", (mark.variant_of,)).fetchone()
    ):
        raise ValueError("Unknown mark variant")
    with c:
        id_ = c.execute(
            "INSERT INTO marks(item_id,data,created_at) VALUES (?,?,?)",
            (mark.item_id, dump(mark.model_dump(mode="json")), now()),
        ).lastrowid
        audit(c, "MARK_ADDED", str(id_))
    return id_


def add_pair(c, data):
    pair = Pair.model_validate(data)
    if pair.left_item == pair.right_item:
        raise ValueError("Pairs must refer to different items")
    for id_ in (pair.left_item, pair.right_item):
        if not c.execute("SELECT 1 FROM items WHERE id=?", (id_,)).fetchone():
            raise ValueError("Pair item missing")
    with c:
        id_ = c.execute(
            "INSERT INTO pairs(left_item,right_item,data) VALUES (?,?,?)",
            (pair.left_item, pair.right_item, dump(pair.model_dump(mode="json"))),
        ).lastrowid
        audit(c, "PAIR_ADDED", str(id_))
    return id_


def search(c, query="", source=None, limit=50):
    # Parameterized plain-text matching: no raw SQL, FTS syntax or remote search.
    pattern = "%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    rows = c.execute(
        """SELECT DISTINCT i.* FROM items i
        LEFT JOIN marks m ON m.item_id=i.id LEFT JOIN assertions a ON a.item_id=i.id
        WHERE i.excluded=0 AND (? IS NULL OR i.source=?) AND
        (i.data LIKE ? ESCAPE '\\' OR m.data LIKE ? ESCAPE '\\' OR a.data LIKE ? ESCAPE '\\')
        ORDER BY i.id LIMIT ?""",
        (source, source, pattern, pattern, pattern, min(500, max(1, limit))),
    )
    return [
        {"id": r["id"], "source": r["source"], **json.loads(r["data"])}
        for r in rows
        if eligible(get_source(c, r["source"]))
    ]


def local_file(root, relative):
    path = (Path(root) / relative).resolve()
    if not path.is_relative_to(Path(root).resolve()):
        raise ValueError("Private file escaped reference directory")
    return path


def crop(root, asset_id, box):
    with exclusive(root), connect(root) as c:
        row = c.execute(
            "SELECT a.*,i.source,i.excluded FROM assets a JOIN items i ON i.id=a.item_id "
            "WHERE a.id=?",
            (asset_id,),
        ).fetchone()
        if (
            not row
            or row["removed"]
            or row["excluded"]
            or not eligible(get_source(c, row["source"]))
        ):
            raise ValueError("Asset excluded")
        data = json.loads(row["data"])
        raw = local_file(root, data["normalized_path"]).read_bytes()
        if hashlib.sha256(raw).hexdigest() != row["normalized_hash"]:
            raise ValueError("Asset integrity failed")
        with Image.open(io.BytesIO(raw)) as im:
            x0, y0, x1, y1 = box
            if not (0 <= x0 < x1 <= im.width and 0 <= y0 < y1 <= im.height):
                raise ValueError("Invalid crop bounds")
            buf = io.BytesIO()
            im.crop(box).save(buf, format="PNG")
        normalized, info = normalize(buf.getvalue())
        from radar.references.holdout import assert_reference_allowed

        assert_reference_allowed(root, info["original_hash"], info["normalized_hash"])
        path = write_blob(root, "crops", info["normalized_hash"], normalized)
        id_ = identity(asset_id, box, "jri-pixels-1")
        with c:
            c.execute(
                "INSERT OR IGNORE INTO crops(id,asset_id,data) VALUES (?,?,?)",
                (
                    id_,
                    asset_id,
                    dump(
                        {
                            "box": box,
                            "path": path,
                            "hash": info["normalized_hash"],
                            "rights_inherited_from": asset_id,
                            "processing_version": "jri-pixels-1",
                        }
                    ),
                ),
            )
            audit(c, "CROP_CREATED", id_)
        return id_


def exclude(c, *, source_id=None, item_id=None, asset_id=None):
    if sum(x is not None for x in (source_id, item_id, asset_id)) != 1:
        raise ValueError("Select exactly one exclusion target")
    with c:
        if source_id:
            source = get_source(c, source_id)
            source.enabled = False
            from radar.references.store import put_source

            put_source(c, source)
        elif item_id:
            if c.execute("UPDATE items SET excluded=1 WHERE id=?", (item_id,)).rowcount != 1:
                raise ValueError("Unknown item")
        else:
            if c.execute("UPDATE assets SET removed=1 WHERE id=?", (asset_id,)).rowcount != 1:
                raise ValueError("Unknown asset")
        audit(c, "EXCLUSION", source_id or item_id or asset_id)
        # Previously exported snapshots remain historical, explicitly invalidated for further use.
        for export in c.execute("SELECT id,data FROM exports").fetchall():
            data = json.loads(export["data"])
            data["invalidated_at"] = now()
            c.execute("UPDATE exports SET data=? WHERE id=?", (dump(data), export["id"]))


def purge(root):
    """Remove unreferenced/excluded media and all stale exports; retain provenance tombstones."""
    with exclusive(root), connect(root) as c:
        keep = set()
        for r in c.execute(
            "SELECT a.*,i.source,i.excluded FROM assets a JOIN items i ON a.item_id=i.id"
        ):
            if not r["removed"] and not r["excluded"] and eligible(get_source(c, r["source"])):
                d = json.loads(r["data"])
                keep.update([d["original_path"], d["normalized_path"]])
                keep.update(
                    json.loads(x["data"])["path"]
                    for x in c.execute(
                        "SELECT data FROM crops WHERE asset_id=? AND removed=0", (r["id"],)
                    )
                )
        removed = 0
        for folder in ("originals", "normalized", "crops", "embeddings", "exports"):
            directory = Path(root) / folder
            if directory.exists():
                for file in directory.rglob("*"):
                    if file.is_file() and str(file.relative_to(root)) not in keep:
                        file.unlink()
                        removed += 1
        with c:
            for r in c.execute("SELECT id,data FROM exports").fetchall():
                d = json.loads(r["data"])
                d["invalidated_at"] = now()
                c.execute("UPDATE exports SET data=? WHERE id=?", (dump(d), r["id"]))
            audit(c, "PRIVATE_MEDIA_PURGED", "library", files=removed)
        return removed


def import_manifest(
    root, source_id, path, max_items=100, max_images=300, *, transport=None, clock=None, sleep=None
):
    """The same durable queue, limits and safe diagnostics as API collection."""
    from radar.references.ingest import collect

    return collect(
        root,
        source_id,
        "manifest",
        manifest_path=path,
        max_items=max_items,
        max_images=max_images,
        transport=transport,
        clock=clock,
        sleep=sleep,
    )


def export_dataset(root, version, *, include_fair_use=False, split_mode="artist"):
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", version) or split_mode not in {"artist", "item"}:
        raise ValueError("Invalid export version/split mode")
    with exclusive(root), connect(root) as c:
        if c.execute("SELECT 1 FROM exports WHERE id=?", (version,)).fetchone():
            raise ValueError("Dataset versions are immutable; choose a new version")
        items = {}
        for row in c.execute("SELECT * FROM items WHERE excluded=0 ORDER BY id"):
            source = get_source(c, row["source"])
            if not eligible(source) or (
                source.classification == "INTERNAL_FAIR_USE" and not include_fair_use
            ):
                continue
            photos = []
            for asset in c.execute(
                "SELECT * FROM assets WHERE item_id=? AND removed=0 ORDER BY id", (row["id"],)
            ):
                d = json.loads(asset["data"])
                # A later source review cannot retroactively grant rights to an
                # image collected under a different basis. Preserve that snapshot.
                if (
                    d["source_review"]["classification"] == "INTERNAL_FAIR_USE"
                    and not include_fair_use
                ):
                    continue
                path = local_file(root, d["normalized_path"])
                if not path.exists():
                    continue
                if hashlib.sha256(path.read_bytes()).hexdigest() != asset["normalized_hash"]:
                    raise ValueError("Export image integrity failed")
                from radar.references.holdout import assert_reference_allowed

                assert_reference_allowed(root, asset["original_hash"], asset["normalized_hash"])
                photos.append(
                    {
                        "id": asset["id"],
                        "path": str(path),
                        "hash": asset["normalized_hash"],
                        "rights": d["rights"],
                        "source_review": d["source_review"],
                    }
                )
                for cr in c.execute(
                    "SELECT * FROM crops WHERE asset_id=? AND removed=0", (asset["id"],)
                ):
                    cd = json.loads(cr["data"])
                    cp = local_file(root, cd["path"])
                    if not cp.exists() or hashlib.sha256(cp.read_bytes()).hexdigest() != cd["hash"]:
                        raise ValueError("Export crop integrity failed")
                    assert_reference_allowed(root, cd["hash"])
                    photos.append(
                        {
                            "id": cr["id"],
                            "parent": asset["id"],
                            "path": str(cp),
                            "hash": cd["hash"],
                            "rights": d["rights"],
                        }
                    )
            if not photos:
                continue
            assertions = [
                dict(id=a["id"], **json.loads(a["data"]))
                for a in c.execute(
                    "SELECT * FROM assertions WHERE item_id=? ORDER BY id", (row["id"],)
                )
            ]
            superseded = {a["supersedes"] for a in assertions if a.get("supersedes")}
            labels = [
                a for a in assertions if a["id"] not in superseded and a["status"] != "SUPERSEDED"
            ]
            items[row["id"]] = {
                "schema": "jri-vlm-1",
                "item_id": row["id"],
                "source": row["source"],
                "source_review": source.model_dump(mode="json"),
                "item": json.loads(row["data"]),
                "images": photos,
                "assertion_history": assertions,
                "evidence_qualified_labels": [
                    a for a in labels if a["origin"] != "MODEL_PREDICTION"
                ],
                "model_predictions": [a for a in labels if a["origin"] == "MODEL_PREDICTION"],
                "visual_training_targets": [
                    a
                    for a in labels
                    if a["status"] == "DOCUMENTED"
                    and a.get("observable_from_photo")
                    and a["origin"] != "MODEL_PREDICTION"
                ],
                "marks": [
                    json.loads(m["data"])
                    for m in c.execute("SELECT data FROM marks WHERE item_id=?", (row["id"],))
                ],
            }
        # Connected components keep every view/crop, exact/near duplicate and artist group together.
        parent = {id_: id_ for id_ in items}

        def find(id_):
            while parent[id_] != id_:
                parent[id_] = parent[parent[id_]]
                id_ = parent[id_]
            return id_

        def union(a, b):
            if a in parent and b in parent:
                x, y = find(a), find(b)
                parent[max(x, y)] = min(x, y)

        owners = {}
        for id_, item in items.items():
            keys = ["hash:" + p["hash"] for p in item["images"]]
            if split_mode == "artist":
                makers = item["item"]["artists"] + [
                    m for mark in item["marks"] for m in mark["candidate_makers"]
                ]
                makers += [
                    a["value"]
                    for a in item["evidence_qualified_labels"]
                    if a["field"] == "maker_group"
                ]
                keys += [
                    "artist:" + re.sub(r"\s+", " ", m.strip().casefold())
                    for m in makers
                    if m.strip()
                ]
            keys += [
                "group:" + a["value"]
                for a in item["evidence_qualified_labels"]
                if a["field"] == "split_group"
            ]
            for key in keys:
                if key in owners:
                    union(id_, owners[key])
                owners[key] = id_
        for d in c.execute(
            "SELECT a.item_id AS a,b.item_id AS b FROM duplicates d "
            "JOIN assets a ON a.id=d.left_id JOIN assets b ON b.id=d.right_id"
        ):
            union(d["a"], d["b"])
        counts = {"train": 0, "validation": 0, "test": 0}
        folder = Path(root) / "exports" / version
        folder.mkdir(parents=True, mode=0o700)
        path = folder / "examples.jsonl"
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        try:
            with os.fdopen(fd, "w") as f:
                for id_, item in items.items():
                    group = find(id_)
                    bucket = int(hashlib.sha256(group.encode()).hexdigest()[:8], 16) % 100
                    split = "train" if bucket < 80 else "validation" if bucket < 90 else "test"
                    item.update(split=split, split_group=group)
                    counts[split] += 1
                    f.write(dump(item) + "\n")
            pairs = []
            for pair in c.execute("SELECT data FROM pairs ORDER BY id"):
                value = json.loads(pair["data"])
                left, right = value["left_item"], value["right_item"]
                if (
                    left in items
                    and right in items
                    and items[left]["split"] == items[right]["split"]
                ):
                    pairs.append({**value, "split": items[left]["split"]})
            pairs_path = folder / "pairs.jsonl"
            pairs_path.write_text("".join(dump(value) + "\n" for value in pairs))
            pairs_path.chmod(0o600)
            manifest = {
                "schema": "jri-vlm-1",
                "version": version,
                "created_at": now(),
                "count": len(items),
                "pairs_count": len(pairs),
                "splits": counts,
                "split_mode": split_mode,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "include_internal_fair_use": include_fair_use,
                "uploaded": False,
                "training_ready": False,
                "warning": "Review labels, artist aliases, negatives and source leakage; "
                "a local manifest is not permission to train or upload. "
                "Do not export hidden material tests as image-only target answers.",
            }
            (folder / "manifest.json").write_text(dump(manifest) + "\n")
            (folder / "manifest.json").chmod(0o600)
            with c:
                c.execute("INSERT INTO exports VALUES (?,?,?)", (version, now(), dump(manifest)))
                audit(c, "LOCAL_EXPORT", version, items=len(items))
            return manifest
        except BaseException:
            path.unlink(missing_ok=True)
            raise
