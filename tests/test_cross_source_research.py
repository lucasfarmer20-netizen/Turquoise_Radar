"""Synthetic evidence only: no live access, real maker facts or private payloads."""

import json
from datetime import UTC, datetime

import pytest

from radar.references.directory import connect, digest, put_review, upsert
from radar.references.research import features, propose, report, review, verify
from radar.references.research_facts import import_facts
from radar.references.store import dump
from tests.test_maker_directory import directory  # noqa: F401
from tests.test_reference_similarity import library  # noqa: F401


def add(directory, source, name="Synthetic Alpha", **patch):  # noqa: F811
    root, original, _ = directory
    policy = dict(original, source=source, independence_group=source)
    rid = put_review(root, policy)
    r = dict(
        maker_name=name,
        aliases=[],
        identity_kind="UNRESOLVED",
        locator=f"https://example.test/hallmarks/{source}-{name}",
        source_url="https://example.test/hallmarks/a.htm",
        image_urls=[],
        transcription="ALPHA",
        transcription_status="REPORTED",
        description="Synthetic spiral above ALPHA",
        source_caption=name,
        attribution_provenance=f"Synthetic original workshop register {source}",
        uncertainty="SOURCE_ATTRIBUTED",
        **patch,
    )
    with connect(root, write=True) as c:
        return upsert(c, r, rid, source)


def citation(key, field="maker_name", quote="Synthetic Alpha"):
    return {"record": key, "field": field, "quote": quote}


def assessment(a, b, **patch):
    return dict(
        left=a,
        right=b,
        identity="SAME_REPORTED",
        mark="UNASSESSED",
        independence="UNESTABLISHED",
        support=[citation(a), citation(b)],
        conflicting_evidence=[],
        uncertainty="Attribution not authenticated",
        rationale="Synthetic research review",
        reviewer="Synthetic test reviewer",
        reviewed_at=datetime.now(UTC).isoformat(),
        **patch,
    )


def entry(root, a, b, **patch):
    e = assessment(a, b)
    e.update(patch)
    path = review(root, [e])
    return path, json.loads(path.read_text())["relationships"][0]


def marks(a, b):
    return [citation(i, "transcription", "ALPHA") for i in [a, b]]


def test_names_and_different_domains_never_establish_independence(directory):  # noqa: F811
    a, b = add(directory, "one"), add(directory, "two")
    root = directory[0]
    path, r = entry(root, a, b)
    assert r["decision"] == "UNKNOWN" and not r["authenticated"]
    assert propose(root)[0]["decision"] == "UNKNOWN"
    assert verify(root)["decisions"] == {"UNKNOWN": 1}
    assert path == review(root, [r["assessment"]])  # Idempotent.
    entry(root, a, b, mark="COMPATIBLE", mark_evidence=marks(a, b))
    assert verify(root)["decisions"]["SIMILAR_UNVERIFIED"] == 1


def test_independence_requires_both_attribution_histories(directory):  # noqa: F811
    a, b = add(directory, "one"), add(directory, "two")
    args = dict(mark="COMPATIBLE", mark_evidence=marks(a, b), independence="DOCUMENTED_INDEPENDENT")
    with pytest.raises(ValueError, match="Cited evidence"):
        entry(directory[0], a, b, **args)
    provenance = [
        citation(a, "attribution_provenance", "Synthetic original workshop register one"),
        citation(b, "attribution_provenance", "Synthetic original workshop register two"),
    ]
    _, r = entry(directory[0], a, b, **args, independent_provenance=provenance)
    assert r["decision"] == "INDEPENDENT_SOURCE_AGREEMENT" and not r["authenticated"]


def test_library_and_web_repetition_are_same_family(directory):  # noqa: F811
    a = add(directory, "art-amerindien")
    b = add(directory, "library-art-amerindien")
    assert entry(directory[0], a, b)[1]["decision"] == "SAME_SOURCE_REPETITION"
    with pytest.raises(ValueError, match="Independent attribution"):
        entry(
            directory[0],
            a,
            b,
            mark="COMPATIBLE",
            mark_evidence=marks(a, b),
            independence="DOCUMENTED_INDEPENDENT",
            independent_provenance=[
                citation(a, "attribution_provenance", "Synthetic"),
                citation(b, "attribution_provenance", "Synthetic"),
            ],
        )


