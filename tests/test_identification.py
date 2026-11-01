"""Synthetic owner workflow, evidence, financial isolation and local image tests."""

import io
import json
import sqlite3

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import select

from radar.db import (
    ComparableSale,
    Listing,
    MakerCase,
    MakerDecision,
    MakerPhoto,
    MakerRun,
    MakerScenario,
    Setting,
)
from radar.identification import service as svc
from radar.identification.ranking import rank
from radar.main import create_app
from radar.references.directory import connect, digest, put_review, upsert
from radar.references.directory_library import import_library
from radar.references.similarity import build_index
from tests.test_book_references import packet  # noqa: F401
from tests.test_book_references import run as import_test_book
from tests.test_maker_directory import directory  # noqa: F401
from tests.test_reference_similarity import FakeEncoder, image, library  # noqa: F401


def record(name="Synthetic Alpha", text="ALPHA", **patch):
    r = dict(
        record_id=digest(name.encode()),
        source="one-published-source",
        maker_name=name,
        aliases=[],
        identity_kind="PERSON",
        transcription=text,
        transcription_status="REPORTED",
        description="Synthetic applied wire around a bell mark",
        image_urls=[],
        image_policy="EXTERNAL_LINK_ONLY",
        source_url="https://example.test/hallmarks/a.htm",
        independently_authenticated=False,
        source_independence="NOT_ESTABLISHED",
    )
    r.update(patch)
    return r


def query(text="ALPHA"):
    return dict(transcription=text, legibility="LEGIBLE", symbols=[], construction="")


def test_one_published_reference_supports_likely_candidate_without_independence():
    result = rank([record()], query())
    assert result["status"] == "CANDIDATES"
    assert result["candidates"][0]["strength"] == "Strong lettering match"
    assert not result["candidates"][0]["authenticated"]
    assert "probability" not in result["candidates"][0]


@pytest.mark.parametrize("text", ["", "STERLING", "925", "NICKEL SILVER", "A"])
def test_generic_text_and_construction_alone_remain_unknown(text):
    q = query(text)
    q["construction"] = "applied wire split shank"
    assert rank([record(text="STERLING")], q)["status"] == "UNKNOWN"


def test_text_symbols_visual_and_construction_combine_without_probabilities():
    r = record(local_reference_ids=["reference"])
    q = query()
    q.update(symbols=["bell"], construction="applied wire")
    c = rank([r], q, [{"reference_id": "reference", "similarity": 0.81}])["candidates"][0]
    assert all(
        any(word in evidence for evidence in c["similarities"])
        for word in ["Lettering", "Pictorial", "photo", "Construction"]
    )
    assert c["visual"] == ["reference"]
    assert "Visual closeness" in " ".join(c["discrepancies"])
    q = query("STERLING")
    assert (
        rank(
            [record(text="STERLING", local_reference_ids=["reference"])],
            q,
            [{"reference_id": "reference", "similarity": 0.99}],
        )["status"]
        == "UNKNOWN"
    )


def test_shared_initials_and_unreadable_or_conflicting_letters_remain_qualified():
    r = record(text="A A", shared_stamp_claims=[{"claim": "Synthetic two makers share this stamp"}])
    c = rank([r], query("AA"))["candidates"][0]
    assert c["strength"] == "Possible lettering match" and c["discrepancies"]
    q = query()
    q["legibility"] = "UNREADABLE"
    assert rank([record()], q)["status"] == "UNKNOWN"
    rows = [record(f"Synthetic Maker {i}", "AA") for i in range(8)]
    assert len(rank(rows, query("AA"))["candidates"]) == 5


@pytest.fixture
def owner_client(directory, library, tmp_path, monkeypatch):  # noqa: F811
    def deny(*_a, **_k):
        raise AssertionError("External HTTP is not authorized")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", deny)
    root, _, rid = directory
    with connect(root, write=True) as c:
        r = record()
        r["locator"] = "synthetic-alpha"
        upsert(c, r, rid, "art-amerindien")
    build_index(library, FakeEncoder(), True)
    import_library(root, library, include_fair_use=True)
    app = create_app(f"sqlite:///{tmp_path / 'owner.db'}")
    app.state.identification_config = svc.Config(
        root, library, tmp_path / "unused-model", tmp_path / "owner-images"
    )
    app.state.identification_encoder = FakeEncoder()
    with TestClient(app) as client:
        yield client


