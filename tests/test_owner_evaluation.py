"""Synthetic fixtures only. No real owner photo, API call, model download or training."""

import json

import pytest
from PIL import Image

from radar.references.holdout import read_holdouts
from radar.references.ingest import save_asset
from radar.references.owner_evaluation import admit, controls, freeze, load_case, render, score
from radar.references.recognition import normalize_text, rank, summarize
from radar.references.schema import PhotoRef
from radar.references.similarity import build_index, hash_bytes, references
from radar.references.store import connect, get_source
from tests.test_reference_similarity import FakeEncoder, image

pytest_plugins = ["tests.test_reference_similarity"]


def entries():
    rows = []
    for n, (maker, text) in enumerate(
        [
            ("Bell Trading Post", "COPPER BELL"),
            ("Atkinson", "AT"),
            ("Maisels", "MAISEL’S INDIAN TRADING POST"),
            ("Running Bear", "R.B. STERLING"),
        ]
    ):
        rows.append(
            {
                "reference": {
                    "reference_id": str(n),
                    "asset_id": str(n),
                    "item_id": str(n),
                    "image_sha256": str(n),
                    "candidate_makers": [maker + " (gallery attribution)"],
                    "transcription": text,
                    "legibility": "LEGIBLE",
                },
                "vector": [1, n * 0.1, 0.01],
            }
        )
    return rows


@pytest.mark.parametrize("mode", ["visual", "text", "hybrid"])
@pytest.mark.parametrize("text", ["STERLING", "925", "STERLING 925 TAXCO", "R.B.", "AT"])
def test_generic_or_ambiguous_text_abstains_even_with_high_similarity(mode, text):
    result = rank(entries(), [1, 0, 0.01], text, "LEGIBLE", mode)
    assert result["decision"] == "UNKNOWN"
    assert result["authenticated"] is False


def test_legible_text_ranks_actual_inscription_not_candidate_metadata():
    rows = entries()
    assert normalize_text("maisel’s") == "MAISELS"
    result = rank(rows, [0.1, 0.9, 0.01], "bell sterling", "LEGIBLE", "text")
    assert result["matches"][0]["item_id"] == "0"
    assert result["decision"] == "CANDIDATE"
    # Maker names in source metadata are not transcriptions or inferred OCR.
    result = rank(rows, [1, 0, 0.01], "Running Bear", "LEGIBLE", "text")
    assert result["decision"] == "UNKNOWN"
    assert rank(rows, [1, 0, 0.01], "BELL", "PARTIAL", "text")["matches"] == []
    assert (
        rank(rows, [1, 0, 0.01], "BELL", "LEGIBLE", "hybrid", quality_flags=["LOW_RES_MARK"])[
            "decision"
        ]
        == "UNKNOWN"
    )


def test_same_legible_word_on_conflicting_families_abstains():
    rows = entries()
    rows[1]["reference"]["transcription"] = "BELL"
    result = rank(rows, [1, 0, 0.01], "BELL", "LEGIBLE", "text")
    assert result["reason"] == "TEXT_MATCHES_MULTIPLE_ATTRIBUTED_FAMILIES"
    assert result["decision"] == "UNKNOWN"


