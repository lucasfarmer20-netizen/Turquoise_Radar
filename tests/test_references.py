"""All API replies and image bytes here are generated synthetic fixtures. No live requests."""

import io
import json
import random
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from PIL import Image, PngImagePlugin
from pydantic import ValidationError

from radar.references.adapters import ADAPTERS, AIC, CMA, Commons, Met
from radar.references.ingest import collect, relevant
from radar.references.library import (
    add_evidence,
    add_mark,
    add_pair,
    crop,
    exclude,
    export_dataset,
    import_manifest,
    purge,
    search,
)
from radar.references.media import InvalidImage, normalize
from radar.references.network import PoliteHTTP, StopCollection, valid_url
from radar.references.schema import Assertion, Item, Source
from radar.references.sources import seed
from radar.references.store import connect, dump, exclusive, get_source, put_source


@pytest.fixture(autouse=True)
def no_real_http(monkeypatch):
    def reject(*args, **kwargs):
        raise AssertionError("Unmocked HTTP forbidden")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", reject)


class Clock:
    def __init__(self):
        self.time = 100000.0

    def __call__(self):
        return self.time

    def sleep(self, seconds):
        self.time += seconds


@pytest.fixture
def root(tmp_path):
    root = tmp_path / "private"
    c = connect(root)
    seed(c)
    # Source reviews in tests stay current independently of the wall-clock date.
    for r in c.execute("SELECT id FROM sources").fetchall():
        s = get_source(c, r["id"])
        s.reviewed_on = datetime.now(UTC).date()
        with c:
            put_source(c, s)
    c.close()
    return root


def png(seed=1, text="SYNTHETIC PRIVATE"):
    rng = random.Random(seed)
    im = Image.new("RGB", (100, 100))
    im.putdata([(rng.randrange(256), rng.randrange(256), rng.randrange(256)) for _ in range(10000)])
    meta = PngImagePlugin.PngInfo()
    meta.add_text("Owner", text)
    b = io.BytesIO()
    im.save(b, format="PNG", pnginfo=meta)
    return b.getvalue()


def cma(id_=1, rights="CC0", title="SYNTHETIC cuff", image_url=None):
    return {
        "id": id_,
        "title": title,
        "type": "Jewelry",
        "accession_number": str(id_),
        "share_license_status": rights,
        "creators": [{"description": "SYNTHETIC artist"}],
        "technique": "Silver (catalog claim, not a test)",
        "images": {
            "web": {"url": image_url or f"https://openaccess-cdn.clevelandart.org/{id_}.png"}
        },
    }


def transport(rows, *, fail_image=None, image_data=None):
    calls = []

    def handler(request):
        calls.append(str(request.url))
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /\n")
        if request.url.host == "openaccess-api.clevelandart.org":
            skip = int(request.url.params["skip"])
            limit = int(request.url.params["limit"])
            return httpx.Response(
                200, json={"data": rows[skip : skip + limit], "info": {"total": len(rows)}}
            )
        if request.url.host == "openaccess-cdn.clevelandart.org":
            if fail_image and request.url.path == fail_image:
                return httpx.Response(503, headers={"Retry-After": "60"}, text="PRIVATE BODY")
            return httpx.Response(200, content=image_data if image_data is not None else png())
        raise AssertionError("Unexpected synthetic URL")

    return httpx.MockTransport(handler), calls


def run(root, rows, **kw):
    tr, calls = transport(rows, **kw.pop("transport_kw", {}))
    clock = Clock()
    # Across resumes use a future clock, honoring durable rate state without real sleeps.
    with connect(root) as c:
        latest = c.execute("SELECT max(next_at) FROM rate_state").fetchone()[0]
    clock.time = max(clock.time, latest or 0) + 100000
    result = collect(root, "cma", "jewelry", transport=tr, clock=clock, sleep=clock.sleep, **kw)
    return result, calls