def create(client, **data):
    response = client.post(
        "/identify/cases",
        data={"title": "SYNTHETIC unfamiliar jewelry", **data},
        files={"hallmark_photos": ("synthetic.png", image("red"), "image/png")},
    )
    assert response.status_code == 200, response.text
    return int(response.url.path.rsplit("/", 1)[1])


def search(client, case, text="ALPHA", photo=None):
    response = client.post(
        f"/identify/cases/{case}/search",
        data={"transcription": text, "legibility": "LEGIBLE", "photo_id": photo or ""},
    )
    assert response.status_code == 200, response.text
    with client.app.state.sessions() as s:
        run = svc.latest_run(s, case)
    return run, response


def decision(client, case, run, action="CONFIRM", candidate=None, **kwargs):
    return client.post(
        f"/identify/cases/{case}/decisions",
        data={
            "run_id": str(run.id),
            "candidate_id": candidate or run.data["candidates"][0]["id"],
            "action": action,
            "note": "SYNTHETIC owner reason",
            **kwargs,
        },
    )


def test_ui_upload_local_search_confirm_reject_correct_and_history(owner_client):
    c = owner_client
    case = create(c)
    cfg = c.app.state.identification_config
    before = digest((cfg.directory / "directory.db").read_bytes())
    assert "Who likely made this piece?" in c.get("/identify").text
    run, response = search(c, case)
    assert "Synthetic Alpha" in response.text and "Strong lettering match" in response.text
    assert "Original source citation" in response.text
    assert decision(c, case, run).status_code == 200
    with c.app.state.sessions() as s:
        assert svc.accepted(s, case).data["maker"] == "Synthetic Alpha"
        assert not svc.accepted(s, case).data["independently_authenticated"]
    assert decision(c, case, run, "REJECT").status_code == 200
    rerun, response = search(c, case)
    assert rerun.data["status"] == "UNKNOWN" and rerun.data["rejected"]
    assert "Rejected candidates retained" in response.text
    assert (
        decision(c, case, rerun, "RESEARCH", candidate=run.data["candidates"][0]["id"]).status_code
        == 200
    )
    rerun, _ = search(c, case)
    assert decision(c, case, rerun, "CORRECT_TRANSCRIPTION", value="ALPH?").status_code == 200
    assert 'value="ALPH?"' in c.get(f"/identify/cases/{case}").text
    assert (
        decision(c, case, rerun, "CORRECT_MAKER", value="Synthetic Owner Maker").status_code == 200
    )
    rerun, _ = search(c, case)
    assert rerun.data["candidates"][0]["maker"] == "Synthetic Owner Maker"
    assert rerun.data["candidates"][0]["references"] == []
    exported = c.get(f"/identify/cases/{case}/export").json()
    assert len(exported["owner_decisions"]) == 5
    assert digest((cfg.directory / "directory.db").read_bytes()) == before


