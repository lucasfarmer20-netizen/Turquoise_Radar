"""Private owner-photo intake, blind frozen rankings and post-hoc evaluation. No uploads."""

import argparse
import base64
import html
import io
import json
import os
import shutil
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from PIL import Image
from pydantic import BaseModel, ConfigDict, Field, model_validator

from radar.references.holdout import read_holdouts, register
from radar.references.library import local_file
from radar.references.media import normalize
from radar.references.persistence import read_store
from radar.references.recognition import POLICY, families, rank, summarize
from radar.references.similarity import (
    Encoder,
    hash_bytes,
    query_file,
    references,
    unit,
    write_json,
)
from radar.references.store import dump, exclusive, private_root


class PhotoCase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    case_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    origin: Literal["OWNER_DECLARED_INDEPENDENT", "SOURCE_FAMILY_CONTROL", "REPEATED_INSET_CONTROL"]
    capture_group: str = Field(min_length=1, max_length=100)
    partition: Literal["EVALUATION", "CALIBRATION", "CONTROL"]
    legibility: Literal["LEGIBLE", "PARTIAL", "UNREADABLE"]
    transcription: str = Field(default="", max_length=1000)
    transcript_basis: Literal["HUMAN_VISIBLE_TEXT", "EXISTING_REFERENCE_ANNOTATION"]
    provenance: str = Field(min_length=1, max_length=2000)
    full_sha256: str
    mark_sha256: str
    source_sha256: str
    box: tuple[int, int, int, int] | None = None
    quality_flags: list[str] = Field(default_factory=list)
    excluded_items: list[str] = Field(default_factory=list)
    excluded_assets: list[str] = Field(default_factory=list)
    supersedes: str | None = None
    created_at: str

    @model_validator(mode="after")
    def independent_boundary(self):
        if self.origin == "OWNER_DECLARED_INDEPENDENT":
            if self.partition == "CONTROL" or self.transcript_basis != "HUMAN_VISIBLE_TEXT":
                raise ValueError("Owner photo cannot masquerade as an annotated control")
            if self.excluded_items or self.excluded_assets:
                raise ValueError("Owner query cannot select its reference exclusions")
        if self.legibility != "LEGIBLE" and self.transcription:
            raise ValueError("Use only reliably legible text; leave uncertain transcripts blank")
        return self


def evaluation_root(reference_root, target):
    reference_root, target = Path(reference_root).resolve(), Path(target).resolve()
    if target.is_relative_to(reference_root) or reference_root.is_relative_to(target):
        raise ValueError("Evaluation photographs must be separate from reference storage")
    read_store(reference_root).close()
    return private_root(target)


def load_case(folder):
    if folder.is_symlink() or not folder.is_dir():
        raise ValueError("Unsafe evaluation case directory")
    case = PhotoCase.model_validate_json(local_file(folder, "case.json").read_text())
    if folder.name != case.case_id:
        raise ValueError("Case identity mismatch")
    for name, digest in [("full.png", case.full_sha256), ("mark.png", case.mark_sha256)]:
        path = local_file(folder, name)
        if hash_bytes(query_file(path)) != digest:
            raise ValueError("Evaluation photograph integrity failed")
    return case


def cases_in(root):
    cases = []
    for folder in sorted((Path(root) / "cases").glob("*")):
        cases.append((folder, load_case(folder)))
    superseded = {c.supersedes for _, c in cases if c.supersedes}
    return [(f, c) for f, c in cases if c.case_id not in superseded]


def isolated(full, box):
    if box is None:
        return full, ["NO_MARK_BOX"]
    with Image.open(io.BytesIO(full)) as im:
        x0, y0, x1, y1 = box
        if not 0 <= x0 < x1 <= im.width or not 0 <= y0 < y1 <= im.height:
            raise ValueError("Mark box must use the normalized preview's pixel coordinates")
        mark = im.crop(box).convert("RGB")
        flags = ["LOW_RES_MARK"] if min(mark.size) < 64 else []
        # Padding supports diagnosis of small marks, never invents additional detail.
        canvas = Image.new("RGB", (max(64, mark.width), max(64, mark.height)), "white")
        canvas.paste(mark, (0, 0))
        buf = io.BytesIO()
        canvas.save(buf, "PNG")
    return buf.getvalue(), flags


