"""Offline synthetic book packets; no real book text, photographs or private records."""

import copy
import csv
import json
from datetime import UTC, datetime, timedelta

import pytest

from radar.references.book_source import PROHIBITED, load_bundle, sha
from radar.references.books import import_bundle, verify
from radar.references.directory import connect, hybrid, put_review, search, source_registry, stats
from radar.references.handoff import backup, restore
from tests.test_maker_directory import directory, insert  # noqa: F401
from tests.test_reference_similarity import image, library  # noqa: F401


def write_csv(path, rows):
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def seal(path, manifest):
    for f in manifest["files"]:
        raw = (path.parent / f["name"]).read_bytes()
        f.update(sha256=sha(raw), bytes=len(raw))
    path.write_text(json.dumps(manifest))
    return sha(path.read_bytes())


@pytest.fixture
def packet(tmp_path, directory):  # noqa: F811
    root = tmp_path / "synthetic-book"
    root.mkdir()
    old = insert(directory)
    files = []
    photos = []
    for i, page in enumerate([7, 7, 8]):
        name = f"synthetic-photo-{i}.png"
        raw = image(["red", "blue", "green"][i])
        (root / name).write_bytes(raw)
        files.append(dict(name=name, role="PHOTO", page=page, printed_number_visible=True))
        photos.append(dict(path=name, filename=name, page=page, sha256=sha(raw)))
    originals, entries, corrected = [], [], []
    specs = [
        ("Synthetic Alpna", "Synthetic Alpha", ["ALPHA", "ALTSTAMP"], "REPORTED_BY_BOOK"),
        ("Synthetic Shared One", "Synthetic Shared One", ["SHARED"], "REPORTED_BY_BOOK"),
        ("Synthetic Shared Two", "Synthetic Shared Two", ["SHARED"], "REPORTED_BY_BOOK"),
        ("Synthetic Twin", "Synthetic Twin", ["FIRST"], "REPORTED_BY_BOOK"),
        ("Synthetic Twin", "Synthetic Twin", ["SECOND"], "REPORTED_BY_BOOK"),
        ("Synthetic Unknown", "Synthetic Unknown", [], "UNKNOWN_IN_SOURCE"),
        ("Synthetic Pictorial", "Synthetic Pictorial", [], "PICTORIAL_DESCRIPTION_ONLY"),
    ]
    for i, (original, reviewed, variants, status) in enumerate(specs):
        page = 7 if i < 3 else 8
        eid = f"SYNTHETIC-P{page}-{i}"
        oldrow = dict(
            entry_id=eid,
            page=str(page),
            maker_or_shop_source_name=original,
            mark_transcription_source_reported="; ".join(variants),
            mark_or_identity_notes="Synthetic provisional observation",
        )
        originals.append(oldrow)
        e = dict(
            entry_id=eid,
            printed_page=page,
            original_csv=oldrow,
            reviewed_source_name=reviewed,
            source_reported_text_variants=variants,
            review_note="Synthetic corrected reading; earliest, not ear-form.",
            review_status="DISCREPANCY" if i == 0 else "CONFIRMED_AS_SUMMARY",
            illustrated_mark_confirmed=bool(variants),
            mark_claim_status=status,
            evidence_photos=[p for p in photos if p["page"] == page],
            printed_page_number_visible=True,
            independently_authenticated=False,
            directory_matches=[{"record_id": old}] if i == 0 else [],
            directory_crosswalk_status="EXACT_NAME_OVERLAP" if i == 0 else "UNMATCHED",
            crosswalk_conclusion="Synthetic research overlap, not authentication.",
            **{k + "_eligible": False for k in PROHIBITED},
        )
        entries.append(e)
        corrected.append(
            dict(
                entry_id=eid,
                reviewed_source_name=reviewed,
                reviewed_reported_variants=json.dumps(variants),
                review_note=e["review_note"],
                review_status=e["review_status"],
            )
        )
    write_csv(root / "original.csv", originals)
    write_csv(root / "validation.csv", corrected)
    (root / "validation.json").write_text(json.dumps({"entries": entries}))
    (root / "relationships.json").write_text(
        json.dumps(
            [
                {
                    "type": "SOURCE_REPORTED_SHARED_STAMP",
                    "entries": [entries[1]["entry_id"], entries[2]["entry_id"]],
                    "claim": "Synthetic published shared-stamp attribution.",
                },
                {
                    "type": "HOMONYM_DO_NOT_MERGE",
                    "entries": [entries[3]["entry_id"], entries[4]["entry_id"]],
                    "claim": "Synthetic homonyms; separate source identities.",
                },
            ]
        )
    )
    (root / "collisions.json").write_text(
        json.dumps(
            [
                {
                    "normalized_text": "shared",
                    "entries": [{"entry_id": e["entry_id"]} for e in entries[1:3]],
                }
            ]
        )
    )
    documents = {
        "provisional_csv": "original.csv",
        "validation_json": "validation.json",
        "validation_csv": "validation.csv",
        "relationships": "relationships.json",
        "collisions": "collisions.json",
    }
    files.extend(dict(name=n, role="DOCUMENT") for n in documents.values())
    manifest = dict(
        schema="jri-reviewed-book-1",
        source_kind="MANUAL_BOOK",
        source="book:synthetic-5e",
        book=dict(
            title="Synthetic Reference Book",
            author="Synthetic Author",
            edition="5th",
            bibliographic_status="Synthetic fixture, not a real book",
        ),
        image_policy="PRIVATE_REFERENCE_ONLY",
        facts_allowed=True,
        rights=dict(local_reference_use=True, **dict.fromkeys(PROHIBITED, False)),
        reviewer="Synthetic owner",
        authorization="Synthetic bounded import authorization",
        independence_group="UNESTABLISHED:synthetic-book",
        rationale="Synthetic test only",
        reviewed_at=datetime.now(UTC).isoformat(),
        pages=[7, 8],
        entry_ids=[e["entry_id"] for e in entries],
        files=files,
        documents=documents,
    )
    path = root / "book-review.json"
    fingerprint = seal(path, manifest)
    return path, fingerprint, manifest, old