def test_crop_visual_evidence_photos_private_and_index_unchanged(owner_client):
    c = owner_client
    case = create(c)
    cfg = c.app.state.identification_config
    before = digest((cfg.library / "similarity/index.json").read_bytes())
    with c.app.state.sessions() as s:
        photo = s.scalar(select(MakerPhoto).where(MakerPhoto.case_id == case))
    assert (
        c.post(
            f"/identify/cases/{case}/photos/{photo.id}/crop",
            data={"x1": "12", "y1": "12", "x2": "80", "y2": "80"},
        ).status_code
        == 200
    )
    with c.app.state.sessions() as s:
        crop = s.scalar(
            select(MakerPhoto).where(MakerPhoto.case_id == case, MakerPhoto.id != photo.id)
        )
    assert crop.data["parent"] == photo.id and crop.data["box"] == [12, 12, 80, 80]
    run, response = search(c, case, text="", photo=crop.id)
    assert run.data["candidates"] and run.data["visual_reference_ids"]
    ref = run.data["candidates"][0]["references"][0]
    r = c.get(f"/identify/reference/{ref['record_id']}?view={ref['local_reference_ids'][0]}")
    assert r.status_code == 200 and r.headers["cache-control"] == "private, no-store"
    assert "Eligible local reference mark" in response.text
    assert digest((cfg.library / "similarity/index.json").read_bytes()) == before
    assert c.get(f"/identify/cases/{case + 1}/photos/{photo.id}").status_code == 404
    assert (
        c.post(
            f"/identify/cases/{case}/photos/{photo.id}/crop",
            data={"x1": "-1", "y1": "12", "x2": "80", "y2": "80"},
        ).status_code
        == 422
    )


def test_no_image_fetch_external_only_and_revoked_rights(owner_client, directory):  # noqa: F811
    c = owner_client
    case = create(c)
    run, _ = search(c, case)
    rid = run.data["candidates"][0]["references"][0]["record_id"]
    assert c.get(f"/identify/reference/{rid}").status_code == 404
    root, review, _ = directory
    put_review(root, dict(review, facts_allowed=False))
    assert c.get(f"/identify/reference/{rid}").status_code == 404
    run, _ = search(c, case)
    assert not run.data["candidates"]


def test_future_feedback_scoped_to_distinguishing_query_and_no_source_mutation(owner_client):
    c = owner_client
    a = create(c)
    run, _ = search(c, a)
    assert decision(c, a, run).status_code == 200
    b = create(c)
    run, _ = search(c, b)
    assert run.data["candidates"][0]["owner_feedback"][0]["case_id"] == a
    with c.app.state.sessions() as s:
        assert not svc.feedback(s, b, query("STERLING"))
        assert not svc.feedback(s, b, query("DIFFERENT"))


def test_stale_run_and_cross_case_decision_are_rejected(owner_client):
    c = owner_client
    a = create(c)
    b = create(c)
    old, _ = search(c, a)
    new, _ = search(c, a)
    assert decision(c, a, old).status_code == 422
    assert decision(c, b, new).status_code == 422
    assert decision(c, a, new, candidate="invented").status_code == 422
    assert c.post(f"/identify/cases/{a}/search", data={"symbols": "invented"}).status_code == 422
    assert (
        c.post(
            "/identify/cases", data={"title": "blocked"}, headers={"Origin": "https://evil.test"}
        ).status_code
        == 403
    )


def test_workbench_and_comparable_scenarios_conditional_without_financial_mutation(
    owner_client, item, prefs
):
    c = owner_client
    with c.app.state.sessions.begin() as s:
        listing = Listing(
            dedup_key="synthetic-scenario",
            data=item.model_dump(mode="json"),
            original_data={"synthetic": True},
        )
        s.add(listing)
        s.flush()
        lid = listing.id
        before = dict(listing.data)
        comp = ComparableSale(
            data=dict(
                category="ring",
                materials="source-reported silver",
                condition="used",
                sale_price="70",
                sale_date="2026-01-01",
                evidence="Synthetic Alpha published sold record",
                notes="",
                synthetic=False,
            )
        )
        s.add(comp)
        s.flush()
        compid = comp.id
    case = create(c, listing_id=str(lid))
    run, _ = search(c, case)
    cid = run.data["candidates"][0]["id"]
    with c.app.state.sessions.begin() as s:
        scenario = svc.scenario(s, case, cid, [compid], prefs)
        assert not scenario.data["owner_accepted_at_creation"]
        assert "CONDITIONAL" in svc.scenario_status(s, s.get(MakerCase, case), scenario)
        assert s.get(Listing, lid).data == before
    assert decision(c, case, run).status_code == 200
    response = c.post(
        f"/identify/cases/{case}/scenarios",
        data={"candidate_id": cid, "comparable_id": str(compid), "selected_for_comparison": "yes"},
    )
    assert response.status_code == 200, response.text
    assert "OWNER-ACCEPTED LIKELY MAKER" in response.text
    assert "Inspect maker-based value" in c.get(f"/listings/{lid}").text
    assert "Synthetic Alpha published sold record" in c.get("/comparables?q=Synthetic%20Alpha").text
    assert "Synthetic Alpha published sold record" not in c.get("/comparables?q=absent-maker").text
    with c.app.state.sessions.begin() as s:
        changed = prefs.model_dump(mode="json")
        changed["min_profit"] = "100"
        s.merge(Setting(id=1, data=changed))
    assert "RECALCULATE — fee or buying preferences have changed" in c.get(f"/listings/{lid}").text
    assert decision(c, case, run, "REJECT").status_code == 200
    assert "CONDITIONAL" in c.get(f"/listings/{lid}").text
    with c.app.state.sessions() as s:
        assert s.get(Listing, lid).data == before
        assert s.scalar(select(MakerScenario).order_by(MakerScenario.id.desc())).data["analysis"]