def test_complete_collection_rights_relevance_and_private_media(root):
    result, calls = run(
        root,
        [cma(), cma(2, rights="Copyrighted"), dict(cma(3, title="SYNTHETIC vase"), type="Ceramic")],
    )
    assert result["images_stored"] == 1 and result["rejected_records"] == 2
    with connect(root) as c:
        a = c.execute("SELECT * FROM assets").fetchone()
        d = json.loads(a["data"])
        assert (
            d["rights"]["license"] == "CC0"
            and d["rights"]["evidence"] == "share_license_status=CC0"
        )
        assert (root / d["original_path"]).read_bytes() == png()
        with Image.open(root / d["normalized_path"]) as im:
            assert not im.info
        assert all(
            json.loads(r["data"])["status"] == "ATTRIBUTED"
            for r in c.execute("SELECT data FROM assertions")
        )
        assert "PRIVATE" not in "\n".join(r["data"] for r in c.execute("SELECT data FROM audit"))
        assert len(search(c, "SYNTHETIC artist")) == 1
    assert all("/2.png" not in u and "/3.png" not in u for u in calls)


def test_pagination_and_bounded_resume_do_not_skip_pending_rows(root):
    rows = [cma(i) for i in range(1, 8)]
    first, _ = run(root, rows, max_images=2, page_size=5)
    assert first["images_stored"] == 2 and first["discovered"] == 5
    second, _ = run(root, rows, max_images=20, page_size=5)
    assert second["images_stored"] == 5
    third, calls = run(root, rows)
    assert third["status"] == "EXHAUSTED" and not calls
    with connect(root) as c:
        assert c.execute("SELECT count(*) FROM assets").fetchone()[0] == 7
        assert c.execute("SELECT count(DISTINCT original_hash) FROM assets").fetchone()[0] == 1
        assert c.execute("SELECT count(*) FROM duplicates").fetchone()[0] == 6


def test_cma_checkpoint_resume_after_committed_image_interrupt(root, monkeypatch):
    from radar.references import ingest

    rows = [cma(i) for i in range(1, 21)]
    first, _ = run(root, rows, max_images=10, max_items=20, page_size=20)
    assert first["discovered"] == 20 and first["images_stored"] == 10
    with connect(root) as c:
        original_assets = [dict(r) for r in c.execute("SELECT * FROM assets ORDER BY id")]
        original_stream = dict(c.execute("SELECT * FROM streams").fetchone())
        original_queue = [dict(r) for r in c.execute("SELECT * FROM queue ORDER BY id")]
    original_files = {
        str(p): p.read_bytes() for p in root.rglob("*") if p.suffix in {".bin", ".png"}
    }
    # A real process can stop after an asset commit but before the queue is DONE.
    save = ingest.save_asset

    def interrupt_after_commit(*args):
        save(*args)
        raise KeyboardInterrupt()

    with monkeypatch.context() as patch:
        patch.setattr(ingest, "save_asset", interrupt_after_commit)
        with pytest.raises(KeyboardInterrupt):
            run(root, rows, max_images=10, max_items=20, page_size=20)
    with connect(root) as c:
        assert c.execute("SELECT count(*) FROM assets").fetchone()[0] == 11
        assert (
            c.execute("SELECT state FROM queue WHERE external_id='11'").fetchone()[0] == "PENDING"
        )
    resumed, calls = run(root, rows, max_images=9, max_items=20, page_size=20)
    assert resumed["pages"] == 0 and resumed["discovered"] == 0
    assert resumed["images_stored"] == 9 and resumed["existing_images"] == 1
    assert all("/api/artworks/" not in url for url in calls)
    assert not {f"https://openaccess-cdn.clevelandart.org/{i}.png" for i in range(1, 12)} & set(
        calls
    )
    assert all(Path(path).read_bytes() == content for path, content in original_files.items())
    with connect(root) as c:
        assert dict(c.execute("SELECT * FROM streams").fetchone()) == original_stream
        assert c.execute("SELECT count(*) FROM assets").fetchone()[0] == 20
        assert c.execute("SELECT count(*) FROM queue WHERE state='DONE'").fetchone()[0] == 20
        for before in original_assets:
            assert (
                dict(c.execute("SELECT * FROM assets WHERE id=?", (before["id"],)).fetchone())
                == before
            )
        after_queue = [dict(r) for r in c.execute("SELECT * FROM queue ORDER BY id")]
        for before, after in zip(original_queue, after_queue, strict=True):
            assert all(before[k] == after[k] for k in ("id", "external_id", "payload"))


