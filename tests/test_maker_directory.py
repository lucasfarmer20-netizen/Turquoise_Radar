"""Offline synthetic directory provenance, access, checkpoint and evidence regressions."""

import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from radar.references.directory import (
    collect,
    connect,
    enqueue,
    hybrid,
    initialize,
    put_review,
    revise,
    search,
    stats,
    upsert,
)
from radar.references.directory_adapters import art_amerindien
from radar.references.network import StopCollection
from tests.test_reference_similarity import library  # noqa: F401


@pytest.fixture
def directory(tmp_path, library):  # noqa: F811
    root = tmp_path / "directory"
    initialize(root, library)
    review = {
        "source": "art-amerindien",
        "hosts": ["example.test"],
        "paths": ["/hallmarks/"],
        "terms_urls": ["https://example.test/terms"],
        "evidence": "Synthetic fixture, not a real source review",
        "rationale": "Synthetic factual test only",
        "facts_allowed": True,
        "image_policy": "EXTERNAL_LINK_ONLY",
        "reviewed_at": datetime.now(UTC).isoformat(),
        "independence_group": "synthetic",
        "record_limit": 10,
    }
    rid = put_review(root, review)
    return root, review, rid


def cell(name="Synthetic Alpha", src="alpha.png", alt="ALPHA hallmark"):
    return (
        f'<table><tr><td><img src="native_american_marks/{src}" '
        f'alt="{alt}"><br>{name}<br>Navajo</td></tr></table>'
    ).encode()


def insert(directory, name="Synthetic Alpha", alt="ALPHA hallmark", src="alpha.png"):
    root, _, rid = directory
    r = art_amerindien(cell(name, src, alt), "https://example.test/hallmarks/a.htm")[0]
    with connect(root, write=True) as c:
        key = upsert(c, r, rid, "art-amerindien")
    return key


def test_init_requires_complete_existing_library(tmp_path):
    with pytest.raises(ValueError, match="Existing private JRI"):
        initialize(tmp_path / "directory", tmp_path / "absent")
    assert not (tmp_path / "absent" / "references.db").exists()


def test_no_replacement(directory, library):  # noqa: F811
    with pytest.raises(ValueError, match="replace"):
        initialize(directory[0], library)


def test_nested_cells_and_multiple_photos_are_one_variant():
    raw = b"""<table><td><table><td><img src="native_american_marks/a.png">
    <img src="native_american_marks/b.png"><br>Synthetic Maker<br>Hopi 1</td>
    <td><img src="decorative.png"><br>Not a reference</td></table></td></table>"""
    rows = art_amerindien(raw, "https://example.test/hallmarks/a.htm")
    assert len(rows) == 1 and len(rows[0]["image_urls"]) == 2
    assert rows[0]["maker_name"] == "Synthetic Maker"
    assert rows[0]["transcription"] is None


def test_source_claims_versions_and_fts(directory):
    key = insert(directory)
    insert(directory)
    with connect(directory[0]) as c:
        assert c.execute("SELECT count(*) FROM records").fetchone()[0] == 1
        assert c.execute("SELECT count(*) FROM observations").fetchone()[0] == 1
    revise(
        directory[0],
        key,
        {
            "aliases": ["Synthetic Alias"],
            "transcription": "ALPHA",
            "transcription_status": "REPORTED",
            "identity_kind": "PERSON",
        },
        "Synthetic caption",
    )
    assert search(directory[0], "Alias")[0]["record_id"] == key
    row = search(directory[0], "ALPHA")[0]
    assert row["attribution"] == "SOURCE_ATTRIBUTED"
    assert not row["training_eligible"] and not row["independently_authenticated"]
    with connect(directory[0]) as c:
        assert c.execute("SELECT count(*) FROM observations").fetchone()[0] == 2


def test_same_variant_conflicting_captions_are_not_silently_merged(directory):
    insert(directory)
    insert(directory, name="Synthetic Other")
    row = search(directory[0], "Alpha")[0]
    assert row["uncertainty"] == "SOURCE_CONFLICT"
    assert len(row["attributed_names"]) == 2
    assert row["contradictions"]


def test_revocation_and_expiry_hide_reference(directory):
    root, review, _ = directory
    insert(directory)
    put_review(root, dict(review, facts_allowed=False))
    assert not search(root, "Alpha")
    with pytest.raises(ValueError, match="eligible"):
        collect(root, "art-amerindien", live=True)
    put_review(root, dict(review, reviewed_at=(datetime.now(UTC) - timedelta(days=31)).isoformat()))
    with pytest.raises(ValueError, match="expired"):
        search(root, "Alpha")