def test_owner_intake_is_separate_immutable_and_hash_quarantined(library, tmp_path):
    evaluation = tmp_path / "evaluation"
    photo = tmp_path / "synthetic-owner.png"
    photo.write_bytes(image("purple"))
    original = photo.read_bytes()
    result = admit(
        library,
        evaluation,
        photo,
        "object-01",
        "SYNTHETIC independent capture",
        "LEGIBLE",
        "STERLING",
        (0, 0, 80, 80),
    )
    folder = evaluation / "cases" / result["case_id"]
    case = load_case(folder)
    assert case.origin == "OWNER_DECLARED_INDEPENDENT"
    assert photo.read_bytes() == original
    assert case.full_sha256 in read_holdouts(library)["hashes"]
    assert not list(library.glob("**/full.png"))
    with connect(library) as c:
        source = get_source(c, "synthetic")
        before = c.execute("SELECT count(*) FROM assets").fetchone()[0]
        with pytest.raises(ValueError, match="leakage"):
            save_asset(
                c,
                library,
                source,
                "synthetic-0",
                PhotoRef(
                    url="https://example.test/illegal.png",
                    license="UNLICENSED_INTERNAL_FAIR_USE",
                    license_url=source.terms_url,
                    evidence="Synthetic leak attempt",
                ),
                original,
            )
        assert c.execute("SELECT count(*) FROM assets").fetchone()[0] == before
    with pytest.raises(ValueError, match="Duplicate"):
        admit(library, evaluation, photo, "object-01", "Duplicate synthetic capture")
    corrected = admit(
        library,
        evaluation,
        photo,
        "object-01",
        "Synthetic transcript correction",
        "LEGIBLE",
        "BELL",
        (0, 0, 80, 80),
        supersedes=case.case_id,
    )
    assert corrected["case_id"] != case.case_id
    assert load_case(folder).transcription == "STERLING"
    with pytest.raises(ValueError, match="partitions"):
        other = tmp_path / "other.png"
        other.write_bytes(image("orange"))
        admit(
            library, evaluation, other, "object-01", "Synthetic split leak", partition="CALIBRATION"
        )


def test_reference_pixels_cannot_be_claimed_as_independent(library, tmp_path):
    ref = references(library, True)[0]
    external = tmp_path / "renamed-reference.png"
    external.write_bytes((library / ref["path"]).read_bytes())
    with pytest.raises(ValueError, match="Reference-derived"):
        admit(library, tmp_path / "evaluation", external, "fake-independent", "Synthetic")
    with pytest.raises(ValueError, match="separate"):
        photo = tmp_path / "purple.png"
        photo.write_bytes(image("purple"))
        admit(library, library / "test-images", photo, "synthetic", "Synthetic")


def test_manually_injected_holdout_is_blocked_by_index_and_export(library, tmp_path):
    from radar.references.library import export_dataset

    photo = tmp_path / "private.png"
    photo.write_bytes(image("purple"))
    result = admit(library, tmp_path / "evaluation", photo, "synthetic", "Synthetic")
    full = tmp_path / "evaluation" / "cases" / result["case_id"] / "full.png"
    raw = full.read_bytes()
    (library / "illegal-copy.png").write_bytes(raw)
    with connect(library) as c:
        asset = c.execute("SELECT * FROM assets LIMIT 1").fetchone()
        data = json.loads(asset["data"])
        data.update(normalized_path="illegal-copy.png", original_path="illegal-copy.png")
        c.execute(
            "UPDATE assets SET normalized_hash=?,original_hash=?,data=? WHERE id=?",
            (hash_bytes(raw), hash_bytes(raw), json.dumps(data), asset["id"]),
        )
        c.commit()
    with pytest.raises(ValueError, match="leakage"):
        build_index(library, FakeEncoder(), True)
    with pytest.raises(ValueError, match="leakage"):
        export_dataset(library, "synthetic-leak", include_fair_use=True)