def admit(
    reference_root,
    target,
    photo,
    capture_group,
    provenance,
    legibility="UNREADABLE",
    transcription="",
    box=None,
    partition="EVALUATION",
    supersedes=None,
):
    reference_root = Path(reference_root).resolve()
    if Path(photo).resolve().is_relative_to(reference_root):
        raise ValueError("Reference/gallery image is not an independent owner photograph")
    raw = query_file(photo)
    full, _ = normalize(raw)
    mark, flags = isolated(full, box)
    case_id = uuid.uuid4().hex
    case = PhotoCase(
        case_id=case_id,
        origin="OWNER_DECLARED_INDEPENDENT",
        capture_group=capture_group,
        partition=partition,
        legibility=legibility,
        transcription=transcription,
        transcript_basis="HUMAN_VISIBLE_TEXT",
        provenance=provenance,
        full_sha256=hash_bytes(full),
        mark_sha256=hash_bytes(mark),
        source_sha256=hash_bytes(raw),
        box=box,
        quality_flags=flags,
        supersedes=supersedes,
        created_at=datetime.now(UTC).isoformat(),
    )
    target = evaluation_root(reference_root, target)
    with exclusive(reference_root), read_store(reference_root) as c:
        known = {
            h for r in c.execute("SELECT original_hash,normalized_hash FROM assets") for h in r
        }
        known.update(json.loads(r[0])["hash"] for r in c.execute("SELECT data FROM crops"))
        hashes = {case.full_sha256, case.mark_sha256, case.source_sha256}
        if hashes & known:
            raise ValueError("Reference-derived pixels cannot be admitted as independent")
        previous = cases_in(target)
        if supersedes:
            parent = next((p for _, p in previous if p.case_id == supersedes), None)
            if not parent or (parent.capture_group, parent.partition) != (capture_group, partition):
                raise ValueError("Correction needs an active case in the same group and partition")
        elif hashes & set(read_holdouts(reference_root)["hashes"]):
            raise ValueError("Duplicate owner capture; use an append-only correction")
        if any(
            p.origin == case.origin
            and p.capture_group == capture_group
            and p.partition != partition
            for _, p in previous
        ):
            raise ValueError("A capture group cannot cross calibration/evaluation partitions")
        folder = target / "cases" / case_id
        folder.mkdir(parents=True, mode=0o700)
        try:
            (folder / "full.png").write_bytes(full)
            (folder / "mark.png").write_bytes(mark)
            write_json(folder / "case.json", case.model_dump(mode="json"))
            for path in folder.iterdir():
                path.chmod(0o600)
            # Register first: an interruption leaves conservative blocked hashes, never a leak.
            register(reference_root, case_id, hashes)
        except BaseException:
            shutil.rmtree(folder)
            raise
    return {
        "case_id": case_id,
        "origin": case.origin,
        "quality_flags": flags,
        "reference_or_training_import": False,
        "original_file_unchanged": True,
    }


def corpus(root, encoder):
    root = Path(root).resolve()
    path = local_file(root, "similarity/index.json")
    if path.stat().st_size > 32 * 1024 * 1024:
        raise ValueError("Index exceeds bound")
    raw = path.read_bytes()
    index = json.loads(raw)
    refs = references(root, index["include_internal_fair_use"])
    if index["model_revision"] != encoder.revision or dump(refs) != dump(
        [e["reference"] for e in index["entries"]]
    ):
        raise ValueError("Reference/model snapshot changed; rebuild the reference index")
    context = []
    with read_store(root) as c:
        for entry in index["entries"]:
            ref = entry["reference"]
            asset = c.execute(
                "SELECT data,normalized_hash FROM assets WHERE id=?", (ref["asset_id"],)
            ).fetchone()
            data = json.loads(asset[0])
            pixels = local_file(root, data["normalized_path"]).read_bytes()
            if hash_bytes(pixels) != asset[1]:
                raise ValueError("Reference context image integrity failed")
            vector = encoder.embed(pixels) if ref["is_crop"] else entry["vector"]
            context.append({"reference": ref, "vector": unit(vector)})
    return {"mark": index["entries"], "context": context}, hash_bytes(raw)