@pytest.mark.parametrize("measurements", [None, "Overall: 4.9 cm (SYNTHETIC catalog summary)"])
def test_cma_retains_culture_and_full_dimensions_as_attributed_evidence(measurements):
    raw = cma()
    raw.update(
        culture=["SYNTHETIC culture, attribution uncertain"],
        creators=[],
        measurements=measurements,
        dimensions={"overall": {"length": 0.117, "width": 0.049}},
    )
    item = CMA().item(None, raw)
    fields = {a.field: a for a in item.assertions}
    assert "SYNTHETIC culture, attribution uncertain" in fields["cultural_attribution"].value
    assert "0.117" in fields["source_dimensions"].value
    assert "0.049" in fields["source_dimensions"].value
    assert ("stated_dimensions" in fields) == (measurements is not None)
    assert all(a.status == "ATTRIBUTED" and not a.observable_from_photo for a in item.assertions)
    assert all(a.evidence_url == item.page_url for a in item.assertions)
    assert "stated_weight" not in fields and "maker_attribution" not in fields
    assert item.artists == []


def test_server_error_retains_queue_and_sanitizes_diagnostics(root):
    result, _ = run(root, [cma()], transport_kw={"fail_image": "/1.png"})
    assert result["error"] == "HTTP_ERROR" and result["http_status"] == 503
    with connect(root) as c:
        assert c.execute("SELECT state FROM queue").fetchone()[0] == "PENDING"
        assert "PRIVATE BODY" not in c.execute("SELECT data FROM batches").fetchone()[0]
    clock = Clock()
    clock.time = 999999
    tr, _ = transport([cma()])
    result = collect(root, "cma", "jewelry", transport=tr, clock=clock, sleep=clock.sleep)
    assert result["images_stored"] == 1


def test_keyboard_interrupt_retains_partial_item_and_recovers(root):
    tr, _ = transport([cma()])
    base = tr.handle_request

    def crash(req):
        if req.url.path == "/1.png":
            raise KeyboardInterrupt()
        return base(req)

    clock = Clock()
    with pytest.raises(KeyboardInterrupt):
        collect(
            root,
            "cma",
            "jewelry",
            transport=httpx.MockTransport(crash),
            clock=clock,
            sleep=clock.sleep,
        )
    with connect(root) as c:
        assert c.execute("SELECT status FROM batches").fetchone()[0] == "INTERRUPTED"
    result, _ = run(root, [cma()])
    assert result["images_stored"] == 1


@pytest.mark.parametrize("status", [401, 403, 429, 500, 503])
def test_robots_fail_closed(root, status):
    clock = Clock()
    c = connect(root)
    calls = []

    def handler(r):
        calls.append(r.url.path)
        return httpx.Response(status, text="sensitive upstream body")

    http = PoliteHTTP(
        c,
        get_source(c, "cma"),
        transport=httpx.MockTransport(handler),
        clock=clock,
        sleep=clock.sleep,
    )
    with pytest.raises(StopCollection) as e:
        http.get("https://openaccess-api.clevelandart.org/api/artworks/")
    assert e.value.category == "ROBOTS_UNAVAILABLE" and calls == ["/robots.txt"]
    http.close()
    c.close()


def test_robots_directive_and_rate_limit_are_honored(root):
    c = connect(root)
    clock = Clock()
    calls = []

    def handler(r):
        calls.append(r.url.path)
        return httpx.Response(200, text="User-agent: *\nDisallow: /private\nCrawl-delay: 7\n")

    h = PoliteHTTP(
        c,
        get_source(c, "cma"),
        transport=httpx.MockTransport(handler),
        clock=clock,
        sleep=clock.sleep,
    )
    with pytest.raises(StopCollection, match="ROBOTS_DENIED"):
        h.get("https://openaccess-api.clevelandart.org/private")
    assert calls == ["/robots.txt"]
    h.get("https://openaccess-api.clevelandart.org/allowed")
    h.get("https://openaccess-api.clevelandart.org/another")
    assert clock.time >= 100014
    h.close()
    c.close()