def test_new_case_workbench_link_idempotent_and_unknown_costs(owner_client):
    c = owner_client
    case = create(c)
    r = c.post(f"/identify/cases/{case}/workbench")
    assert r.status_code == 200
    assert c.post(f"/identify/cases/{case}/workbench").url == r.url
    with c.app.state.sessions() as s:
        listing = s.get(Listing, s.get(MakerCase, case).listing_id)
        assert listing.data["item_price"] is None and listing.data["resale_low"] is None
        assert (
            not listing.data["weight_verified"] and listing.data["material_evidence"] == "unknown"
        )


def test_bad_upload_exif_xss_and_blank_photo(owner_client):
    c = owner_client
    assert (
        c.post(
            "/identify/cases",
            data={"title": "bad"},
            files={"hallmark_photos": ("x.svg", b"<svg/>", "image/svg+xml")},
        ).status_code
        == 422
    )
    raw = io.BytesIO()
    im = Image.new("RGB", (96, 96), "white")
    exif = Image.Exif()
    exif[271] = "PRIVATE"
    im.save(raw, "JPEG", exif=exif)
    response = c.post(
        "/identify/cases",
        data={"title": "<script>alert(1)</script>"},
        files={"hallmark_photos": ("private.jpg", raw.getvalue(), "image/jpeg")},
    )
    assert response.status_code == 200 and "<script>alert" not in response.text
    case = int(response.url.path.rsplit("/", 1)[1])
    with c.app.state.sessions() as s:
        photo = s.scalar(select(MakerPhoto).where(MakerPhoto.case_id == case))
    content = c.get(f"/identify/cases/{case}/photos/{photo.id}").content
    assert not Image.open(io.BytesIO(content)).getexif()
    run, _ = search(c, case, text="", photo=photo.id)
    assert run.data["status"] == "UNKNOWN" and "contrast" in run.data["warnings"][0]


def test_missing_directory_never_initializes_replacement(owner_client, tmp_path):
    owner_client.app.state.identification_config.directory = tmp_path / "missing"
    assert "Restore the verified handoff" in owner_client.get("/identify").text
    assert owner_client.post("/identify/cases", data={"title": "blocked"}).status_code == 422
    assert not (tmp_path / "missing").exists()


