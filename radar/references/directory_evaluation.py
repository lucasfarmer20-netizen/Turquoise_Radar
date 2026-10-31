"""Private side-by-side frozen photograph benchmark; no label-derived accuracy claims."""

import argparse
import base64
import html
import io
import json
from pathlib import Path

from PIL import Image

from radar.references.directory import digest, hybrid, source_registry, stats
from radar.references.library import local_file
from radar.references.persistence import verify
from radar.references.similarity import Encoder, search
from radar.references.store import private_root


def evaluate(directory, library, baseline, annotations, output, weights):
    baseline, output = Path(baseline).resolve(), Path(output).resolve()
    library = Path(library).resolve()
    if output.exists() or output.is_relative_to(library):
        raise ValueError("New private output outside the historical library required")
    verify(library)
    frozen = json.loads((baseline / "reviewed-results.json").read_text())
    if digest((library / "similarity/index.json").read_bytes()) != frozen["index_sha256"]:
        raise ValueError("Historical index changed; baseline is no longer comparable")
    annotation_bytes = Path(annotations).read_bytes()
    annotations = json.loads(annotation_bytes)
    if set(annotations) != {r["query_id"] for r in frozen["rows"]}:
        raise ValueError("One reviewed annotation for every frozen query required")
    encoder = Encoder(weights)
    rows = []
    for old in frozen["rows"]:
        q = old["query_id"]
        if not q.isalnum():
            raise ValueError("Invalid case identifier")
        raw = local_file(baseline, f"normalized/{q}.png").read_bytes()
        crop = local_file(baseline, f"crops/{q}.png").read_bytes()
        if digest(raw) != old["query_sha256"] or digest(crop) != old["crop_sha256"]:
            raise ValueError("Frozen query/crop changed")
        a = annotations[q]
        if a["legibility"] not in {"LEGIBLE", "PARTIAL", "UNREADABLE"} or not a.get("evidence"):
            raise ValueError("Reviewed visible transcription and evidence required")
        original = search(library, encoder, raw, top_k=5)
        visual = search(library, encoder, crop, top_k=50)
        if [(m["reference_id"], round(m["similarity"], 8)) for m in original["matches"]] != [
            (m["reference_id"], round(m["similarity"], 8)) for m in old["full"]["matches"]
        ]:
            raise ValueError("Original matcher no longer reproduces frozen baseline")
        result = hybrid(
            directory,
            a.get("transcription", ""),
            legibility=a["legibility"],
            description=a.get("description", ""),
            visual_matches=visual["matches"],
        )
        rows.append(
            {
                "query_id": q,
                "annotation": a,
                "original": original,
                "expanded": result,
                "ground_truth": "UNVERIFIED",
                "independent_camera_provenance": "UNESTABLISHED",
            }
        )
    private_root(output)
    receipt = {
        "schema": "jri-directory-evaluation-1",
        "reference_coverage": stats(directory),
        "source_registry": source_registry(directory),
        "index_sha256": frozen["index_sha256"],
        "frozen_results_sha256": digest((baseline / "reviewed-results.json").read_bytes()),
        "annotations_sha256": digest(annotation_bytes),
        "directory_db_sha256": digest((Path(directory) / "directory.db").read_bytes()),
        "model_revision": encoder.revision,
        "rows": rows,
        "accuracy": None,
        "warning": (
            "Candidate filtering and source-attributed leads only. "
            "No independent maker labels; no measured accuracy gain."
        ),
    }
    (output / "results.json").write_text(json.dumps(receipt, indent=2) + "\n")
    render(library, baseline, output / "comparison.html", receipt)
    for p in output.iterdir():
        p.chmod(0o600)
    return {
        "queries": len(rows),
        "candidate_recommendations": sum(r["expanded"]["decision"] == "CANDIDATE" for r in rows),
        "unknown": sum(r["expanded"]["decision"] == "UNKNOWN" for r in rows),
        "accuracy": None,
    }


