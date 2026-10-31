"""Synthetic privacy, evidence and retrieval tests; no model download or inference API."""

import io
import json

import pytest
from PIL import Image, ImageDraw

from radar.references.ingest import save_asset
from radar.references.library import add_mark, crop
from radar.references.schema import PhotoRef, Source
from radar.references.similarity import (
    build_index,
    evaluate,
    references,
    render_report,
    search,
    unit,
)
from radar.references.store import connect, dump, put_source


class FakeEncoder:
    revision = "synthetic-color-only-fixture"

    def embed(self, raw):
        with Image.open(io.BytesIO(raw)) as im:
            return unit([v + 1 for v in im.convert("RGB").resize((1, 1)).getpixel((0, 0))])


def image(color):
    im = Image.new("RGB", (96, 96), color)
    draw = ImageDraw.Draw(im)
    draw.rectangle((24, 24, 60, 60), outline="white", width=3)
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


@pytest.fixture
def library(tmp_path):
    root = tmp_path / "private"
    source = Source(
        id="synthetic",
        name="Synthetic fixture",
        url="https://example.test",
        terms_url="https://example.test/terms",
        reviewed_on=__import__("datetime").date.today(),
        classification="INTERNAL_FAIR_USE",
        access_method="REVIEWED_MANIFEST",
        hosts=["example.test"],
        enabled=True,
        restrictions="Synthetic fixture only",
        rationale="Synthetic offline test",
        provenance="Synthetic generated images",
    )
    with connect(root) as c:
        put_source(c, source)
        for n, color in enumerate(["red", "blue", "green"]):
            id_ = f"synthetic-{n}"
            item = {
                "external_id": id_,
                "title": "Synthetic hallmark",
                "page_url": source.url,
                "artists": ["SYNTHETIC MAKER"],
            }
            c.execute(
                "INSERT INTO items VALUES (?,?,?,?,0,?)",
                (id_, source.id, id_, dump(item), "2026-01-01"),
            )
            c.commit()
            save_asset(
                c,
                root,
                source,
                id_,
                PhotoRef(
                    url=f"https://example.test/{n}.png",
                    view="HALLMARK",
                    license="UNLICENSED_INTERNAL_FAIR_USE",
                    license_url=source.terms_url,
                    evidence="Synthetic",
                    credit="Synthetic",
                ),
                image(color),
            )
            asset = c.execute("SELECT id FROM assets WHERE item_id=?", (id_,)).fetchone()[0]
            add_mark(
                c,
                {
                    "item_id": id_,
                    "photo_id": asset,
                    "transcription": f"TEST {n}",
                    "legibility": "LEGIBLE",
                    "origin": "REFERENCE_REVIEW",
                    "evidence": "Synthetic",
                    "candidate_makers": [f"Fixture maker {n}"],
                },
            )
        c.commit()
    return root


def test_ranked_output_keeps_claim_uncertainty_and_private_report(library, monkeypatch):
    # Any accidental HTTP in index/query fails this test.
    import urllib.request

    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: pytest.fail("Network forbidden"))
    encoder = FakeEncoder()
    build_index(library, encoder, True)
    result = search(library, encoder, image("red"))
    assert result["matches"][0]["item_id"] == "synthetic-0"
    assert result["authenticated"] is False
    assert "not probabilities" in result["warning"]
    assert "attributed" in result["matches"][0]["candidate_basis"]
    output = library / "report.html"
    result["matches"][0]["candidate_makers"] = ["<script>alert(1)</script>"]
    render_report(library, output, image("red"), result)
    text = output.read_text()
    assert "<script>" not in text and "&lt;script&gt;" in text
    assert "default-src 'none'" in text and "data:image/png" in text
    assert output.stat().st_mode & 0o777 == 0o600


def test_fair_use_is_opt_in_and_historical_snapshot_cannot_be_relicensed(library):
    assert references(library) == []
    assert len(references(library, True)) == 3
    with connect(library) as c:
        source = json.loads(c.execute("SELECT data FROM sources").fetchone()[0])
        source["classification"] = "OPEN_ACCESS"
        put_source(c, Source.model_validate(source))
        c.commit()
    assert references(library) == []