@pytest.mark.parametrize(
    "url",
    [
        "http://openaccess-api.clevelandart.org/a",
        "https://evil.invalid/a",
        "https://u:p@openaccess-api.clevelandart.org/a",
        "https://127.0.0.1/a",
        "https://localhost/a",
        "https://openaccess-api.clevelandart.org:8000/a",
    ],
)
def test_host_and_protocol_validation(url):
    with pytest.raises(StopCollection):
        valid_url(url, ["openaccess-api.clevelandart.org", "127.0.0.1", "localhost"])


def test_redirect_is_checked_before_next_request(root):
    c = connect(root)
    clock = Clock()
    calls = []

    def handler(r):
        calls.append(str(r.url))
        if r.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(302, headers={"Location": "https://evil.invalid/photo"})

    h = PoliteHTTP(
        c,
        get_source(c, "cma"),
        transport=httpx.MockTransport(handler),
        clock=clock,
        sleep=clock.sleep,
    )
    with pytest.raises(StopCollection, match="URL_NOT_APPROVED"):
        h.get("https://openaccess-api.clevelandart.org/a")
    assert len(calls) == 2
    h.close()
    c.close()


def test_response_cap_and_proxy_errors_never_capture_body(root):
    c = connect(root)
    clock = Clock()

    def handler(r):
        if r.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, content=b"x" * 5000)

    h = PoliteHTTP(
        c,
        get_source(c, "cma"),
        transport=httpx.MockTransport(handler),
        clock=clock,
        sleep=clock.sleep,
    )
    with pytest.raises(StopCollection, match="RESPONSE_TOO_LARGE"):
        h.get("https://openaccess-api.clevelandart.org/a", 100)
    h.close()

    def proxy(r):
        raise httpx.ProxyError("SENSITIVE proxy details")

    h = PoliteHTTP(
        c,
        get_source(c, "cma"),
        transport=httpx.MockTransport(proxy),
        clock=clock,
        sleep=clock.sleep,
    )
    with pytest.raises(StopCollection, match="NETWORK_PROXY_BLOCK") as e:
        h.get("https://openaccess-api.clevelandart.org/a")
    assert "SENSITIVE" not in str(e.value)
    h.close()
    c.close()


@pytest.mark.parametrize("data", [b"", b"not an image", b"<html>blocked</html>"])
def test_invalid_image_bytes_rejected(data):
    with pytest.raises(InvalidImage):
        normalize(data)


def test_near_duplicates_are_flagged_not_silently_discarded(root):
    raw = png()
    im = Image.open(io.BytesIO(raw)).convert("RGB")
    im.putpixel((0, 0), (0, 0, 0))
    b = io.BytesIO()
    im.save(b, format="PNG")
    rows = [cma(1), cma(2)]
    tr, _ = transport(rows)
    base = tr.handle_request

    def handler(r):
        return httpx.Response(200, content=b.getvalue()) if r.url.path == "/2.png" else base(r)

    clock = Clock()
    result = collect(
        root,
        "cma",
        "jewelry",
        transport=httpx.MockTransport(handler),
        clock=clock,
        sleep=clock.sleep,
    )
    assert result["images_stored"] == 2 and result["near_duplicate_images"] == 1


@pytest.mark.parametrize(
    "source_id,raw,expected",
    [
        ("cma", cma(rights="Other"), False),
        (
            "aic",
            {"id": 1, "title": "SYNTHETIC ring", "image_id": "abc", "is_public_domain": False},
            False,
        ),
        (
            "aic",
            {
                "id": 1,
                "title": "SYNTHETIC ring",
                "image_id": "abc",
                "alt_image_ids": ["def"],
                "is_public_domain": True,
            },
            True,
        ),
    ],
)
def test_per_item_rights_not_metadata_rights(source_id, raw, expected):
    item = ADAPTERS[source_id]().item(None, raw)
    assert bool(item.photos) == expected
    if expected:
        assert len(item.photos) == 2