@pytest.mark.parametrize(
    "url",
    [
        "http://example.test/hallmarks/a.htm",
        "https://evil.test/hallmarks/a.htm",
        "https://example.test/private/a.htm",
        "https://user:secret@example.test/hallmarks/a.htm",
    ],
)
def test_url_boundaries(directory, url):
    with pytest.raises((ValueError, StopCollection)):
        enqueue(directory[0], "art-amerindien", [url])


def test_robots_denial_no_content_or_empty_exhaustion(directory, monkeypatch):
    root = directory[0]
    enqueue(root, "art-amerindien", ["https://example.test/hallmarks/a.htm"])
    monkeypatch.setattr("radar.references.network.PoliteHTTP.delay", lambda *a: None)
    calls = []

    def handler(req):
        calls.append(req.url.path)
        return httpx.Response(200, text="User-agent: *\nDisallow: /hallmarks/")

    result = collect(root, "art-amerindien", live=True, transport=httpx.MockTransport(handler))
    assert result["stop"] == "ROBOTS_DENIED" and calls == ["/robots.txt"]
    assert stats(root)["records"] == 0 and stats(root)["pages"][0]["done"] == 0


def test_resumable_bounded_no_image_requests(directory, monkeypatch):
    root = directory[0]
    enqueue(root, "art-amerindien", ["https://example.test/hallmarks/a.htm"])
    monkeypatch.setattr("radar.references.network.PoliteHTTP.delay", lambda *a: None)
    calls = []
    raw = cell() + cell("Synthetic Beta", "beta.png", "BETA hallmark")

    def handler(req):
        calls.append(req.url.path)
        return (
            httpx.Response(404)
            if req.url.path == "/robots.txt"
            else httpx.Response(200, content=raw)
        )

    transport = httpx.MockTransport(handler)
    assert (
        collect(root, "art-amerindien", live=True, max_records=1, transport=transport)[
            "records_processed"
        ]
        == 1
    )
    assert stats(root)["pages"][0]["offset"] == 1
    collect(root, "art-amerindien", live=True, max_records=1, transport=transport)
    assert stats(root)["records"] == 2 and stats(root)["pages"][0]["done"] == 1
    assert all(p.endswith(".htm") or p == "/robots.txt" for p in calls)
    # Completed checkpoints cause zero more HTTP calls on resume.
    previous = len(calls)
    collect(root, "art-amerindien", live=True, transport=transport)
    assert len(calls) == previous


def test_page_change_keeps_history_and_corrections(directory, monkeypatch):
    root = directory[0]
    enqueue(root, "art-amerindien", ["https://example.test/hallmarks/a.htm"])
    monkeypatch.setattr("radar.references.network.PoliteHTTP.delay", lambda *a: None)

    def run(raw, refresh=False):
        return collect(
            root,
            "art-amerindien",
            live=True,
            refresh=refresh,
            transport=httpx.MockTransport(
                lambda r: httpx.Response(404)
                if r.url.path == "/robots.txt"
                else httpx.Response(200, content=raw)
            ),
        )

    run(cell())
    key = search(root, "Alpha")[0]["record_id"]
    revise(root, key, {"transcription": "ALPHA", "transcription_status": "REPORTED"}, "Synthetic")
    run(cell(alt="Changed synthetic description"), True)
    with connect(root) as c:
        assert c.execute("SELECT count(*) FROM page_versions").fetchone()[0] == 2
    assert search(root, "Alpha")[0]["transcription"] == "ALPHA"


@pytest.mark.parametrize("text", ["STERLING", "925", "14K", "STERLING K", "950 SILVER"])
def test_generic_or_single_initial_never_identifies(directory, text):
    insert(directory, alt="STERLING ALPHA")
    assert hybrid(directory[0], text, legibility="LEGIBLE")["decision"] == "UNKNOWN"