def freeze(root, target, encoder, only_origin=None):
    target = evaluation_root(root, target)
    entries, snapshot = corpus(root, encoder)
    results = []
    for folder, case in cases_in(target):
        if only_origin and case.origin != only_origin:
            continue
        rankings = {}
        vectors = {
            "mark": encoder.embed((folder / "mark.png").read_bytes()),
            "context": encoder.embed((folder / "full.png").read_bytes()),
        }
        for region, query_region, reference_region in [
            ("mark", "mark", "mark"),
            ("context", "context", "context"),
            ("query_context_reference_mark", "context", "mark"),
            ("query_mark_reference_context", "mark", "context"),
        ]:
            vector = vectors[query_region]
            rankings[region] = {}
            for mode in ["visual", "text", "hybrid"]:
                result = rank(
                    entries[reference_region],
                    vector,
                    case.transcription,
                    case.legibility,
                    mode,
                    quality_flags=[f for f in case.quality_flags if f != "NO_MARK_BOX"],
                    exclude_items=case.excluded_items,
                    exclude_assets=case.excluded_assets,
                    exclude_hashes=[case.full_sha256, case.mark_sha256, case.source_sha256],
                )
                # Retain evidence identities/hashes, not repeated full source-review prose.
                for match in result["matches"]:
                    match.pop("rights", None)
                rankings[region][mode] = result
        results.append(
            {
                "case_id": case.case_id,
                "origin": case.origin,
                "capture_group": case.capture_group,
                "partition": case.partition,
                "query_full_sha256": case.full_sha256,
                "query_mark_sha256": case.mark_sha256,
                "case_sha256": hash_bytes((folder / "case.json").read_bytes()),
                "rankings": rankings,
            }
        )
    run = {
        "schema": "jri004-frozen-run-1",
        "policy": POLICY,
        "model_revision": encoder.revision,
        "reference_index_sha256": snapshot,
        "implementation_sha256": hash_bytes(
            Path(__file__).read_bytes() + Path(__file__).with_name("recognition.py").read_bytes()
        ),
        "created_at": datetime.now(UTC).isoformat(),
        "cases": results,
        "labels_read_during_ranking": False,
        "training_approved": False,
    }
    run_id = uuid.uuid4().hex
    folder = target / "runs" / run_id
    folder.mkdir(parents=True, mode=0o700)
    write_json(folder / "run.json", run)
    return {
        "run_id": run_id,
        "cases": len(results),
        "run_sha256": hash_bytes((folder / "run.json").read_bytes()),
        "owner_photos": sum(r["origin"] == "OWNER_DECLARED_INDEPENDENT" for r in results),
    }


def score(run_file, expected_digest, labels_file):
    raw = Path(run_file).read_bytes()
    if hash_bytes(raw) != expected_digest:
        raise ValueError("Frozen ranking digest mismatch")
    run = json.loads(raw)
    if run.get("schema") != "jri004-frozen-run-1" or run["labels_read_during_ranking"]:
        raise ValueError("A blind frozen ranking is required")
    # This is deliberately the first label read, after predictions have been frozen.
    labels = json.loads(Path(labels_file).read_text())
    report = summarize(run["cases"], labels)
    report.update(
        run_sha256=expected_digest,
        reference_index_sha256=run["reference_index_sha256"],
        policy=run["policy"],
        labels_sha256=hash_bytes(Path(labels_file).read_bytes()),
    )
    return report