@pytest.mark.parametrize(
    "license_name,url,allowed",
    [
        ("CC BY 4.0", "https://creativecommons.org/licenses/by/4.0/", True),
        ("CC0", "https://creativecommons.org/publicdomain/zero/1.0/", True),
        ("CC BY-NC 4.0", "https://creativecommons.org/licenses/by-nc/4.0/", False),
        ("CC0", "https://example.invalid/not-a-license", False),
        ("Unknown", "", False),
    ],
)
def test_commons_license_credit_and_provenance(license_name, url, allowed):
    r = {
        "pageid": 1,
        "title": "File:SYNTHETIC ring",
        "imageinfo": [
            {
                "url": "https://upload.wikimedia.org/x.png",
                "extmetadata": {
                    "LicenseShortName": {"value": license_name},
                    "LicenseUrl": {"value": url},
                    "Artist": {"value": "<b>SYNTHETIC photographer</b>"},
                },
            }
        ],
    }
    item = Commons().item(None, r)
    assert bool(item.photos) == allowed
    if allowed:
        assert item.photos[0].credit == "SYNTHETIC photographer"


def test_met_uses_new_paginated_search_and_preserves_multiple_views():
    class Fake:
        urls = []

        def json(self, url):
            self.urls.append(url)
            if "/search?" in url:
                return {"objectIDs": [1, 2], "total": 3}
            return {
                "objectID": 1,
                "objectURL": "https://www.metmuseum.org/art/1",
                "title": "SYNTHETIC ring",
                "objectName": "Ring",
                "isPublicDomain": True,
                "primaryImage": "https://images.metmuseum.org/one.jpg",
                "additionalImages": ["https://images.metmuseum.org/two.jpg"],
                "medium": "Silver",
                "artistDisplayName": "SYNTHETIC artist",
            }

    f = Fake()
    rows, cursor, done = Met().page(f, "ring", {}, 2)
    assert "/v1.1/search?" in f.urls[0] and cursor == {"offset": 2} and not done
    item = Met().item(f, rows[0])
    assert len(item.photos) == 2
    assert item.assertions[-1].status == "ATTRIBUTED"


def test_all_page_continuations():
    class Fake:
        def __init__(self, reply):
            self.reply = reply

        def json(self, url):
            self.url = url
            return self.reply

    f = Fake({"data": [{"id": 1}], "pagination": {"total_pages": 5}})
    assert AIC().page(f, "ring", {"page": 3}, 10)[1:] == ({"page": 4}, False)
    f = Fake(
        {
            "query": {"pages": {"1": {"pageid": 1}}},
            "continue": {"gsroffset": 20, "continue": "gsroffset||"},
        }
    )
    assert Commons().page(f, "ring", {}, 10)[1:] == (
        {"gsroffset": 20, "continue": "gsroffset||"},
        False,
    )
    f = Fake({"data": [cma()], "info": {"total": 1}})
    assert CMA().page(f, "ring", {}, 10)[2] is True


def test_blocked_and_stale_sources_cannot_collect(root):
    with pytest.raises(ValueError):
        collect(root, "tskies", "ring")
    with connect(root) as c:
        source = get_source(c, "cma")
        source.reviewed_on = "2020-01-01"
        put_source(c, Source.model_validate(source.model_dump()))
    with pytest.raises(ValueError):
        collect(root, "cma", "ring")


def test_seed_never_reenables_excluded_source(root):
    with connect(root) as c:
        exclude(c, source_id="cma")
        seed(c)
        assert not get_source(c, "cma").enabled


def test_lock_and_migration_do_not_replace_data(root, tmp_path):
    with exclusive(root), pytest.raises(BlockingIOError), exclusive(root):
        pass
    other = tmp_path / "not-jri"
    other.mkdir()
    with sqlite3.connect(other / "references.db") as c:
        c.execute("CREATE TABLE important (id INTEGER)")
        c.execute("INSERT INTO important VALUES (7)")
    with pytest.raises(ValueError):
        connect(other)
    with sqlite3.connect(other / "references.db") as c:
        assert c.execute("SELECT * FROM important").fetchall() == [(7,)]


