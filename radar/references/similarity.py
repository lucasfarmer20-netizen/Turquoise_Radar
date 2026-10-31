"""Local visual retrieval. Cosine similarity is not maker or material authentication."""

import argparse
import base64
import hashlib
import html
import io
import json
import math
import os
from pathlib import Path
from urllib.parse import urlsplit

from PIL import Image

from radar.references.ingest import eligible
from radar.references.library import local_file
from radar.references.media import MAX_BYTES, normalize
from radar.references.persistence import read_store
from radar.references.schema import Mark
from radar.references.store import dump, exclusive, get_source, private_root

WEIGHTS_URL = "https://download.pytorch.org/models/resnet18-f37072fd.pth"
WEIGHTS_SHA256 = "f37072fd47e89c5e827621c5baffa7500819f7896bbacec160b1a16c560e07ec"
REVISION = "resnet18-imagenet1k-v1-avgpool-letterbox224-v1:" + WEIGHTS_SHA256
WARNING = (
    "Visual similarity suggests references to inspect, not authentication. Candidate makers "
    "and materials remain source attributions unless separately supported by owner/test evidence. "
    "Cosine scores are not probabilities; no acceptance threshold is calibrated."
)


def hash_bytes(raw):
    return hashlib.sha256(raw).hexdigest()


def unit(vector):
    if not vector or not all(type(v) in (int, float) and math.isfinite(v) for v in vector):
        raise ValueError("Invalid embedding")
    length = math.sqrt(sum(v * v for v in vector))
    if not math.isfinite(length) or length <= 0:
        raise ValueError("Invalid embedding norm")
    return [float(v / length) for v in vector]


def fetch_model(destination):
    """The only network-enabled operation in this module; never sends private images."""
    import urllib.request

    destination = Path(destination).resolve()
    private_root(destination.parent)
    if destination.exists():
        if hash_bytes(destination.read_bytes()) != WEIGHTS_SHA256:
            raise ValueError("Existing model digest differs; refusing replacement")
        return {"sha256": WEIGHTS_SHA256, "downloaded": False}

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *_args):
            return None

    request = urllib.request.Request(WEIGHTS_URL, headers={"User-Agent": "TurquoiseRadar-JRI/0.1"})
    with urllib.request.build_opener(NoRedirect()).open(request, timeout=60) as response:
        raw = response.read(60_000_001)
    if len(raw) > 60_000_000 or hash_bytes(raw) != WEIGHTS_SHA256:
        raise ValueError("Official model digest mismatch")
    with destination.open("xb") as f:
        f.write(raw)
    destination.chmod(0o600)
    return {"sha256": WEIGHTS_SHA256, "downloaded": True}