def test_exclusion_and_crop_removal_invalidate_index(library):
    encoder = FakeEncoder()
    build_index(library, encoder, True)
    with connect(library) as c:
        c.execute("UPDATE items SET excluded=1 WHERE id='synthetic-0'")
        c.commit()
    with pytest.raises(ValueError, match="changed"):
        search(library, encoder, image("red"))


def test_explicit_crop_replaces_whole_photo_and_enforces_parent(library):
    with connect(library) as c:
        asset = c.execute("SELECT id FROM assets WHERE item_id='synthetic-0'").fetchone()[0]
    cr = crop(library, asset, (0, 0, 64, 64))
    with connect(library) as c:
        add_mark(
            c,
            {
                "item_id": "synthetic-0",
                "photo_id": cr,
                "legibility": "PARTIAL",
                "origin": "REFERENCE_REVIEW",
                "evidence": "Synthetic cropped mark",
            },
        )
    refs = references(library, True)
    assert len(refs) == 3
    assert next(r for r in refs if r["item_id"] == "synthetic-0")["reference_id"] == cr
    build_index(library, FakeEncoder(), True)
    with connect(library) as c:
        c.execute("UPDATE assets SET removed=1 WHERE id=?", (asset,))
        c.commit()
    with pytest.raises(ValueError, match="changed"):
        search(library, FakeEncoder(), image("red"))


def test_corrupt_reference_or_model_revision_fails_closed(library):
    encoder = FakeEncoder()
    build_index(library, encoder, True)
    path = library / "similarity/index.json"
    index = json.loads(path.read_text())
    index["model_revision"] = "other-model"
    path.write_text(json.dumps(index))
    with pytest.raises(ValueError, match="revision"):
        search(library, encoder, image("red"))
    with connect(library) as c:
        d = json.loads(c.execute("SELECT data FROM assets LIMIT 1").fetchone()[0])
    (library / d["normalized_path"]).write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="integrity"):
        references(library, True)


def test_evaluation_excludes_exact_self_and_keeps_synthetic_results_separate(library):
    encoder = FakeEncoder()
    build_index(library, encoder, True)
    query = library / "query.png"
    query.write_bytes(image("red"))
    result = evaluate(
        library,
        encoder,
        [
            {
                "id": "synthetic-self-exclusion",
                "query_kind": "synthetic_perturbation",
                "query_path": "query.png",
                "positive_items": ["synthetic-0"],
                "negative_items": ["synthetic-1", "synthetic-2"],
                "label_evidence": "Synthetic labels",
            }
        ],
    )
    assert "synthetic-0" not in result["cases"][0]["returned_item_ids"]
    assert result["independent_query_count"] == 0
    assert result["deployment_precision_established"] is False
    assert result["training_approved"] is False


@pytest.mark.parametrize("vector", [[float("nan")], [float("inf")], [0, 0], []])
def test_invalid_embeddings_fail(vector):
    with pytest.raises(ValueError):
        unit(vector)


def test_report_cannot_overwrite_private_database(library):
    result = {"matches": []}
    path = library / "references.db"
    before = path.read_bytes()
    with pytest.raises(ValueError, match="refusing overwrite"):
        render_report(library, path, image("red"), result)
    assert path.read_bytes() == before


def test_index_symlink_cannot_escape_private_root(library, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (library / "similarity").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="escaped"):
        build_index(library, FakeEncoder(), True)
    assert not list(outside.iterdir())


def test_missing_or_untrusted_weights_never_trigger_automatic_download(tmp_path, monkeypatch):
    import urllib.request

    from radar.references.similarity import Encoder

    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: pytest.fail("Network forbidden"))
    with pytest.raises(ValueError, match="Pinned local weights"):
        Encoder(tmp_path / "absent.pth")
    bad = tmp_path / "untrusted.pth"
    bad.write_bytes(b"not trusted weights")
    with pytest.raises(ValueError, match="Pinned local weights"):
        Encoder(bad)