def test_evidence_marks_crops_export_and_exclusion(root):
    run(root, [cma(1), cma(2)])
    with connect(root) as c:
        items = [r[0] for r in c.execute("SELECT id FROM items ORDER BY external_id")]
        asset = c.execute("SELECT id FROM assets WHERE item_id=?", (items[0],)).fetchone()[0]
        add_evidence(
            c,
            items[0],
            {
                "field": "metal_test",
                "value": "Owner reports sterling acid test",
                "origin": "OWNER_REPORTED_TEST",
                "test_method": "ACID",
                "scope": "surface only",
            },
        )
        add_evidence(
            c,
            items[0],
            {
                "field": "mark_transcription",
                "value": "STERLING",
                "origin": "OWNER_INSPECTION",
                "status": "DOCUMENTED",
                "evidence_photo_id": asset,
                "observable_from_photo": True,
            },
        )
        add_evidence(
            c,
            items[0],
            {"field": "maker", "value": "SYNTHETIC prediction", "origin": "MODEL_PREDICTION"},
        )
        add_mark(
            c,
            {
                "item_id": items[0],
                "photo_id": asset,
                "transcription": "STERLING",
                "legibility": "LEGIBLE",
                "candidate_makers": ["SYNTHETIC artist"],
                "origin": "OWNER_INSPECTION",
                "evidence": "SYNTHETIC crop",
            },
        )
        add_pair(
            c,
            {
                "left_item": items[0],
                "right_item": items[1],
                "relation": "DIFFICULT_NEGATIVE",
                "task": "MARK_VARIANT",
                "rationale": "SYNTHETIC similar marks",
                "evidence_refs": ["SYNTHETIC"],
                "reviewer": "synthetic reviewer",
            },
        )
        assert search(c, "STERLING")
    crop_id = crop(root, asset, [0, 0, 80, 80])
    assert crop_id
    manifest = export_dataset(root, "v1")
    assert (
        manifest["count"] == 2 and not manifest["training_ready"] and manifest["pairs_count"] == 1
    )
    rows = [json.loads(x) for x in (root / "exports/v1/examples.jsonl").read_text().splitlines()]
    assert len({r["split"] for r in rows}) == 1
    assert len(rows[0]["visual_training_targets"]) + len(rows[1]["visual_training_targets"]) == 1
    assert all("SYNTHETIC prediction" not in dump(r["evidence_qualified_labels"]) for r in rows)
    with pytest.raises(ValueError):
        export_dataset(root, "v1")
    with connect(root) as c:
        exclude(c, source_id="cma")
        assert not search(c, "ring")
        assert json.loads(c.execute("SELECT data FROM exports WHERE id='v1'").fetchone()[0])[
            "invalidated_at"
        ]
    assert purge(root) > 0
    assert not list((root / "normalized").glob("*.png"))
    assert not (root / "exports/v1/examples.jsonl").exists()


@pytest.mark.parametrize(
    "data",
    [
        {
            "field": "metal",
            "value": "sterling",
            "origin": "MODEL_PREDICTION",
            "status": "DOCUMENTED",
            "document_ref": "x",
        },
        {"field": "metal", "value": "sterling", "origin": "INDEPENDENT_TEST"},
        {"field": "maker", "value": "someone", "origin": "GALLERY", "status": "DOCUMENTED"},
    ],
)
def test_assertions_cannot_promote_unsupported_evidence(data):
    with pytest.raises(ValidationError):
        Assertion.model_validate(data)


