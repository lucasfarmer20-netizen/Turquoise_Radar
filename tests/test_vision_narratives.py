"""Long synthetic reports through the real SDK, service, storage and templates."""

import asyncio
import copy
import json
from html.parser import HTMLParser

import httpx2
import pytest
from anthropic import AsyncAnthropic
from pydantic import ValidationError
from sqlalchemy import select
from test_haiku_allowance import current_test_profile
from test_vision import new_case, submit, upload
from test_vision import no_paid_or_image_network as no_paid_or_image_network

from radar.db import Listing, VisionFinding, VisionPrice, VisionRun, VisionSubItem
from radar.jewelry.config import VisionConfig
from radar.jewelry.errors import MAX_RESPONSE_BYTES
from radar.jewelry.schemas import SCHEMA_VERSION, VisionResult, guarded_text
from radar.jewelry.validation import validation_diagnostics
from radar.vision import SYSTEM_PROMPT, AnthropicVisionProvider

NARRATIVE_PATHS = [
    ("summary",),
    ("visible_southwestern_style",),
    ("hallmark_candidate",),
    ("findings", 0, "description"),
    ("findings", 0, "visible_evidence"),
    ("findings", 0, "uncertainty"),
    ("findings", 0, "next_action"),
    ("possible_maker_identity", "research_name"),
    ("possible_maker_identity", "uncertainty"),
    ("sub_items", 0, "label"),
    ("sub_items", 0, "location_hint"),
    ("sub_items", 0, "uncertainty"),
]


def at(data, path):
    for part in path:
        data = data[part]
    return data


def put(data, path, value):
    at(data, path[:-1])[path[-1]] = value


def report(photo_id="a" * 32):
    return {
        "classification": "MIXED_LOT",
        "summary": "SYNTHETIC cautious report.",
        "visible_southwestern_style": "Visible decorative wire.",
        "hallmark_candidate": "Visible letters require comparison.",
        "findings": [
            {
                "key": "mark",
                "category": "METAL",
                "description": "SYNTHETIC visible letters.",
                "image_id": photo_id,
                "visible_evidence": "Letters AB are visible.",
                "confidence": "LOW",
                "evidence_kind": "DIRECT",
                "uncertainty": "Unverified.",
                "next_action": "Request an independent inspection.",
                "signal": "POSSIBLE_HALLMARK",
                "legibility": "LEGIBLE",
                "hallmark_transcription": "AB",
                "visible_fragments": [],
            }
        ],
        "possible_maker_identity": {
            "research_name": "SYNTHETIC maker research lead",
            "supporting_finding_key": "mark",
            "uncertainty": "A research lead only; identity is unverified.",
        },
        "sub_items": [
            {
                "key": "foreground",
                "label": "SYNTHETIC foreground ornament",
                "image_id": photo_id,
                "location_hint": "Foreground",
                "finding_keys": ["mark"],
                "uncertainty": "Unverified.",
            }
        ],
    }


def long_prose(marker):
    return (
        f"SYNTHETIC_PRIVATE_REPORT_{marker}\n\n"
        + "The visible surface has wear; composition remains unverified. " * 35
        + '\n<script>window.NARRATIVE_XSS=true</script><img src="https://example.invalid/pixel">'
        + "\nUnicode: é Ω 💎. "
        + "x" * 900
        + f" END_{marker}"
    )


def long_report(photo_id="a" * 32):
    data = report(photo_id)
    for index, path in enumerate(NARRATIVE_PATHS):
        put(data, path, long_prose(str(index)))
    return data


@pytest.mark.parametrize("path", NARRATIVE_PATHS)
def test_every_narrative_field_accepts_long_text_verbatim(path):
    data = report()
    text = long_prose("FIELD")
    put(data, path, text)
    result = VisionResult.model_validate_json(json.dumps(data))
    result.validate_images({"a" * 32})
    assert at(result.model_dump(), path) == text


@pytest.mark.parametrize("path", NARRATIVE_PATHS)
def test_long_text_does_not_hide_a_late_unsafe_claim(path):
    data = report()
    put(data, path, long_prose("FIELD") + " This is sterling silver.")
    with pytest.raises(ValidationError) as exc:
        VisionResult.model_validate(data)
    details = validation_diagnostics(exc.value)
    assert any(d["rule_id"] == "NO_VERIFIED_MATERIAL_CLAIM" for d in details)
    assert "SYNTHETIC_PRIVATE_REPORT" not in json.dumps(details)


@pytest.mark.parametrize(
    "claim",
    [
        "Weighs 12.5 grams.",
        "Worth $100.",
        "This piece is authenticated.",
        "Possibly Navajo.",
        "Natural turquoise.",
        "Made by Alice.",
    ],
)
def test_long_summary_retains_substantive_claim_guards(claim):
    data = report()
    data["summary"] = long_prose("GUARD") + " " + claim
    with pytest.raises(ValidationError):
        VisionResult.model_validate(data)


@pytest.mark.parametrize(
    "problem",
    [
        "partial_mark",
        "generic_maker",
        "foreign_photo",
        "unknown_finding",
        "classification",
        "confidence",
    ],
)
def test_long_report_retains_evidence_and_reference_checks(problem):
    data = long_report()
    if problem == "partial_mark":
        data["findings"][0]["legibility"] = "PARTIAL"
    if problem == "generic_maker":
        data["findings"][0]["hallmark_transcription"] = "925"
    if problem == "foreign_photo":
        data["findings"][0]["image_id"] = "b" * 32
    if problem == "unknown_finding":
        data["sub_items"][0]["finding_keys"] = ["unknown"]
    if problem == "classification":
        data["classification"] = "INCONCLUSIVE"
    if problem == "confidence":
        data["findings"][0].update(evidence_kind="INFERRED", confidence="HIGH")
    with pytest.raises(ValidationError):
        result = VisionResult.model_validate(data)
        result.validate_images({"a" * 32})