def test_freezing_is_blind_and_scoring_does_not_modify_predictions(library, tmp_path):
    encoder = FakeEncoder()
    build_index(library, encoder, True)
    target = tmp_path / "evaluation"
    photo = tmp_path / "owner.png"
    photo.write_bytes(image("purple"))
    admitted = admit(
        library,
        target,
        photo,
        "synthetic",
        "Synthetic independent capture",
        "LEGIBLE",
        "STERLING",
        (0, 0, 80, 80),
    )
    run = freeze(library, target, encoder)
    path = target / "runs" / run["run_id"] / "run.json"
    original = path.read_bytes()
    assert json.loads(original)["labels_read_during_ranking"] is False
    labels = target / "labels.json"
    labels.write_text(
        json.dumps(
            [
                {
                    "case_id": admitted["case_id"],
                    "target": "UNKNOWN",
                    "expected_family": None,
                    "evidence": "Synthetic unknown",
                }
            ]
        )
    )
    report = score(path, run["run_sha256"], labels)
    assert all(r["unknown_abstention"] == 1 for r in report["strata"])
    assert report["training_approved"] is False
    labels.write_text(
        json.dumps(
            [
                {
                    "case_id": admitted["case_id"],
                    "target": "KNOWN",
                    "expected_family": "Fixture maker 0",
                    "evidence": "Synthetic",
                }
            ]
        )
    )
    score(path, run["run_sha256"], labels)
    assert path.read_bytes() == original
    with pytest.raises(ValueError, match="digest"):
        score(path, "0" * 64, labels)
    output = target / "report.html"
    render(library, target, path, run["run_sha256"], admitted["case_id"], output)
    assert "UNKNOWN" in output.read_text()
    assert "default-src 'none'" in output.read_text()
    with pytest.raises(ValueError, match="New private"):
        render(library, target, path, run["run_sha256"], admitted["case_id"], output)


def test_controls_are_never_counted_as_owner_photos(library, tmp_path):
    build_index(library, FakeEncoder(), True)
    result = controls(library, tmp_path / "controls")
    assert result["owner_photos"] == 0
    run = freeze(library, tmp_path / "controls", FakeEncoder())
    path = tmp_path / "controls" / "runs" / run["run_id"] / "run.json"
    report = score(path, run["run_sha256"], result["labels_file"])
    assert report["owner_declared_independent_cases"] == 0
    assert report["best_on_independent_photos"] is None
    assert report["independent_improvement_established"] is False


def test_uncertain_transcript_cannot_be_promoted_by_intake(library, tmp_path):
    photo = tmp_path / "blurred.png"
    photo.write_bytes(image("purple"))
    with pytest.raises(ValueError, match="reliably legible"):
        admit(
            library,
            tmp_path / "eval",
            photo,
            "synthetic",
            "Synthetic",
            "UNREADABLE",
            "LORENZO YAZZIE",
        )


def test_crop_isolation_preserves_small_mark_quality_failure():
    import io

    from radar.references.owner_evaluation import isolated

    pixels, flags = isolated(image("purple"), (0, 0, 20, 20))
    with Image.open(io.BytesIO(pixels)) as im:
        assert im.size == (64, 64)
    assert flags == ["LOW_RES_MARK"]


def test_always_unknown_does_not_hide_zero_known_coverage():
    result = rank(entries(), [1, 0, 0.01], "BELL", "LEGIBLE", "visual")
    cases = [
        {
            "case_id": "synthetic",
            "origin": "OWNER_DECLARED_INDEPENDENT",
            "partition": "EVALUATION",
            "capture_group": "one-object",
            "rankings": {"mark": {"visual": result}},
        }
    ]
    report = summarize(
        cases,
        [
            {
                "case_id": "synthetic",
                "target": "KNOWN",
                "expected_family": "Bell Trading Post",
                "evidence": "Synthetic",
            }
        ],
    )
    assert report["strata"][0]["known_candidate_coverage"] == 0
    assert report["strata"][0]["accepted_queries"] == 0


def test_report_rejects_traversal_before_reading_a_run(library, tmp_path):
    with pytest.raises(ValueError, match="Invalid case identity"):
        render(
            library,
            tmp_path,
            tmp_path / "missing.json",
            "unused",
            "../../outside",
            tmp_path / "x.html",
        )


def test_case_metadata_symlink_cannot_escape_its_private_folder(tmp_path):
    folder = tmp_path / ("a" * 32)
    folder.mkdir()
    outside = tmp_path / "outside.json"
    outside.write_text("{}")
    (folder / "case.json").symlink_to(outside)
    with pytest.raises(ValueError, match="escaped"):
        load_case(folder)