def test_fair_use_manifest_is_collectable_without_becoming_a_license(root, tmp_path):
    with connect(root) as c:
        source = Source(
            id="synthetic-gallery",
            name="SYNTHETIC",
            url="https://gallery.example",
            terms_url="https://gallery.example/terms",
            reviewed_on=datetime.now(UTC).date(),
            classification="INTERNAL_FAIR_USE",
            access_method="REVIEWED_MANIFEST",
            hosts=["gallery.example"],
            enabled=True,
            restrictions="SYNTHETIC no identified restriction",
            rationale="SYNTHETIC owner fair-use analysis, not legal clearance",
            provenance="synthetic review",
        )
        put_source(c, source)
    item = Item(
        external_id="1",
        page_url="https://gallery.example/1",
        title="SYNTHETIC ring",
        photos=[
            {
                "url": "https://gallery.example/1.png",
                "license": "claimed CC0",
                "license_url": "x",
                "evidence": "synthetic",
            }
        ],
    )
    path = tmp_path / "manifest.jsonl"
    path.write_text(item.model_dump_json() + "\n")

    def handler(r):
        return (
            httpx.Response(404)
            if r.url.path == "/robots.txt"
            else httpx.Response(200, content=png())
        )

    clock = Clock()
    result = import_manifest(
        root,
        source.id,
        path,
        transport=httpx.MockTransport(handler),
        clock=clock,
        sleep=clock.sleep,
    )
    assert result["images_stored"] == 1
    with connect(root) as c:
        assert (
            json.loads(c.execute("SELECT data FROM assets").fetchone()[0])["rights"]["license"]
            == "UNLICENSED_INTERNAL_FAIR_USE"
        )
    assert export_dataset(root, "open-only")["count"] == 0
    assert export_dataset(root, "internal", include_fair_use=True)["count"] == 1
    with connect(root) as c:
        source.classification = "OPEN_ACCESS"
        source.rights_claim = "SYNTHETIC new review applies to future images"
        put_source(c, source)
    assert export_dataset(root, "after-source-review")["count"] == 0
    assert export_dataset(root, "historic-fair-use", include_fair_use=True)["count"] == 1
    exported = json.loads((root / "exports/historic-fair-use/examples.jsonl").read_text())
    assert exported["images"][0]["rights"]["license"] == "UNLICENSED_INTERNAL_FAIR_USE"


def test_many_successive_batches_are_not_globally_capped(root):
    rows = [cma(i) for i in range(1, 351)]
    total = 0
    for _ in range(7):
        result, _ = run(root, rows, max_images=50, max_items=50, page_size=50)
        total += result["images_stored"]
    assert total == 350
    with connect(root) as c:
        assert c.execute("SELECT count(*) FROM items").fetchone()[0] == 350


@pytest.mark.parametrize(
    "title,kind,expected",
    [
        ("Portrait with necklace", "Painting", False),
        ("Gold ring", "Ring", True),
        ("SYNTHETIC vase", "Ceramic", False),
        ("SYNTHETIC turquoise cuff", "Jewelry", True),
    ],
)
def test_relevance_is_not_a_random_artwork_search(title, kind, expected):
    assert (
        relevant(
            Item(
                external_id="1", page_url="https://example.invalid/1", title=title, object_type=kind
            )
        )
        is expected
    )


def test_manifest_resumes_beyond_initial_batch_and_pins_file_revision(root, tmp_path):
    with connect(root) as c:
        source = get_source(c, "cma")
        source.access_method = "REVIEWED_MANIFEST"
        put_source(c, source)
    path = tmp_path / "reviewed.jsonl"
    rows = [CMA().item(None, cma(i)).model_dump(mode="json") for i in range(1, 5)]
    path.write_text("".join(dump(r) + "\n" for r in rows))
    clock = Clock()
    tr, calls = transport([])
    for _ in range(2):
        result = import_manifest(
            root,
            "cma",
            path,
            max_items=2,
            max_images=2,
            transport=tr,
            clock=clock,
            sleep=clock.sleep,
        )
        assert result["images_stored"] == 2
    result = import_manifest(root, "cma", path, transport=tr, clock=clock, sleep=clock.sleep)
    assert result["status"] == "EXHAUSTED"
    assert sum("/1.png" in url for url in calls) == 1
    with connect(root) as c:
        assert c.execute("SELECT count(*) FROM items").fetchone()[0] == 4


def test_item_with_multiple_views_resumes_without_retransferring_first(root, monkeypatch):
    base = CMA.item

    def two_views(self, http, raw):
        item = base(self, http, raw)
        item.photos.append(
            item.photos[0].model_copy(
                update={"url": "https://openaccess-cdn.clevelandart.org/back.png"}
            )
        )
        return item

    monkeypatch.setattr(CMA, "item", two_views)
    first, _ = run(root, [cma()], max_images=1)
    assert first["items_processed"] == 0 and first["images_stored"] == 1
    second, calls = run(root, [cma()], max_images=1)
    assert second["images_stored"] == 1 and not any("/1.png" in u for u in calls)