def test_migration_preserves_all_v5_tables(tmp_path):
    from sqlalchemy import create_engine

    from radar.migrations import VERSION_1, VERSION_2, VERSION_3, VERSION_4, VERSION_5, migrate

    p = tmp_path / "old.db"
    with sqlite3.connect(p) as c:
        for version in [VERSION_1, VERSION_2, VERSION_3, VERSION_4, VERSION_5]:
            for statement in version:
                c.execute(statement)
        c.execute("PRAGMA user_version=5")
        c.execute(
            "INSERT INTO settings VALUES (?,?)", (1, json.dumps({"preserve": "financial settings"}))
        )
        c.execute(
            "INSERT INTO vision_prices VALUES (?,?,?,?)",
            (1, "SYNTHETIC unchanged Haiku", "{}", "2026-01-01"),
        )
        tables = [r[0] for r in c.execute("select name from sqlite_master where type='table'")]
        before = {t: c.execute("select * from " + t).fetchall() for t in tables}
    engine = create_engine(f"sqlite:///{p}")
    migrate(engine)
    migrate(engine)
    engine.dispose()
    with sqlite3.connect(p) as c:
        assert c.execute("pragma user_version").fetchone()[0] == 6
        assert all(c.execute("select * from " + t).fetchall() == rows for t, rows in before.items())
        assert c.execute("pragma integrity_check").fetchone()[0] == "ok"
        assert not c.execute("pragma foreign_key_check").fetchall()
    assert (tmp_path / "old.db.pre-v6.bak").exists()


def test_negated_symbols_and_unknown_source_identity_do_not_invent_a_maker():
    q = query("")
    q["symbols"] = ["feather"]
    r = record(description="Human profile/head, not a standalone feather.")
    assert rank([r], q)["status"] == "UNKNOWN"
    q["symbols"] = ["human profile"]
    assert rank([r], q)["candidates"]
    result = rank([record("UNKNOWN — J.B", "J.B")], query("JB"))
    assert result["status"] == "UNKNOWN" and result["unresolved_references"]
    alias = record(text="", aliases=["Synthetic Shop"])
    assert rank([alias], query("Synthetic Shop"))["candidates"]


def test_reference_with_strongest_specific_evidence_is_shown_first():
    weak = record(text="", record_id="weak", local_reference_ids=["weak-photo"])
    strong = record(text="ALPHA", record_id="strong")
    c = rank([weak, strong], query(), [{"reference_id": "weak-photo", "similarity": 0.99}])[
        "candidates"
    ][0]
    assert c["references"][0]["record_id"] == "strong"
    assert c["reference_evidence"]["strong"]["similarities"]


def test_transcription_correction_invalidates_old_acceptance_and_feedback(owner_client):
    c = owner_client
    case = create(c)
    run, _ = search(c, case)
    assert decision(c, case, run).status_code == 200
    assert decision(c, case, run, "CORRECT_TRANSCRIPTION", value="BETA").status_code == 200
    with c.app.state.sessions() as s:
        assert svc.accepted(s, case) is None
        assert not svc.feedback(s, case + 1, query("ALPHA"))


def test_photo_storage_cannot_mix_with_references_or_benchmark(owner_client):
    c = owner_client
    c.app.state.identification_config.images = c.app.state.identification_config.library
    assert (
        c.post(
            "/identify/cases",
            data={"title": "SYNTHETIC blocked storage"},
            files={"hallmark_photos": ("test.png", image("red"), "image/png")},
        ).status_code
        == 422
    )


def test_identification_pages_do_not_load_external_images_or_cache_private_content(owner_client):
    response = owner_client.get("/identify")
    assert "img-src 'self';" in response.headers["content-security-policy"]
    assert "https:" not in response.headers["content-security-policy"]
    assert response.headers["cache-control"] == "private, no-store"


def test_exact_photo_feedback_signature_is_separate_from_uninformative_text():
    from radar.identification.ranking import signature

    q = query("")
    assert signature(q) is None
    q["photo_hash"] = "a" * 64
    assert signature(q) is not None
    q["photo_hash"] = "b" * 64
    assert signature(q) != signature(dict(q, photo_hash="a" * 64))


def test_rejection_on_another_piece_does_not_override_distinctive_published_letters():
    strong = record()
    weak = record("Synthetic Alpha Workshop", text="")
    first = rank([strong], query())["candidates"][0]
    feedback = [
        dict(
            candidate_id=first["id"],
            action="REJECT",
            same_case=False,
            case_id=i,
            note="Different piece",
            basis="Owner judgment",
        )
        for i in range(20)
    ]
    result = rank([strong, weak], query(), feedback=feedback)
    assert result["candidates"][0]["maker"] == "Synthetic Alpha"