def run(directory, packet, tmp_path, **kwargs):  # noqa: F811
    return import_bundle(
        directory[0],
        packet[0],
        packet[1],
        apply=True,
        backup_destination=tmp_path / "pre-import",
        **kwargs,
    )


def test_dry_run_read_only_and_no_implicit_database(directory, packet, tmp_path):  # noqa: F811
    before = (directory[0] / "directory.db").read_bytes()
    result = import_bundle(directory[0], packet[0], packet[1])
    assert result["pending"] == 7 and result["pages"] == 2 and result["photos"] == 3
    assert before == (directory[0] / "directory.db").read_bytes()
    assert not (directory[0] / "book_sources").exists()
    with pytest.raises(ValueError, match="Existing private"):
        import_bundle(tmp_path / "absent", packet[0], packet[1], apply=True)
    assert not (tmp_path / "absent").exists()
    with pytest.raises(ValueError, match="backup destination"):
        import_bundle(directory[0], packet[0], packet[1], apply=True)


def test_import_preserves_prior_rows_and_separates_homonyms(directory, packet, tmp_path, library):  # noqa: F811
    before = {p: sha(p.read_bytes()) for p in library.rglob("*") if p.is_file()}
    with connect(directory[0]) as c:
        old = tuple(c.execute("SELECT * FROM records WHERE id=?", (packet[3],)).fetchone())
    assert run(directory, packet, tmp_path)["imported"] == 7
    with connect(directory[0]) as c:
        assert tuple(c.execute("SELECT * FROM records WHERE id=?", (packet[3],)).fetchone()) == old
        assert c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert not c.execute("PRAGMA foreign_key_check").fetchall()
    twins = search(directory[0], "Synthetic Twin")
    assert len(twins) == 2 and len({r["entity_id"] for r in twins}) == 2
    assert {p: sha(p.read_bytes()) for p in before} == before
    assert verify(directory[0])["records"] == 7
    assert (tmp_path / "pre-import/book-backup-receipt.json").is_file()