def test_source_reported_literal_can_support_candidate_not_authentication(directory):
    key = insert(directory)
    revise(
        directory[0],
        key,
        {"transcription": "ALPHA", "transcription_status": "REPORTED"},
        "Synthetic",
    )
    assert hybrid(directory[0], "ALPHA", legibility="LEGIBLE")["decision"] == "CANDIDATE"
    assert hybrid(directory[0], "ALPHA", legibility="PARTIAL")["decision"] == "UNKNOWN"
    # Names in descriptive metadata alone do not become literal marks.
    assert hybrid(directory[0], "SYNTHETIC ALPHA", legibility="LEGIBLE")["decision"] == "UNKNOWN"


def test_letter_conflicts_downgraded_partial_not_disqualified(directory):
    insert(directory)
    wrong = {
        "reference_id": "wrong",
        "transcription": "MAISELS STERLING",
        "legibility": "LEGIBLE",
        "similarity": 0.84,
    }
    result = hybrid(directory[0], "T HENDERSON", legibility="LEGIBLE", visual_matches=[wrong])
    assert result["visual_candidates"][0]["text_consistency"] == "CONTRADICTORY"
    assert result["visual_candidates"][0]["adjusted_similarity"] < 0
    assert result["decision"] == "UNKNOWN"
    partial = hybrid(directory[0], "T HEND…", legibility="PARTIAL", visual_matches=[wrong])
    assert partial["visual_candidates"][0]["text_consistency"] == "UNRESOLVED"


def test_no_network_without_explicit_live(directory):
    with pytest.raises(ValueError, match="Explicit live"):
        collect(directory[0], "art-amerindien")


def test_review_does_not_authorize_image_copying(directory):
    with pytest.raises(ValueError, match="external-only"):
        put_review(directory[0], dict(directory[1], image_policy="COPY_ALL"))


def test_two_makers_same_literal_remain_unknown(directory):
    first = insert(directory)
    second = insert(directory, name="Synthetic Other", src="other.png")
    for key in [first, second]:
        revise(
            directory[0],
            key,
            {"transcription": "ALPHA", "transcription_status": "REPORTED"},
            "Synthetic",
        )
    assert hybrid(directory[0], "ALPHA", legibility="LEGIBLE")["decision"] == "UNKNOWN"


def test_every_ingestion_path_obeys_source_cap(directory):
    root, review, _ = directory
    put_review(root, dict(review, record_limit=1))
    with connect(root) as c:
        rid = c.execute("SELECT review_id FROM current_reviews").fetchone()[0]
    insert((root, review, rid))
    with pytest.raises(ValueError, match="record limit"):
        insert((root, review, rid), name="Synthetic Beta", src="beta.png")
    assert stats(root)["records"] == 1


def test_quality_exclusion_survives_refresh(directory):
    key = insert(directory)
    with connect(directory[0], write=True) as c:
        c.execute("INSERT INTO events(event,subject) VALUES ('QUALITY_EXCLUDED',?)", (key,))
    insert(directory)
    assert not search(directory[0], "Alpha")


def test_handoff_roundtrip_and_no_overwrite(directory, library, tmp_path):  # noqa: F811
    import hashlib

    from radar.references.handoff import backup, restore
    from tests.test_reference_similarity import image

    insert(directory)
    evaluation = tmp_path / "evaluation"
    (evaluation / "package/images").mkdir(parents=True)
    raw = image("red")
    (evaluation / "package/images/synthetic.png").write_bytes(raw)
    (evaluation / "package/manifest.json").write_text(
        json.dumps(
            {
                "image_count": 1,
                "items": [
                    {
                        "file": "images/synthetic.png",
                        "sha256": hashlib.sha256(raw).hexdigest(),
                        "bytes": len(raw),
                    }
                ],
            }
        )
    )
    target = tmp_path / "handoff"
    receipt = backup(target, library, directory[0], evaluation)
    restored = tmp_path / "restored"
    result = restore(target, restored, receipt["manifest_sha256"])
    assert result["checks"]["evaluation_originals"] == 1
    assert result["checks"]["directory"]["records"] == 1
    assert (restored / "evaluation/package/images/synthetic.png").read_bytes() == raw
    with pytest.raises(ValueError, match="overwrite"):
        restore(target, restored, receipt["manifest_sha256"])
    volume = target / "part-001.zip"
    volume.write_bytes(volume.read_bytes()[:-1] + b"x")
    with pytest.raises(ValueError, match="digest"):
        restore(target, tmp_path / "corrupt", receipt["manifest_sha256"])
    assert not (tmp_path / "corrupt").exists()