def test_large_prose_has_no_hidden_presentation_limit_and_still_scans_all_claims():
    text = "This piece is not authenticated. " * 15000
    data = report()
    data["summary"] = text
    assert len(json.dumps(data).encode()) < MAX_RESPONSE_BYTES
    assert VisionResult.model_validate(data).summary == text
    with pytest.raises(ValueError):
        guarded_text(text + " This is sterling silver.")


class Rendered(HTMLParser):
    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.text = []
        self.tags = []
        self.feed(html)

    def handle_data(self, data):
        self.text.append(data)

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


def configure(client):
    config = VisionConfig(pilot_one_shot=True, api_key="SYNTHETIC_TEST_KEY")
    client.app.state.vision_config_factory = lambda: config
    profile = current_test_profile()
    with client.app.state.sessions.begin() as s:
        s.add(VisionPrice(model=profile.model, data=profile.model_dump(mode="json")))
    case = new_case(client)
    photos = upload(client, case, count=1)
    return config, case, photos


def submit_mock_report(client, config, case, photos, data):
    calls = []

    async def handler(request):
        wire = json.loads(request.content)
        assert wire["max_tokens"] == 32768 and wire["service_tier"] == "standard_only"
        assert wire["system"] == SYSTEM_PROMPT
        assert not any(k in wire for k in ["thinking", "effort", "speed", "tools"])
        assert "effort" not in wire["output_config"]
        calls.append(wire)
        return httpx2.Response(
            200,
            headers={"request-id": "req_SYNTHETIC_LONG_REPORT"},
            json={
                "id": "msg_SYNTHETIC",
                "type": "message",
                "role": "assistant",
                "model": config.model,
                "stop_reason": "end_turn",
                "content": [{"type": "text", "text": json.dumps(data)}],
                "usage": {
                    "input_tokens": 8000,
                    "output_tokens": 8000,
                    "output_tokens_details": {"thinking_tokens": 1000},
                },
            },
        )

    sdk = AsyncAnthropic(
        api_key="SYNTHETIC_TEST_KEY",
        max_retries=0,
        open_telemetry=False,
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
    )
    adapter = AnthropicVisionProvider("SYNTHETIC_TEST_KEY", client=sdk)
    client.app.state.vision_provider_factory = lambda *_: adapter
    try:
        response = submit(
            client,
            case,
            photos,
            provider="anthropic",
            paid_confirm="yes",
            approved_model=config.model,
            approved_amount="0.581920",
        )
        assert len(calls) == 1
        return response
    finally:
        asyncio.run(sdk.close())


def test_complete_long_report_sdk_persistence_rendering_and_privacy(client, caplog):
    config, case, photos = configure(client)
    data = long_report(photos[0].id)
    expected = VisionResult.model_validate(data).model_dump(mode="json")
    with client.app.state.sessions() as s:
        original_listing = copy.deepcopy(s.scalar(select(Listing)).data)
    response = submit_mock_report(client, config, case, photos, data)
    assert response.status_code == 200
    parsed = Rendered(response.text)
    text = "".join(parsed.text)
    for path in NARRATIVE_PATHS:
        assert at(expected, path) in text
    assert (
        sum(
            tag == "details" and attrs.get("class") == "vision-long-text"
            for tag, attrs in parsed.tags
        )
        == 12
    )
    assert not any("example.invalid" in attrs.get("src", "") for _, attrs in parsed.tags)
    assert "<script>window.NARRATIVE_XSS=true</script>" not in response.text
    assert "SYNTHETIC_PRIVATE_REPORT" not in caplog.text
    with client.app.state.sessions() as s:
        row = s.scalar(select(VisionRun))
        assert row.status == "SUCCESS" and row.data["result"] == expected
        assert row.data["schema_version"] == SCHEMA_VERSION == "3"
        assert row.data["prompt_version"] == "jewelry-3"
        assert s.scalar(select(VisionFinding)).data == expected["findings"][0]
        assert s.scalar(select(VisionSubItem)).data == expected["sub_items"][0]
        assert s.scalar(select(Listing)).data == original_listing
        assert row.data["response_metadata"]["stop_reason"] == "end_turn"
        assert "SYNTHETIC_PRIVATE_REPORT" not in json.dumps(row.data["response_metadata"])
    assert client.get(f"/static/{photos[0].id}.png").status_code == 404


@pytest.mark.parametrize("problem", ["unsafe", "secret"])
def test_invalid_long_report_remains_private_and_held(client, caplog, problem):
    config, case, photos = configure(client)
    data = long_report(photos[0].id)
    data["summary"] += " This is sterling silver." if problem == "unsafe" else " SYNTHETIC_TEST_KEY"
    response = submit_mock_report(client, config, case, photos, data)
    assert response.status_code == 200
    with client.app.state.sessions() as s:
        row = s.scalar(select(VisionRun))
        assert row.status == "FAILED" and row.data["result"] is None
        assert row.cost_status == "HELD_UNKNOWN" and row.accounted_usd == "0.581920"
        assert not list(s.scalars(select(VisionFinding)))
        assert not list(s.scalars(select(VisionSubItem)))
        assert "SYNTHETIC_PRIVATE_REPORT" not in json.dumps(row.data)
        assert "SYNTHETIC_TEST_KEY" not in json.dumps(row.data)
    assert "SYNTHETIC_PRIVATE_REPORT" not in response.text + caplog.text