def test_corrected_text_variants_unknown_and_citations(directory, packet, tmp_path):  # noqa: F811
    run(directory, packet, tmp_path)
    row = search(directory[0], "ALTSTAMP")[0]
    assert row["maker_name"] == "Synthetic Alpha"
    assert row["original_observation"]["maker_or_shop_source_name"] == "Synthetic Alpna"
    assert row["citation"]["edition"] == "5th" and row["citation"]["printed_page"] == 7
    assert len(row["citation"]["photos"]) == 2
    assert row["transcription_status"] == "REPORTED" and not row["training_eligible"]
    assert hybrid(directory[0], "ALTSTAMP", legibility="LEGIBLE")["decision"] == "CANDIDATE"
    assert hybrid(directory[0], "ALPHA ALTSTAMP", legibility="LEGIBLE")["decision"] == "UNKNOWN"
    assert hybrid(directory[0], "ear-form", legibility="LEGIBLE")["decision"] == "UNKNOWN"
    for word in ("Unknown", "Pictorial"):
        r = search(directory[0], word)[0]
        assert r["transcription"] is None and r["transcription_variants"] == []
    assert hybrid(directory[0], "STERLING A", legibility="LEGIBLE")["decision"] == "UNKNOWN"
    assert any(x.get("source_kind") == "MANUAL_BOOK" for x in source_registry(directory[0]))
    from radar.references.directory_evaluation import source_details

    row["citation"]["title"] = "Synthetic <script>Book</script>"
    rendered = source_details(row)
    assert "5th edition" in rendered and "printed p. 7" in rendered
    assert "&lt;script&gt;" in rendered and "<script>" not in rendered
    assert 'href="None"' not in rendered and "<img" not in rendered


def test_resume_and_shared_ambiguity_even_before_partner_import(directory, packet, tmp_path):  # noqa: F811
    first = run(directory, packet, tmp_path, max_records=2)
    assert first["imported"] == 2 and not first["complete"]
    assert hybrid(directory[0], "SHARED", legibility="LEGIBLE")["decision"] == "UNKNOWN"
    result = import_bundle(
        directory[0],
        packet[0],
        packet[1],
        apply=True,
        backup_destination=tmp_path / "resume-backup",
    )
    assert result["imported"] == 5 and result["already_present"] == 2 and result["complete"]
    shared = hybrid(directory[0], "SHARED", legibility="LEGIBLE")
    assert shared["decision"] == "UNKNOWN"
    assert len([c for c in shared["candidates"] if c["textual_support"]]) == 2
    assert any(r["kind"] == "TEXT_COLLISION" for r in shared["candidates"][0]["relationships"])
    before = sha((directory[0] / "directory.db").read_bytes())
    again = import_bundle(directory[0], packet[0], packet[1], apply=True)
    assert again["imported"] == 0 and again["already_present"] == 7
    assert sha((directory[0] / "directory.db").read_bytes()) == before


@pytest.mark.parametrize("filename", ["synthetic-photo-0.png", "original.csv", "validation.json"])
def test_payload_failure_never_waived(directory, packet, tmp_path, filename):  # noqa: F811
    path = packet[0].parent / filename
    path.write_bytes(path.read_bytes() + b"corruption")
    before = sha((directory[0] / "directory.db").read_bytes())
    with pytest.raises(ValueError, match="mismatch"):
        run(directory, packet, tmp_path)
    assert sha((directory[0] / "directory.db").read_bytes()) == before


@pytest.mark.parametrize("mutation", ["scope", "rights", "photo", "original", "unknown", "csv"])
def test_structural_and_correction_gates(directory, packet, tmp_path, mutation):  # noqa: F811
    path, _, m, old = packet
    m = copy.deepcopy(m)
    vp = path.parent / "validation.json"
    data = json.loads(vp.read_text())
    e = data["entries"][0]
    if mutation == "scope":
        m["entry_ids"].pop()
    elif mutation == "rights":
        m["rights"]["training"] = True
    elif mutation == "photo":
        e["evidence_photos"].pop()
    elif mutation == "original":
        e["original_csv"]["maker_or_shop_source_name"] = "Silently rewritten"
    elif mutation == "unknown":
        e["mark_claim_status"] = "UNKNOWN_IN_SOURCE"
    elif mutation == "csv":
        e["reviewed_source_name"] = "Unreviewed substitute"
    vp.write_text(json.dumps(data))
    fingerprint = seal(path, m)
    with pytest.raises(ValueError):
        run(directory, (path, fingerprint, m, old), tmp_path)
    assert stats(directory[0])["records"] == 1