def test_demo_decisions_do_not_influence_real_case_rankings(owner_client):
    c = owner_client
    demo, real = create(c), create(c)
    with c.app.state.sessions.begin() as s:
        case = s.get(MakerCase, demo)
        case.data = {**case.data, "synthetic": True}
    run, _ = search(c, demo)
    assert decision(c, demo, run).status_code == 200
    other, _ = search(c, real)
    assert not other.data["candidates"][0]["owner_feedback"]


def test_new_search_input_is_not_overwritten_by_an_older_transcription_correction(owner_client):
    c = owner_client
    case = create(c)
    run, _ = search(c, case)
    assert decision(c, case, run, "CORRECT_TRANSCRIPTION", value="OLDER").status_code == 200
    _, response = search(c, case, text="LATEST")
    assert 'name="transcription" maxlength="300" value="LATEST"' in response.text


def test_private_book_views_resolve_each_verified_photo_and_fail_on_tampering(
    owner_client,
    directory,  # noqa: F811
    packet,  # noqa: F811
    tmp_path,
):
    from radar.references.directory import search as directory_search

    import_test_book(directory, packet, tmp_path)
    row = directory_search(directory[0], "ALTSTAMP")[0]
    urls = []
    for photo in row["citation"]["photos"]:
        url = f"/identify/reference/{row['record_id']}?view={photo['sha256']}"
        urls.append(url)
        response = owner_client.get(url)
        assert response.status_code == 200
        assert response.headers["cache-control"] == "private, no-store"
        assert digest(response.content) == photo["sha256"]
    assert len(urls) == 2
    assert owner_client.get(urls[0] + "invented").status_code == 404
    path = directory[0] / row["citation"]["photos"][1]["private_path"]
    path.write_bytes(image("yellow"))
    assert owner_client.get(urls[1]).status_code == 404
    assert owner_client.get(urls[0]).status_code == 200


@pytest.mark.parametrize(
    "label",
    [
        "Arrow head",
        "ARROW-HEAD",
        "arrowhead",
        "Broken arrow symbol",
        "Bird within a circle",
        "Feather pictograph",
        "Rain cloud mark on fake jewelry",
        "UNKNOWN",
        "Unidentified — mark 2",
    ],
)
def test_pictorial_labels_remain_unknown_even_with_lettering_visual_and_owner_support(label):
    from radar.identification.ranking import maker_key

    r = record(label, text="ALPHA", identity_kind="UNRESOLVED", local_reference_ids=["photo"])
    result = rank(
        [r],
        query(),
        [{"reference_id": "photo", "similarity": 0.99}],
        feedback=[
            dict(candidate_id=digest(maker_key(label).encode()), action="CONFIRM", same_case=True)
        ],
    )
    assert result["status"] == "UNKNOWN" and not result["candidates"]
    mark = result["unresolved_references"][0]
    assert mark["maker"] == label and mark["references"] == [r]
    assert "UNKNOWN" in mark["unresolved_reason"]
    assert mark["visual"] == ["photo"] and not mark["authenticated"]
    assert len(mark["owner_feedback"]) == 1


@pytest.mark.parametrize(
    "name",
    [
        "Synthetic Feather",
        "Bear: Synthetic Jane",
        "Sunburst Handcrafts Inc.",
        "Arrowhead Trading Post",
        "Bell Trading Post",
    ],
)
def test_named_people_and_shops_survive_unknown_entity_type_and_pictorial_words(name):
    result = rank([record(name, identity_kind="UNRESOLVED")], query())
    assert result["candidates"][0]["maker"] == name
    assert result["candidates"][0]["strength"] == "Strong lettering match"
    assert not result["unresolved_references"]