def test_frozen_comparison_reproduces_baseline_and_escapes_text(
    directory,
    library,  # noqa: F811
    tmp_path,
    monkeypatch,  # noqa: F811
):  # noqa: F811, E501
    import hashlib

    from radar.references.directory_evaluation import evaluate
    from radar.references.similarity import build_index
    from radar.references.similarity import search as visual_search
    from tests.test_reference_similarity import FakeEncoder, image

    root = directory[0]
    key = insert(directory)
    revise(root, key, {"transcription": "ALPHA", "transcription_status": "REPORTED"}, "Synthetic")
    encoder = FakeEncoder()
    build_index(library, encoder, include_fair_use=True)
    baseline = tmp_path / "baseline"
    (baseline / "normalized").mkdir(parents=True)
    (baseline / "crops").mkdir()
    raw = image("orange")
    for folder in ["normalized", "crops"]:
        (baseline / folder / "Q01.png").write_bytes(raw)
    old = visual_search(library, encoder, raw)
    (baseline / "reviewed-results.json").write_text(
        json.dumps(
            {
                "index_sha256": hashlib.sha256(
                    (library / "similarity/index.json").read_bytes()
                ).hexdigest(),
                "rows": [
                    {
                        "query_id": "Q01",
                        "query_sha256": hashlib.sha256(raw).hexdigest(),
                        "crop_sha256": hashlib.sha256(raw).hexdigest(),
                        "full": old,
                    }
                ],
            }
        )
    )
    annotations = tmp_path / "annotations.json"
    annotations.write_text(
        json.dumps(
            {
                "Q01": {
                    "transcription": "ALPHA",
                    "legibility": "LEGIBLE",
                    "evidence": "Synthetic <script>bad()</script>",
                }
            }
        )
    )
    monkeypatch.setattr("radar.references.directory_evaluation.Encoder", lambda _: encoder)
    output = tmp_path / "report"
    result = evaluate(root, library, baseline, annotations, output, "unused-synthetic-model")
    assert result["queries"] == 1 and result["candidate_recommendations"] == 1
    text = (output / "comparison.html").read_text()
    assert "<script>bad()" not in text and "&lt;script&gt;bad()" in text
    assert "default-src 'none'" in text
    with pytest.raises(ValueError, match="New private output"):
        evaluate(root, library, baseline, annotations, output, "unused-synthetic-model")
    (baseline / "crops/Q01.png").write_bytes(image("blue"))
    with pytest.raises(ValueError, match="Frozen query/crop changed"):
        evaluate(
            root, library, baseline, annotations, tmp_path / "tampered", "unused-synthetic-model"
        )


def test_partial_search_and_documented_relationships(directory):
    from radar.references.directory import relate

    first = insert(directory)
    second = insert(directory, name="Synthetic Beta", src="beta.png", alt="BETA mark")
    assert search(directory[0], "Alp", partial=True)[0]["record_id"] == first
    assert not search(directory[0], "Alp")
    relate(directory[0], first, second, "DIFFICULT_NEGATIVE", "Synthetic lookalike evidence")
    assert search(directory[0], "Alpha")[0]["relationships"][0]["authenticated"] is False
    with pytest.raises(ValueError, match="independent corroboration"):
        relate(
            directory[0],
            first,
            second,
            "SOURCE_AGREEMENT",
            "Synthetic",
            independently_assessed=True,
        )


def test_reuses_existing_library_with_explicit_rights_opt_in(directory, library):  # noqa: F811
    from radar.references.directory_library import import_library

    assert import_library(directory[0], library)["reused_existing_records"] == 0
    assert (
        import_library(directory[0], library, include_fair_use=True)["reused_existing_records"] == 3
    )
    assert stats(directory[0])["downloaded_images"] == 0
    matches = search(directory[0], "SYNTHETIC MAKER")
    assert len(matches) == 3 and all(r["local_reference_ids"] for r in matches)
    assert (
        import_library(directory[0], library, include_fair_use=True)["reused_existing_records"] == 3
    )
    assert stats(directory[0])["records"] == 3


def test_review_lists_and_encoded_scope_boundary(directory):
    from radar.references.directory import approved_url

    root, review, _ = directory
    for field, value in [("hosts", "example.test"), ("paths", "/hallmarks/")]:
        with pytest.raises(ValueError, match="list"):
            put_review(root, dict(review, **{field: value}))
    with pytest.raises(ValueError, match="Noncanonical"):
        approved_url("https://example.test/hallmarks/%2e%2e/private", review)