def test_manifest_pin_and_path_escape(directory, packet, tmp_path):  # noqa: F811
    with pytest.raises(ValueError, match="manifest digest"):
        import_bundle(directory[0], packet[0], "0" * 64)
    path, _, m, _ = packet
    m["files"][0]["name"] = "../outside.png"
    path.write_text(json.dumps(m))
    with pytest.raises(ValueError, match="Unsafe"):
        load_bundle(path, sha(path.read_bytes()))


def test_symlink_payload_rejected(packet, tmp_path):
    path = packet[0].parent / "synthetic-photo-0.png"
    target = tmp_path / "external.png"
    path.rename(target)
    path.symlink_to(target)
    with pytest.raises(ValueError, match="Symlink"):
        load_bundle(packet[0], packet[1])


def test_migration_failure_rolls_back(directory, packet, tmp_path, monkeypatch):  # noqa: F811
    import radar.references.books as books

    monkeypatch.setattr(books, "MIGRATION_2", (*books.MIGRATION_2, "INVALID SYNTHETIC SQL"))
    import sqlite3

    with pytest.raises(sqlite3.OperationalError):
        run(directory, packet, tmp_path)
    with connect(directory[0]) as c:
        assert c.execute("PRAGMA user_version").fetchone()[0] == 1
        assert not c.execute("SELECT name FROM sqlite_master WHERE name='book_imports'").fetchall()
    assert stats(directory[0])["records"] == 1


def test_pinned_book_review_does_not_expire_but_can_be_revoked(directory, packet, tmp_path):  # noqa: F811
    path, _, m, old = packet
    m["reviewed_at"] = (datetime.now(UTC) - timedelta(days=60)).isoformat()
    fingerprint = seal(path, m)
    run(directory, (path, fingerprint, m, old), tmp_path)
    assert search(directory[0], "ALTSTAMP")
    put_review(directory[0], dict(m, facts_allowed=False))
    assert not search(directory[0], "ALTSTAMP")
    with pytest.raises(ValueError, match="revoked"):
        import_bundle(directory[0], path, fingerprint, apply=True)


def test_new_review_cannot_replace_existing_claim(directory, packet, tmp_path):  # noqa: F811
    run(directory, packet, tmp_path)
    path, _, m, _ = packet
    m["authorization"] += " Different packet version."
    fingerprint = seal(path, m)
    with pytest.raises(ValueError, match="Conflicting existing book entry"):
        import_bundle(
            directory[0], path, fingerprint, apply=True, backup_destination=tmp_path / "replacement"
        )


def test_handoff_retains_book_inputs_and_resume_without_original_workspace(
    directory,  # noqa: F811
    packet,
    library,  # noqa: F811
    tmp_path,  # noqa: F811
):
    run(directory, packet, tmp_path, max_records=2)
    evaluation = tmp_path / "evaluation/package"
    evaluation.mkdir(parents=True)
    raw = image("red")
    (evaluation / "synthetic.png").write_bytes(raw)
    (evaluation / "manifest.json").write_text(
        json.dumps(
            {
                "image_count": 1,
                "items": [{"file": "synthetic.png", "sha256": sha(raw), "bytes": len(raw)}],
            }
        )
    )
    archive = tmp_path / "handoff"
    receipt = backup(archive, library, directory[0], evaluation.parent)
    destination = tmp_path / "restored"
    result = restore(archive, destination, receipt["manifest_sha256"])
    assert result["checks"]["book_evidence"]["records"] == 2
    restored = destination / "directory"
    manifest = restored / "book_sources" / packet[1] / "book-review.json"
    assert (
        import_bundle(
            restored,
            manifest,
            packet[1],
            apply=True,
            backup_destination=tmp_path / "restored-before",
        )["imported"]
        == 5
    )
    assert verify(restored)["records"] == 7
    photo = manifest.parent / "synthetic-photo-0.png"
    photo.write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="mismatch"):
        verify(restored)
    with pytest.raises(ValueError, match="mismatch"):
        backup(tmp_path / "bad-backup", library, restored, evaluation.parent)


def test_missing_relationship_fails_verification(directory, packet, tmp_path):  # noqa: F811
    run(directory, packet, tmp_path)
    with connect(directory[0], write=True) as c:
        c.execute("DELETE FROM book_group_members")
    with pytest.raises(ValueError, match="members differ"):
        verify(directory[0])