def test_description_does_not_consume_a_named_candidate_slot_or_modify_records():
    from copy import deepcopy

    rows = [
        record(
            "Arrow head", text="ALPHA", description="Synthetic arrow", identity_kind="TRADING_POST"
        )
    ]
    rows += [record(f"Synthetic Arrow Shop {i}", text="ALPHA") for i in range(6)]
    before = deepcopy(rows)
    result = rank(rows, dict(query(), symbols=["arrow"]))
    assert len(result["candidates"]) == 5
    assert all(c["maker"].startswith("Synthetic Arrow Shop") for c in result["candidates"])
    assert result["unresolved_references"][0]["maker"] == "Arrow head"
    assert rows == before


def test_cached_description_is_not_an_actionable_maker_and_saved_search_stays_intact(owner_client):
    from copy import deepcopy

    from radar.identification.ranking import display_result

    c = owner_client
    case = create(c)
    run, _ = search(c, case)
    old = deepcopy(run.data)
    old["policy"] = "jri005-owner-likely-maker-v1"
    descriptor = deepcopy(old["candidates"][0])
    descriptor.update(id=digest(b"ARROW HEAD"), maker="Arrow head", strength="Pictorial lead")
    old["candidates"].append(descriptor)
    old["rejected"] = [deepcopy(descriptor)]  # Old rejection must not offer reconsider-as-maker.
    old["warnings"] = ["SYNTHETIC seller-metadata-only fixture; photographs not evaluated"]
    with c.app.state.sessions.begin() as s:
        s.get(MakerRun, run.id).data = old
    presented = display_result(old)
    assert len(presented["unresolved_references"]) == 1
    assert presented["candidates"][0]["maker"] == "Synthetic Alpha"
    assert not presented["rejected"]
    response = c.get(f"/identify/cases/{case}")
    assert "Related marks with UNKNOWN maker" in response.text
    assert "Pictorial mark description, not a named maker or trading post" in response.text
    assert "original saved search remains unchanged" in response.text
    assert "seller-metadata-only" in response.text
    assert "<h3>Arrow head</h3>" not in response.text
    assert f'name="candidate_id" value="{descriptor["id"]}"' not in response.text
    assert decision(c, case, run, candidate=descriptor["id"]).status_code == 422
    assert decision(c, case, run, "CORRECT_MAKER", value="Arrow head").status_code == 422
    with c.app.state.sessions() as s:
        assert s.get(MakerRun, run.id).data == old
        assert not svc.decisions(s, case)


def test_legacy_descriptive_owner_feedback_cannot_create_identity_or_value(owner_client, prefs):
    from copy import deepcopy

    c = owner_client
    with connect(c.app.state.identification_config.directory, write=True) as directory_db:
        review = directory_db.execute(
            "SELECT review_id FROM current_reviews WHERE source='art-amerindien'"
        ).fetchone()[0]
        for i in range(4):
            r = record(f"Synthetic Alpha Workshop {i}", locator=f"synthetic-extra-{i}")
            upsert(directory_db, r, review, "art-amerindien")
    case = create(c)
    c.post(f"/identify/cases/{case}/workbench")
    run, _ = search(c, case)
    with c.app.state.sessions.begin() as s:
        old = deepcopy(run.data)
        candidate = old["candidates"][0]
        candidate.update(id=digest(b"ARROW HEAD"), maker="Arrow head")
        s.get(MakerRun, run.id).data = old
        d = MakerDecision(
            case_id=case,
            run_id=run.id,
            candidate_id=candidate["id"],
            action="CONFIRM",
            data={"maker": "Arrow head", "note": "SYNTHETIC legacy decision"},
        )
        s.add(d)
        s.flush()
        assert svc.accepted(s, case) is None
        with pytest.raises(ValueError, match="unresolved mark description"):
            svc.scenario(s, case, candidate["id"], [1], prefs)
        d.action = "CORRECT_MAKER"  # Construct a legacy correction fixture, not a UI update.
    new, response = search(c, case)
    assert len(new.data["candidates"]) == 5
    assert all(x["maker"] != "Arrow head" for x in new.data["candidates"])
    assert new.data["unresolved_references"][0]["maker"] == "Arrow head"
    assert "<h3>Arrow head</h3>" not in response.text