def test_native_origin_or_sterling_claim_is_not_automatic_truth(root):
    row = cma()
    row["technique"] = "STERLING 925 TAXCO MEXICO ALPACA GERMAN SILVER; seller claims Native origin"
    run(root, [row])
    with connect(root) as c:
        assert search(c, "GERMAN SILVER")
        data = [json.loads(r["data"]) for r in c.execute("SELECT data FROM assertions")]
        assert all(r["status"] == "ATTRIBUTED" and r["confidence"] == "UNKNOWN" for r in data)
        assert all(r["test_method"] is None for r in data)
    assert export_dataset(root, "unverified")["count"] == 1
    row = json.loads((root / "exports/unverified/examples.jsonl").read_text())
    assert row["visual_training_targets"] == []


def test_owner_correction_history_is_append_only(root):
    run(root, [cma()])
    with connect(root) as c:
        item = c.execute("SELECT id FROM items").fetchone()[0]
        old = c.execute("SELECT id,data FROM assertions ORDER BY id LIMIT 1").fetchone()
        add_evidence(
            c,
            item,
            {
                "field": "stated_material",
                "value": "Owner acid test is inconclusive",
                "origin": "OWNER_REPORTED_TEST",
                "test_method": "ACID",
                "supersedes": old["id"],
            },
        )
        assert (
            c.execute("SELECT data FROM assertions WHERE id=?", (old["id"],)).fetchone()[0]
            == old["data"]
        )
    export_dataset(root, "corrected")
    row = json.loads((root / "exports/corrected/examples.jsonl").read_text())
    assert any(a["id"] == old["id"] for a in row["assertion_history"])
    assert all(a["id"] != old["id"] for a in row["evidence_qualified_labels"])


def test_artist_disjoint_export_does_not_leak_shared_artist(root):
    rows = [cma(i) for i in range(1, 5)]
    tr, _ = transport(rows)
    base = tr.handle_request

    def handler(req):
        if req.url.host == "openaccess-cdn.clevelandart.org" and req.url.path != "/robots.txt":
            return httpx.Response(200, content=png(int(req.url.path[1:-4])))
        return base(req)

    clock = Clock()
    collect(
        root,
        "cma",
        "jewelry",
        transport=httpx.MockTransport(handler),
        clock=clock,
        sleep=clock.sleep,
    )
    export_dataset(root, "artist-holdout")
    output = [
        json.loads(line)
        for line in (root / "exports/artist-holdout/examples.jsonl").read_text().splitlines()
    ]
    assert len({r["split_group"] for r in output}) == 1
    assert len({r["split"] for r in output}) == 1
    assert len({p["hash"] for r in output for p in r["images"]}) == 4


def test_blob_validation_does_not_trust_format_size_or_filename():
    im = Image.new("RGB", (20, 20))
    b = io.BytesIO()
    im.save(b, format="PNG")
    with pytest.raises(InvalidImage, match="IMAGE_DIMENSIONS"):
        normalize(b.getvalue())
    im = Image.new("RGB", (100, 100))
    b = io.BytesIO()
    im.save(b, format="GIF")
    with pytest.raises(InvalidImage, match="IMAGE_FORMAT"):
        normalize(b.getvalue())
    with pytest.raises(InvalidImage):
        normalize(png()[:500])


def test_network_budget_stops_without_advancing_queue(root):
    result, calls = run(root, [cma()], max_requests=1)
    assert result["error"] == "BATCH_NETWORK_LIMIT" and result["pages"] == 0
    assert len(calls) == 1


def test_cli_requires_live_and_does_not_initialize_or_contact_sources(
    tmp_path, monkeypatch, capsys
):
    import sys

    from radar.references.cli import main

    monkeypatch.setattr(sys, "argv", ["jri", "--root", str(tmp_path / "private"), "collect", "cma"])
    with pytest.raises(SystemExit) as e:
        main()
    assert e.value.code == 2
    assert not (tmp_path / "private").exists()
    assert "requires --live" in capsys.readouterr().err


def test_private_store_refuses_public_static_location():
    from radar.references.store import private_root

    root = Path(__file__).resolve().parents[1] / "radar/static/jri-forbidden"
    with pytest.raises(ValueError):
        private_root(root)
    assert not root.exists()