def render(library, baseline, destination, receipt):
    def esc(value):
        return html.escape(str(value))

    def image(path):
        with Image.open(path) as original:
            im = original.convert("RGB")
            im.thumbnail((500, 360))
            buf = io.BytesIO()
            im.save(buf, format="JPEG", quality=85)
        return (
            '<img src="data:image/jpeg;base64,' + base64.b64encode(buf.getvalue()).decode() + '">'
        )

    parts = [
        '<!doctype html><html><head><meta charset="utf-8">',
        "<meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'none'; img-src data:; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'\">",  # noqa: E501
        "<title>Private hallmark directory benchmark</title><style>body{font:16px system-ui;margin:24px}section{border-top:3px solid #888;padding:20px 0}table{width:100%;table-layout:fixed;border-collapse:collapse}td,th{padding:10px;border:1px solid #ccc;vertical-align:top;overflow-wrap:anywhere}img{max-width:100%;max-height:250px}small{font-size:12px}.photos{display:flex;gap:20px}.photos div{width:45%}</style></head><body>",  # noqa: E501
        "<h1>Private JRI-004 expanded directory comparison</h1>",
        f"<p>{receipt['reference_coverage']['records']} active source-attributed directory records. Existing visual index is unchanged; external-only photographs are not copied or embedded. No independent corroboration or authentication is claimed.</p>",  # noqa: E501
        "<p>All 16 original photographs and frozen crop hashes were verified. Original ResNet top-five results reproduced exactly to eight decimal places. Expanded visual ranking compares the same frozen crop against all indexed records, with complete legible-text contradiction penalties. Manual transcripts are reviewed input, not automatic OCR.</p>",  # noqa: E501
        "<p><strong>All evaluation maker labels remain UNVERIFIED. CANDIDATE means a source-attributed mark-text research lead, not an identified or authenticated owner piece. UNKNOWN is returned without unambiguous supported mark text. Accuracy, precision, false-match and abstention rates cannot be measured.</strong></p>",  # noqa: E501
        "<p>Query-derived coverage leads influenced collection: this is targeted research, not an untouched confirmatory evaluation. Shared pebbled staging in four photos is a correlated capture family. Higher cosine is not higher identity confidence. No training or external inference.</p>",  # noqa: E501
    ]
    for row in receipt["rows"]:
        q = row["query_id"]
        a = row["annotation"]
        r = row["expanded"]
        parts += [
            f'<section><h2>{esc(q)} — {esc(r["decision"])}</h2><p>Visible transcription: {esc(a.get("transcription", ""))}; {esc(a["legibility"])}</p><p>{esc(a["evidence"])}</p><div class="photos"><div>Query photograph'  # noqa: E501
            + image(baseline / "normalized" / f"{q}.png")
            + "</div><div>Frozen reviewed crop"
            + image(baseline / "crops" / f"{q}.png")
            + "</div></div>",
            "<h3>Original whole-photo ResNet: top five</h3><table><tr><th>Candidate / source</th><th>Reference image</th><th>Cosine / visible reference text</th></tr>",  # noqa: E501
        ]
        for m in row["original"]["matches"]:
            parts += [
                f'<tr><td>{esc("; ".join(m["candidate_makers"]))}<br><a rel="noreferrer" href="{esc(m["source_url"])}">{esc(m["source_name"])}</a><br><small>{esc(m["reference_id"])}</small></td><td>'  # noqa: E501
                + image(local_file(library, m["path"]))
                + f"</td><td>{m['similarity']:.6f}<p>{esc(m['transcription'])}</p><p>{esc(m['mark_description'])}</p></td></tr>"  # noqa: E501
            ]
        parts += [
            "</table><h3>Expanded factual directory: candidate evidence and gaps</h3><p>"
            + esc(r["reason"])
            + "</p><ul>"
        ]
        if not r["candidates"]:
            parts += ["<li>No supported directory lead for the reviewed query information.</li>"]
        for c in r["candidates"]:
            parts += [
                f'<li><strong>{esc(c["maker_name"])}</strong> — {esc(c["evidence"])}; {esc(c["attribution"])}<p>Reported literal mark: {esc(c["transcription"])} ({esc(c["transcription_status"])})</p><p>Description: {esc(c["description"])}</p><p>Contradictions: {esc(c["contradictions"])}</p><a rel="noreferrer" href="{esc(c["source_url"])}">Original directory page</a>; photographs remain external links, no copying approval: '  # noqa: E501
                + " ".join(
                    f'<a rel="noreferrer" href="{esc(u)}">reference mark {n + 1}</a>'
                    for n, u in enumerate(c["image_urls"])
                )
                + "</li>"
            ]
        parts += [
            "</ul><p>Missing evidence: independently documented owner identity; independent corroboration; direct visual comparison with any external-only candidate photograph; mark variant and period compatibility. Source captions and cultural context remain claims.</p>",  # noqa: E501
            "<h3>Crop visual candidates after text contradiction filtering (first five)</h3><table><tr><th>Candidate</th><th>Reference image</th><th>Raw cosine / text evidence</th></tr>",  # noqa: E501
        ]
        for m in r["visual_candidates"][:5]:
            parts += [
                f'<tr><td>{esc("; ".join(m["candidate_makers"]))}<br><a rel="noreferrer" href="{esc(m["source_url"])}">Source</a></td><td>'  # noqa: E501
                + image(local_file(library, m["path"]))
                + f"</td><td>{m['similarity']:.6f}; {esc(m['text_consistency'])}<p>{esc(m['transcription'])}</p><p>Adjusted rank score {m['adjusted_similarity']:.6f}; this is a policy penalty, not cosine or probability.</p></td></tr>"  # noqa: E501
            ]
        parts += ["</table></section>"]
    parts += ["</body></html>"]
    Path(destination).write_text("".join(parts))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--directory", type=Path, default=Path("data/jri-directory"))
    p.add_argument("--library", type=Path, default=Path("data/jri"))
    p.add_argument("--baseline", type=Path, default=Path("data/jri004-owner-baseline"))
    p.add_argument("--weights", type=Path, default=Path("data/jri-models/resnet18-f37072fd.pth"))
    p.add_argument("annotations", type=Path)
    p.add_argument("output", type=Path)
    a = p.parse_args()
    print(
        json.dumps(
            evaluate(a.directory, a.library, a.baseline, a.annotations, a.output, a.weights),
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