def test_collisions_are_not_conflicts_without_same_stamp_evidence(directory):  # noqa: F811
    a = add(directory, "one")
    b = add(directory, "two", "Synthetic Other")
    patch = dict(
        identity="DIFFERENT_REPORTED",
        mark="SIMILAR",
        mark_evidence=marks(a, b),
        support=[citation(a), citation(b, quote="Synthetic Other")],
    )
    assert entry(directory[0], a, b, **patch)[1]["decision"] == "SIMILAR_UNVERIFIED"
    patch["mark"] = "SAME_STAMP"
    with pytest.raises(ValueError, match="Conflicting attributions"):
        entry(directory[0], a, b, **patch)
    assert (
        entry(directory[0], a, b, **patch, conflicting_evidence=patch["support"])[1]["decision"]
        == "CONFLICTING_ATTRIBUTIONS"
    )


def test_shared_stamp_remains_ambiguous(directory):  # noqa: F811
    a = add(directory, "one", shared_stamp_claims=["Synthetic shared stamp with partner"])
    b = add(directory, "two")
    _, r = entry(
        directory[0],
        a,
        b,
        shared_evidence=[citation(a, "shared_stamp_claims", "Synthetic shared stamp with partner")],
    )
    assert r["decision"] == "SHARED_REUSED_MARK"


def test_no_generic_purity_match_or_surname_merger():
    a = dict(
        maker_name="Synthetic Person Sr",
        transcription="STERLING 925",
        transcription_status="REPORTED",
        aliases=[],
    )
    b = dict(a, maker_name="Synthetic Person Jr")
    assert not features(a, b)
    b["aliases"] = [a["maker_name"]]
    assert features(a, b) == ["NAME_OR_EXPLICIT_ALIAS"]
    a["shop_identity"] = b["shop_identity"] = "Synthetic Shop"
    a["period"] = b["period"] = "1900–1910, source claim"
    assert features(a, b)[-2:] == ["REPORTED_SHOP", "REPORTED_PERIOD"]


def test_unknown_evidence_tamper_limit_and_private_report(directory, tmp_path):  # noqa: F811
    a, b = add(directory, "one"), add(directory, "two")
    e = assessment(a, b)
    e["support"][0]["quote"] = "invented quotation"
    with pytest.raises(ValueError, match="quote absent"):
        review(directory[0], [e])
    with pytest.raises(ValueError, match="25"):
        review(directory[0], [assessment(a, b)] * 26)
    path, _ = entry(directory[0], a, b)
    out = report(directory[0], path, tmp_path / "report.html")
    text = out.read_text()
    assert "UNKNOWN" in text and "Content-Security-Policy" in text
    assert "<img" not in text and "<script" not in text
    put_review(directory[0], dict(directory[1], source="one", facts_allowed=False))
    report(directory[0], path, out)
    assert "Source review expired, revoked" in out.read_text()
    with connect(directory[0], write=True) as c:
        c.execute("DELETE FROM observations WHERE record_id=?", (a,))
    with pytest.raises(ValueError, match="observation missing"):
        verify(directory[0])


def facts_packet(directory, tmp_path):  # noqa: F811
    root, original, _ = directory
    url = "https://example.test/hallmarks/selected"
    rid = put_review(root, dict(original, source="synthetic-facts", selected_urls=[url]))
    raw = b"Synthetic ZETA mark attributed to Synthetic Maker; not confirmed."
    packet = tmp_path / "packet"
    packet.mkdir()
    (packet / "page.txt").write_bytes(raw)
    data = dict(
        schema="jri-reviewed-facts-1",
        source="synthetic-facts",
        review_id=rid,
        files={"page.txt": digest(raw)},
        entries=[
            dict(
                text_file="page.txt",
                quotes=["ZETA mark attributed to Synthetic Maker"],
                review_note="Synthetic source claim only",
                observed_at=datetime.now(UTC).isoformat(),
                record=dict(
                    maker_name="Synthetic Maker",
                    identity_kind="UNRESOLVED",
                    transcription="ZETA",
                    transcription_status="REPORTED",
                    locator=url,
                    source_url=url,
                    image_urls=[],
                    uncertainty="UNCONFIRMED",
                ),
            )
        ],
    )
    (packet / "facts.json").write_text(dump(data))
    return packet, digest((packet / "facts.json").read_bytes()), data