def controls(root, target):
    """Non-independent controls only, including all indexed confusing shop families."""
    root = Path(root).resolve()
    target = evaluation_root(root, target)
    refs = references(root, True)
    labels, created = [], []
    selected = {}
    for ref in refs:
        selected.setdefault(ref["item_id"], ref)
    tasks = [(r, "SOURCE_FAMILY_CONTROL") for r in selected.values()]
    tasks += [
        (r, "REPEATED_INSET_CONTROL")
        for r in refs
        if r["is_crop"] and sum(x["item_id"] == r["item_id"] for x in refs) > 1
    ]
    with read_store(root) as c:
        for ref, origin in tasks:
            case_id = uuid.uuid4().hex
            data = json.loads(
                c.execute("SELECT data FROM assets WHERE id=?", (ref["asset_id"],)).fetchone()[0]
            )
            full = local_file(root, data["normalized_path"]).read_bytes()
            mark = local_file(root, ref["path"]).read_bytes()
            legibility = (
                ref["legibility"] if ref["legibility"] in {"LEGIBLE", "PARTIAL"} else "UNREADABLE"
            )
            case = PhotoCase(
                case_id=case_id,
                origin=origin,
                capture_group=ref["item_id"],
                partition="CONTROL",
                legibility=legibility,
                transcription=ref["transcription"] or "" if legibility == "LEGIBLE" else "",
                transcript_basis="EXISTING_REFERENCE_ANNOTATION",
                provenance="Existing gallery reference/annotation; not an owner photo.",
                full_sha256=hash_bytes(full),
                mark_sha256=hash_bytes(mark),
                source_sha256=hash_bytes(mark),
                quality_flags=[],
                excluded_items=[ref["item_id"]] if origin == "SOURCE_FAMILY_CONTROL" else [],
                excluded_assets=[ref["asset_id"]],
                created_at=datetime.now(UTC).isoformat(),
            )
            folder = target / "cases" / case_id
            folder.mkdir(parents=True, mode=0o700)
            (folder / "full.png").write_bytes(full)
            (folder / "mark.png").write_bytes(mark)
            write_json(folder / "case.json", case.model_dump(mode="json"))
            expected = families(ref)
            available = [
                r
                for r in refs
                if r["asset_id"] != ref["asset_id"] and r["item_id"] not in case.excluded_items
            ]
            known = len(expected) == 1 and any(expected[0] in families(r) for r in available)
            labels.append(
                {
                    "case_id": case_id,
                    "target": "KNOWN" if known else "UNKNOWN",
                    "expected_family": expected[0] if known else None,
                    "evidence": "Gallery-attributed family comparison only. Singleton families "
                    "are absent after holdout; repeated insets are correlated. "
                    "Neither is independent maker authentication.",
                }
            )
            created.append(case_id)
    path = target / ("control-labels-" + uuid.uuid4().hex + ".json")
    write_json(path, labels)
    return {"control_cases": len(created), "labels_file": str(path), "owner_photos": 0}