class Encoder:
    revision = REVISION

    def __init__(self, weights):
        path = Path(weights)
        if not path.is_file() or hash_bytes(path.read_bytes()) != WEIGHTS_SHA256:
            raise ValueError("Pinned local weights required; use fetch-model explicitly")
        try:
            import torch
            from torchvision.models import resnet18
            from torchvision.transforms import functional as tf
        except ImportError as exc:
            raise ValueError(
                "Install optional similarity extra; no automatic installation"
            ) from exc
        torch.set_num_threads(2)
        net = resnet18(weights=None)  # Never download weights implicitly or execute hub code.
        net.load_state_dict(torch.load(path, map_location="cpu", weights_only=True))
        self.net = torch.nn.Sequential(*list(net.children())[:-1]).eval()
        self.torch, self.tf = torch, tf

    def embed(self, raw):
        normalized, _ = normalize(raw)
        with Image.open(io.BytesIO(normalized)) as im:
            # A whole-mark letterbox avoids losing letters/pictographs to a center crop.
            im.thumbnail((224, 224), Image.Resampling.BILINEAR)
            canvas = Image.new("RGB", (224, 224), (255, 255, 255))
            canvas.paste(im, ((224 - im.width) // 2, (224 - im.height) // 2))
            # Upscale small marks only for the model input, never as added source detail.
            if max(im.size) < 224:
                size = tuple(max(1, round(v * 224 / max(im.size))) for v in im.size)
                im = im.resize(size, Image.Resampling.BILINEAR)
                canvas = Image.new("RGB", (224, 224), "white")
                canvas.paste(im, ((224 - im.width) // 2, (224 - im.height) // 2))
            tensor = self.tf.normalize(
                self.tf.to_tensor(canvas), [0.485, 0.456, 0.406], [0.229, 0.224, 0.225]
            )
        with self.torch.inference_mode():
            vector = self.net(tensor.unsqueeze(0)).flatten().tolist()
        return unit(vector)


def references(root, include_fair_use=False):
    """Only photograph-backed, current eligible mark evidence; prefer explicit crops."""
    root = Path(root).resolve()
    with read_store(root) as c:
        rows = []
        for row in c.execute("SELECT id,item_id,data FROM marks ORDER BY id"):
            mark = Mark.model_validate_json(row["data"])
            if not mark.photo_id or mark.legibility == "REPORTED_ONLY":
                continue
            item = c.execute(
                "SELECT * FROM items WHERE id=? AND excluded=0", (row["item_id"],)
            ).fetchone()
            if not item:
                continue
            source = get_source(c, item["source"])
            if not eligible(source) or (
                source.classification == "INTERNAL_FAIR_USE" and not include_fair_use
            ):
                continue
            cr = c.execute(
                "SELECT * FROM crops WHERE id=? AND removed=0", (mark.photo_id,)
            ).fetchone()
            asset_id = cr["asset_id"] if cr else mark.photo_id
            asset = c.execute(
                "SELECT * FROM assets WHERE id=? AND removed=0 AND item_id=?",
                (asset_id, row["item_id"]),
            ).fetchone()
            if not asset:
                continue
            data = json.loads(asset["data"])
            snapshot = data["source_review"]
            if snapshot["classification"] not in {"OPEN_ACCESS", "INTERNAL_FAIR_USE"}:
                continue
            if snapshot["classification"] == "INTERNAL_FAIR_USE" and not include_fair_use:
                continue
            cd = json.loads(cr["data"]) if cr else None
            path = cd["path"] if cr else data["normalized_path"]
            digest = cd["hash"] if cr else asset["normalized_hash"]
            raw = local_file(root, path).read_bytes()
            if hash_bytes(raw) != digest:
                raise ValueError("Reference image integrity failed")
            catalog = json.loads(item["data"])
            rows.append(
                {
                    "reference_id": mark.photo_id,
                    "asset_id": asset_id,
                    "item_id": item["id"],
                    "external_id": item["external_id"],
                    "source": item["source"],
                    "source_name": source.name,
                    "source_url": catalog["page_url"],
                    "title": catalog["title"],
                    "candidate_makers": mark.candidate_makers or catalog.get("artists", []),
                    "candidate_basis": "attributed reference metadata; not authenticated",
                    "transcription": mark.transcription,
                    "mark_description": mark.pictorial_description,
                    "legibility": mark.legibility,
                    "path": path,
                    "image_sha256": digest,
                    "is_crop": bool(cr),
                    "source_review_sha256": hash_bytes(source.model_dump_json().encode()),
                    "rights": data["rights"],
                }
            )
        crop_assets = {r["asset_id"] for r in rows if r["is_crop"]}
        # Once a reviewed mark crop exists, do not embed its entire decorative jewelry photo.
        unique = {}
        for row in rows:
            if row["is_crop"] or row["asset_id"] not in crop_assets:
                unique[row["reference_id"]] = row
        return sorted(unique.values(), key=lambda r: r["reference_id"])


def write_json(path, value):
    raw = (json.dumps(value, indent=2, allow_nan=False) + "\n").encode()
    temporary = path.with_suffix(".tmp")
    temporary.write_bytes(raw)
    temporary.chmod(0o600)
    temporary.replace(path)


def build_index(root, encoder, include_fair_use=False):
    root = private_root(root)
    with exclusive(root):
        refs = references(root, include_fair_use)
        if not refs or len(refs) > 5000:
            raise ValueError("Need 1–5000 eligible photograph-backed marks/crops")
        entries = []
        for ref in refs:
            raw = local_file(root, ref["path"]).read_bytes()
            entries.append({"reference": ref, "vector": unit(encoder.embed(raw))})
        index = {
            "schema": "jri-similarity-1",
            "model_revision": encoder.revision,
            "include_internal_fair_use": include_fair_use,
            "entries": entries,
        }
        folder = local_file(root, "similarity")
        folder.mkdir(exist_ok=True, mode=0o700)
        write_json(folder / "index.json", index)
        return {
            "references": len(entries),
            "items": len({r["item_id"] for r in refs}),
            "model_revision": encoder.revision,
            "warning": WARNING,
        }


def search(root, encoder, raw, top_k=5, exclude_items=(), exclude_hashes=()):
    root = Path(root).resolve()
    path = local_file(root, "similarity/index.json")
    if path.stat().st_size > 32 * 1024 * 1024:
        raise ValueError("Index exceeds bound")
    index = json.loads(path.read_text())
    if index.get("schema") != "jri-similarity-1" or index["model_revision"] != encoder.revision:
        raise ValueError("Index/model revision mismatch; rebuild")
    refs = references(root, index["include_internal_fair_use"])
    if dump(refs) != dump([entry["reference"] for entry in index["entries"]]):
        raise ValueError("Reference eligibility/evidence changed; rebuild index")
    vector = unit(encoder.embed(raw))
    ranked = []
    for entry in index["entries"]:
        ref = entry["reference"]
        if ref["item_id"] in exclude_items or ref["image_sha256"] in exclude_hashes:
            continue
        other = unit(entry["vector"])
        if len(vector) != len(other):
            raise ValueError("Embedding dimension mismatch")
        score = max(-1.0, min(1.0, sum(a * b for a, b in zip(vector, other, strict=True))))
        ranked.append(dict(ref, similarity=score))
    ranked.sort(key=lambda r: (-r["similarity"], r["reference_id"]))
    # Multiple views/crops from one object do not crowd out other candidate objects.
    results, seen = [], set()
    for row in ranked:
        if row["item_id"] in seen:
            continue
        seen.add(row["item_id"])
        results.append(row)
        if len(results) == min(50, max(1, top_k)):
            break
    return {
        "matches": results,
        "warning": WARNING,
        "model_revision": encoder.revision,
        "eligible_reference_count": len(refs),
        "authenticated": False,
    }


def query_file(path):
    path = Path(path)
    if not path.is_file() or path.stat().st_size > MAX_BYTES:
        raise ValueError("Private local image file required (maximum 20 MiB); no URLs")
    raw = path.read_bytes()
    normalize(raw)
    return raw


def render_report(root, output, query_raw, result):
    output = Path(output).resolve()
    if output.exists() or output.suffix.lower() != ".html":
        raise ValueError("Report needs a new private .html path; refusing overwrite")
    private_root(output.parent)

    def image(raw):
        normalized, _ = normalize(raw)
        return "data:image/png;base64," + base64.b64encode(normalized).decode()

    parts = [
        "<!doctype html><meta charset='utf-8'>",
        "<meta http-equiv='Content-Security-Policy' content=\"default-src 'none'; "
        "img-src data:; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'\">",
        "<title>Private hallmark references</title>",
        "<style>body{font:17px system-ui;max-width:960px;margin:2rem auto;padding:1rem}"
        "article{border-top:1px solid #bbb;padding:1rem}img{max-width:280px;max-height:230px}"
        "strong{color:#76420a}</style><h1>Private hallmark references</h1>",
        "<strong>" + html.escape(WARNING) + "</strong>",
        "<h2>Query</h2><img alt='Private query' src='" + image(query_raw) + "'>",
    ]
    for r in result["matches"]:
        source_url = r["source_url"]
        if urlsplit(source_url).scheme != "https":
            source_url = "#"
        parts.extend(
            [
                "<article><h2>" + html.escape(r["external_id"]) + "</h2>",
                "<img alt='Reference mark' src='"
                + image(local_file(root, r["path"]).read_bytes())
                + "'>",
                "<p>Source: <a rel='noreferrer noopener' href='"
                + html.escape(source_url, quote=True)
                + "'>"
                + html.escape(r["source_name"])
                + "</a></p>",
                "<p>Candidate: "
                + html.escape(", ".join(r["candidate_makers"]) or "Unknown")
                + "</p>",
                "<p>Mark: "
                + html.escape(r["transcription"] or r["mark_description"] or "Unresolved")
                + "</p><p>Description: "
                + html.escape(r["mark_description"] or "No separate description recorded")
                + "</p><p>Legibility: "
                + html.escape(r["legibility"])
                + "</p><p>Cosine similarity: "
                + f"{r['similarity']:.4f}"
                + "</p>",
                "<p>" + html.escape(r["candidate_basis"]) + "</p></article>",
            ]
        )
    output.write_text("\n".join(parts))
    output.chmod(0o600)


def evaluate(root, encoder, cases):
    """Explicit labels, exact-self exclusion and stratified provenance; no authentication metric."""
    reports = []
    for case in cases:
        raw = query_file(local_file(root, case["query_path"]))
        normalized, _ = normalize(raw)
        positives, negatives = set(case["positive_items"]), set(case["negative_items"])
        if not positives or positives & negatives or not case.get("label_evidence"):
            raise ValueError("Disjoint explicitly reviewed labels and evidence required")
        if case["query_kind"] not in {
            "independent_capture",
            "source_family_holdout",
            "repeated_source_inset",
            "synthetic_perturbation",
        }:
            raise ValueError("Declare query independence")
        result = search(
            root,
            encoder,
            raw,
            top_k=5,
            exclude_items=case.get("exclude_items", []),
            exclude_hashes=[hash_bytes(raw), hash_bytes(normalized)],
        )
        ids = [m["item_id"] for m in result["matches"]]
        judged = [id_ for id_ in ids if id_ in positives | negatives]
        hits = [id_ for id_ in ids if id_ in positives]
        reports.append(
            {
                "case_id": case["id"],
                "query_kind": case["query_kind"],
                "label_evidence": case["label_evidence"],
                "returned_item_ids": ids,
                "top1_correct": bool(ids and ids[0] in positives),
                "hit_at_5": bool(hits),
                "recall_at_5": len(hits) / len(positives),
                "precision_at_5_judged": sum(id_ in positives for id_ in judged) / len(judged)
                if judged
                else None,
                "false_matches_at_5": sum(id_ in negatives for id_ in ids),
                "unjudged_at_5": len(ids) - len(judged),
            }
        )
    strata = {}
    for kind in sorted({r["query_kind"] for r in reports}):
        group = [r for r in reports if r["query_kind"] == kind]
        strata[kind] = {
            "queries": len(group),
            "precision_at_1": sum(r["top1_correct"] for r in group) / len(group),
            "hit_at_5": sum(r["hit_at_5"] for r in group) / len(group),
            "false_matches_at_5": sum(r["false_matches_at_5"] for r in group),
        }
    return {
        "cases": reports,
        "strata": strata,
        "warning": WARNING,
        "independent_query_count": sum(r["query_kind"] == "independent_capture" for r in reports),
        "training_approved": False,
        "deployment_precision_established": False,
    }


def main():
    os.umask(0o077)
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root", type=Path, default=Path("data/jri"))
    p.add_argument("--weights", type=Path, default=Path("data/jri-models/resnet18-f37072fd.pth"))
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("fetch-model")
    build = sub.add_parser("index")
    build.add_argument("--include-internal-fair-use", action="store_true")
    query = sub.add_parser("query")
    query.add_argument("photo", type=Path)
    query.add_argument("--top-k", type=int, default=5)
    query.add_argument("--html", type=Path)
    ev = sub.add_parser("evaluate")
    ev.add_argument("cases", type=Path)
    a = p.parse_args()
    if a.command == "fetch-model":
        result = fetch_model(a.weights)
    else:
        read_store(a.root).close()  # Never create a missing dataset for setup/query.
        encoder = Encoder(a.weights)
        if a.command == "index":
            result = build_index(a.root, encoder, a.include_internal_fair_use)
        elif a.command == "query":
            raw = query_file(a.photo)
            result = search(a.root, encoder, raw, a.top_k)
            if a.html:
                render_report(a.root, a.html, raw, result)
        else:
            result = evaluate(a.root, encoder, json.loads(a.cases.read_text()))
    print(json.dumps(result, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