def test_facts_idempotent_no_images_preserves_observation_and_verifies(directory, tmp_path):  # noqa: F811
    packet, sha, _ = facts_packet(directory, tmp_path)
    first = import_facts(directory[0], packet, sha)
    assert first == import_facts(directory[0], packet, sha)
    assert first["images_downloaded"] == 0
    assert verify(directory[0])["reviewed_factual_records"] == 1
    target = directory[0] / "research" / "facts" / sha / "page.txt"
    target.write_text("corrupted")
    with pytest.raises(ValueError, match="hash mismatch"):
        verify(directory[0])


@pytest.mark.parametrize("failure", ["hash", "quote", "url", "image", "revoked"])
def test_facts_fail_closed(directory, tmp_path, failure):  # noqa: F811
    packet, sha, data = facts_packet(directory, tmp_path)
    if failure == "hash":
        (packet / "page.txt").write_text("wrong")
    if failure == "quote":
        data["entries"][0]["quotes"] = ["invented"]
    if failure == "url":
        data["entries"][0]["record"]["source_url"] = "https://example.test/hallmarks/other"
    if failure == "image":
        data["entries"][0]["record"]["image_urls"] = ["https://example.test/image.jpg"]
    if failure == "revoked":
        put_review(directory[0], dict(directory[1], source="synthetic-facts", facts_allowed=False))
    (packet / "facts.json").write_text(dump(data))
    sha = digest((packet / "facts.json").read_bytes())
    with pytest.raises(ValueError):
        import_facts(directory[0], packet, sha)
    with connect(directory[0]) as c:
        assert c.execute("SELECT count(*) FROM records").fetchone()[0] == 0


def test_legacy_boolean_cannot_bypass_independence_review(directory):  # noqa: F811
    from radar.references.directory import relate

    a, b = add(directory, "one"), add(directory, "two")
    with pytest.raises(ValueError, match="attribution provenance"):
        relate(
            directory[0], a, b, "SOURCE_AGREEMENT", "Different URLs", independently_assessed=True
        )


def test_handoff_research_and_fact_evidence_roundtrip(directory, library, tmp_path):  # noqa: F811
    from radar.references.handoff import backup, restore
    from tests.test_reference_similarity import image

    packet, sha, _ = facts_packet(directory, tmp_path)
    import_facts(directory[0], packet, sha)
    a, b = add(directory, "one"), add(directory, "two")
    path, _ = entry(directory[0], a, b)
    evaluation = tmp_path / "evaluation"
    (evaluation / "package").mkdir(parents=True)
    raw = image("red")
    (evaluation / "package/synthetic.png").write_bytes(raw)
    (evaluation / "package/manifest.json").write_text(
        dump(
            {
                "image_count": 1,
                "items": [{"file": "synthetic.png", "sha256": digest(raw), "bytes": len(raw)}],
            }
        )
    )
    archive = tmp_path / "archive"
    receipt = backup(archive, library, directory[0], evaluation)
    target = tmp_path / "restored"
    result = restore(archive, target, receipt["manifest_sha256"])
    assert result["checks"]["cross_source_research"] == verify(directory[0])
    review_path = target / "directory/research/reviews" / path.name
    report(target / "directory", review_path, target / "report.html")
    assert "UNKNOWN" in (target / "report.html").read_text()
    evidence = target / "directory/research/facts" / sha / "page.txt"
    evidence.unlink()
    with pytest.raises(FileNotFoundError):
        backup(tmp_path / "broken", target / "jri", target / "directory", target / "evaluation")


def test_report_escapes_untrusted_source_text_and_unsafe_links(directory, tmp_path):  # noqa: F811
    from radar.references.research import link

    a = add(directory, "one", "<script>alert(1)</script>")
    b = add(directory, "two")
    path, _ = entry(
        directory[0], a, b, support=[citation(a, quote="<script>alert(1)</script>"), citation(b)]
    )
    output = report(directory[0], path, tmp_path / "safe.html")
    assert "<script>" not in output.read_text()
    assert "&lt;script&gt;" in output.read_text()
    assert link("javascript:alert(1)", "source") == "source"


def test_missing_entire_review_is_detected_from_append_only_event(directory):  # noqa: F811
    a, b = add(directory, "one"), add(directory, "two")
    path, _ = entry(directory[0], a, b)
    path.unlink()
    with pytest.raises(ValueError, match="Research review missing"):
        verify(directory[0])


def test_missing_provenance_cannot_be_quoted_as_null(directory):  # noqa: F811
    a, b = add(directory, "one"), add(directory, "two")
    e = assessment(a, b)
    e["support"] = [citation(a, "nonexistent", "null"), citation(b)]
    with pytest.raises(ValueError, match="field missing"):
        review(directory[0], [e])