def render(root, target, run_file, expected_digest, case_id, output):
    if len(case_id) != 32 or any(c not in "0123456789abcdef" for c in case_id):
        raise ValueError("Invalid case identity")
    raw = Path(run_file).read_bytes()
    if hash_bytes(raw) != expected_digest:
        raise ValueError("Frozen ranking digest mismatch")
    frozen = next(c for c in json.loads(raw)["cases"] if c["case_id"] == case_id)
    folder = local_file(target, "cases/" + case_id)
    case = load_case(folder)
    if hash_bytes((folder / "case.json").read_bytes()) != frozen["case_sha256"]:
        raise ValueError("Query metadata changed after freezing")
    output = Path(output).resolve()
    if output.exists() or output.suffix.lower() != ".html":
        raise ValueError("New private HTML path required")
    private_root(output.parent)

    def image(raw):
        return "data:image/png;base64," + base64.b64encode(normalize(raw)[0]).decode()

    parts = [
        "<!doctype html><meta charset='utf-8'>",
        "<meta http-equiv='Content-Security-Policy' content=\"default-src 'none'; "
        "img-src data:; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'\">",
        "<title>Private hallmark evaluation</title><style>body{font:17px system-ui;"
        "max-width:1100px;margin:auto}img{max-width:200px;max-height:150px}"
        "article{border-top:1px solid #aaa;padding:1rem}</style>",
        "<h1>Private hallmark evaluation</h1><p>Similarity is not authentication. "
        "Scores are not calibrated probabilities. Human transcription uses visible text only.</p>",
        "<p>Origin: " + html.escape(case.origin) + "</p>",
        "<p>Legible transcript: " + html.escape(case.transcription or "None") + "</p>",
    ]
    for region, approaches in frozen["rankings"].items():
        parts.append(
            "<h2>"
            + html.escape(region)
            + "</h2><img alt='Private query region' src='"
            + image(
                (
                    folder
                    / (
                        "mark.png"
                        if region in {"mark", "query_mark_reference_context"}
                        else "full.png"
                    )
                ).read_bytes()
            )
            + "'>"
        )
        for mode, result in approaches.items():
            parts.append(
                "<h3>"
                + html.escape(mode + ": " + result["decision"])
                + "</h3><p>"
                + html.escape(result["reason"])
                + "</p>"
            )
            for match in result["matches"]:
                pixels = local_file(root, match["path"]).read_bytes()
                if hash_bytes(pixels) != match["image_sha256"]:
                    raise ValueError("Reference evidence integrity failed")
                url = (
                    match["source_url"] if urlsplit(match["source_url"]).scheme == "https" else "#"
                )
                parts.append(
                    "<article><img alt='Reference mark' src='"
                    + image(pixels)
                    + "'><p><a rel='noreferrer noopener' href='"
                    + html.escape(url, quote=True)
                    + "'>"
                    + html.escape(match["source_name"] + " / " + match["external_id"])
                    + "</a></p><p>Attributed candidates: "
                    + html.escape(", ".join(match["candidate_makers"]) or "Unknown")
                    + "</p><p>"
                    + html.escape(match["mark_description"])
                    + "</p><p>Cosine: "
                    + f"{match['similarity']:.4f}"
                    + "; text agreement: "
                    + f"{match['text_score']:.4f}"
                    + "</p><p>Reference attribution, not independently authenticated.</p></article>"
                )
    output.write_text("\n".join(parts))
    output.chmod(0o600)


def main():
    os.umask(0o077)
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root", type=Path, default=Path("data/jri"))
    p.add_argument("--evaluation-root", type=Path, default=Path("data/jri-evaluation"))
    p.add_argument("--weights", type=Path, default=Path("data/jri-models/resnet18-f37072fd.pth"))
    sub = p.add_subparsers(dest="command", required=True)
    i = sub.add_parser("intake")
    i.add_argument("photo", type=Path)
    i.add_argument("--capture-group", required=True)
    i.add_argument("--provenance", required=True)
    i.add_argument(
        "--legibility", choices=["LEGIBLE", "PARTIAL", "UNREADABLE"], default="UNREADABLE"
    )
    i.add_argument("--transcription", default="")
    i.add_argument("--box", nargs=4, type=int)
    i.add_argument("--partition", choices=["EVALUATION", "CALIBRATION"], default="EVALUATION")
    i.add_argument("--supersedes")
    sub.add_parser("controls")
    sub.add_parser("freeze")
    s = sub.add_parser("score")
    s.add_argument("run_file", type=Path)
    s.add_argument("labels_file", type=Path)
    s.add_argument("--run-sha256", required=True)
    h = sub.add_parser("report")
    h.add_argument("run_file", type=Path)
    h.add_argument("case_id")
    h.add_argument("output", type=Path)
    h.add_argument("--run-sha256", required=True)
    a = p.parse_args()
    if a.command == "intake":
        result = admit(
            a.root,
            a.evaluation_root,
            a.photo,
            a.capture_group,
            a.provenance,
            a.legibility,
            a.transcription,
            tuple(a.box) if a.box else None,
            a.partition,
            a.supersedes,
        )
    elif a.command == "controls":
        result = controls(a.root, a.evaluation_root)
    elif a.command == "freeze":
        result = freeze(a.root, a.evaluation_root, Encoder(a.weights))
    elif a.command == "score":
        result = score(a.run_file, a.run_sha256, a.labels_file)
    else:
        render(a.root, a.evaluation_root, a.run_file, a.run_sha256, a.case_id, a.output)
        result = {"private_report_saved": True, "uploaded": False}
    print(json.dumps(result, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
