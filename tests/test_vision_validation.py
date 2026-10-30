"""Synthetic regressions only: no owner image, provider body or external network."""

import asyncio
import copy
import gzip
import json

import httpx
import httpx2
import pytest
from anthropic import APIResponseValidationError, AsyncAnthropic
from pydantic import ValidationError
from pydantic_core import PydanticCustomError
from sqlalchemy import select
from test_vision import new_case, pilot_test_config, submit, upload

from radar.db import VisionFinding, VisionRun
from radar.jewelry.errors import (
    MAX_RESPONSE_BYTES,
    FailureTrace,
    ResponseTooLargeError,
    classify_failure,
    numeric_usage,
)
from radar.jewelry.schemas import Finding, VisionResult, guarded_text
from radar.jewelry.validation import validation_diagnostics
from radar.vision import SYSTEM_PROMPT, AnthropicVisionProvider, VisionImage


@pytest.fixture(autouse=True)
def no_external_network(monkeypatch):
    for name in (
        "VISION_PROVIDER",
        "VISION_LIVE_ENABLED",
        "VISION_PILOT_ONE_SHOT",
        "ANTHROPIC_API_KEY",
        "VISION_IMAGE_DIR",
    ):
        monkeypatch.delenv(name, raising=False)

    async def reject_async(*args, **kwargs):
        raise AssertionError("No external HTTP allowed")

    def reject(*args, **kwargs):
        raise AssertionError("No external HTTP allowed")

    for library in (httpx, httpx2):
        monkeypatch.setattr(library.AsyncHTTPTransport, "handle_async_request", reject_async)
        monkeypatch.setattr(library.HTTPTransport, "handle_request", reject)


def finding(**updates):
    return dict(
        key="mark",
        category="METAL",
        description="Silver-colored surface shows wear.",
        image_id="a" * 32,
        visible_evidence="A yellow-toned patch is visible at the edge.",
        confidence="LOW",
        evidence_kind="INFERRED",
        uncertainty="Composition is unverified.",
        signal="PLATING_CONCERN",
        **updates,
    )


def payload():
    return dict(
        classification="CUFF",
        summary="Silver-colored cuff with blue cabochon.",
        visible_southwestern_style="Decorative wire around the bezel.",
        hallmark_candidate="UNKNOWN: back view needed.",
        findings=[finding()],
    )


def error_details(data):
    with pytest.raises(ValidationError) as exc:
        VisionResult.model_validate(data)
    return validation_diagnostics(exc.value)


@pytest.mark.parametrize(
    "text",
    [
        "This piece is not authenticated.",
        "Not authenticated.",
        "This cannot be authenticated from a photograph.",
        "Cannot determine whether this is sterling from the photograph.",
        "Whether the metal is genuine sterling cannot be verified from appearance alone.",
        "No tribal attribution can be established from this photo.",
        "Tribal attribution cannot be supported from the image.",
        "Cannot determine who this was made by.",
        "Who this was made by cannot be determined from the photograph.",
        "Metal content is unverified; plating cannot be ruled out.",
        "Yellow-toned metal may be exposed under the silver-colored surface.",
        "This piece is not authenticated; independent inspection is needed.",
    ],
)
def test_cautious_observations_preserved_verbatim(text):
    assert guarded_text(text) == text
    data = payload()
    data["findings"][0]["uncertainty"] = text
    data["summary"] = text
    result = VisionResult.model_validate(data)
    assert result.summary == result.findings[0].uncertainty == text


@pytest.mark.parametrize(
    "text,rule",
    [
        ("This is sterling silver.", "NO_VERIFIED_MATERIAL_CLAIM"),
        ("The cuff is sterling-plated.", "NO_VERIFIED_MATERIAL_CLAIM"),
        ("This piece is not authenticated, but it is sterling.", "NO_VERIFIED_MATERIAL_CLAIM"),
        ("Not authenticated and definitely sterling.", "NO_AUTHENTICATION_CLAIM"),
        ("Not only authenticated.", "NO_AUTHENTICATION_CLAIM"),
        ("It is not true that this is not authenticated.", "NO_AUTHENTICATION_CLAIM"),
        ("This piece is not not authenticated.", "NO_AUTHENTICATION_CLAIM"),
        ("This piece is authenticated.", "NO_AUTHENTICATION_CLAIM"),
        (
            "No tribal attribution can be established. Authentic Navajo piece.",
            "NO_CULTURAL_ATTRIBUTION",
        ),
        ("Possibly Navajo.", "NO_CULTURAL_ATTRIBUTION"),
        ("No Hopi attribution can be established.", "NO_CULTURAL_ATTRIBUTION"),
        ("Possibly made by Alice.", "NO_MAKER_AUTHENTICATION"),
        ("Cannot determine who this was made by. Made by Alice.", "NO_MAKER_AUTHENTICATION"),
        ("Cannot determine whether this is sterling; weighs 12.5 grams.", "NO_INVENTED_WEIGHT"),
        ("Not authenticated, worth $100.", "NO_INVENTED_VALUE"),
        ("Value is 30 USD.", "NO_INVENTED_VALUE"),
        ("Mass is 1.2 dwt.", "NO_INVENTED_WEIGHT"),
        ("Natural turquoise.", "NO_STONE_TREATMENT_CLAIM"),
        ("Could be Kingman turquoise.", "NO_MINE_ATTRIBUTION"),
        ("This is\nsterling silver.", "NO_VERIFIED_MATERIAL_CLAIM"),
        ("Natural\nturquoise.", "NO_STONE_TREATMENT_CLAIM"),
    ],
)
def test_negation_never_shields_positive_claims_or_hard_bans(text, rule):
    data = payload()
    data["findings"][0]["description"] = text
    details = error_details(data)
    assert details[0]["path"] == ["findings", 0, "description"]
    assert details[0]["rule_id"] == rule and details[0]["kind"] == "safety"
    assert text not in json.dumps(details)


@pytest.mark.parametrize("fragment", ["A&", "O'", "1/", "+", "?", "AB-", "9?5"])
def test_partial_marks_support_bounded_literal_punctuation(fragment):
    values = {
        **finding(),
        "category": "METAL",
        "evidence_kind": "DIRECT",
        "legibility": "PARTIAL",
        "visible_fragments": [fragment],
        "hallmark_transcription": None,
    }
    assert Finding.model_validate(values).visible_fragments == [fragment]


@pytest.mark.parametrize(
    "updates,rule",
    [
        ({"evidence_kind": "INFERRED", "confidence": "HIGH"}, "INFERRED_CONFIDENCE_LIMIT"),
        (
            {"legibility": "PARTIAL", "hallmark_transcription": "925"},
            "HALLMARK_DIRECT_LEGIBLE_METAL",
        ),
        (
            {"legibility": "LEGIBLE", "hallmark_transcription": "925"},
            "HALLMARK_DIRECT_LEGIBLE_METAL",
        ),
        (
            {
                "legibility": "LEGIBLE",
                "evidence_kind": "DIRECT",
                "category": "STONE",
                "hallmark_transcription": "925",
            },
            "HALLMARK_DIRECT_LEGIBLE_METAL",
        ),
        (
            {"legibility": "LEGIBLE", "evidence_kind": "DIRECT", "hallmark_transcription": "925?"},
            "HALLMARK_CHARACTER_FORMAT",
        ),
        ({"visible_fragments": ["<script>"]}, "FRAGMENT_CHARACTER_FORMAT"),
        ({"visible_fragments": ["ABCDEFGHIJK"]}, "FRAGMENT_CHARACTER_FORMAT"),
    ],
)
def test_evidence_conflicts_remain_rejected(updates, rule):
    data = payload()
    data["findings"][0].update(updates)
    assert error_details(data)[0]["rule_id"] == rule


@pytest.mark.parametrize(
    "mark",
    [
        "925",
        "STERLING",
        "SILVER",
        "14K",
        "18KT",
        "1/20 12K GF",
        "GOLD FILLED",
        "EPNS",
        "SILVER PLATED",
        "S925",
        "925S",
    ],
)
def test_generic_material_mark_is_not_maker_evidence(mark):
    data = payload()
    data["findings"][0].update(
        evidence_kind="DIRECT", legibility="LEGIBLE", hallmark_transcription=mark
    )
    data["possible_maker_identity"] = {
        "research_name": "SYNTHETIC Alice",
        "supporting_finding_key": "mark",
        "uncertainty": "This piece is not authenticated.",
    }
    assert error_details(data)[0]["rule_id"] == "GENERIC_MARK_NOT_MAKER_EVIDENCE"


def test_maker_lead_requires_actual_mark_and_stays_unverified():
    data = payload()
    data["possible_maker_identity"] = {
        "research_name": "SYNTHETIC Alice",
        "supporting_finding_key": "mark",
        "uncertainty": "This piece is not authenticated.",
    }
    assert error_details(data)[0]["rule_id"] == "MAKER_REQUIRES_LEGIBLE_MARK"
    data["findings"][0].update(
        evidence_kind="DIRECT", legibility="LEGIBLE", hallmark_transcription="AB 925"
    )
    result = VisionResult.model_validate(data)
    assert result.possible_maker_identity.status == "UNVERIFIED_RESEARCH_LEAD"


@pytest.mark.parametrize(
    "problem,rule",
    [
        ("duplicate", "UNIQUE_FINDING_AND_SUBITEM_KEYS"),
        ("missing_finding", "KNOWN_FINDING_REFERENCE"),
        ("wrong_image", "AUTHORIZED_PHOTO_REFERENCE"),
        ("cross_image", "SUBITEM_EVIDENCE_SAME_PHOTO"),
    ],
)
def test_mixed_lot_reference_errors_are_actionable(problem, rule):
    data = payload()
    data["classification"] = "MIXED_LOT"
    data["sub_items"] = [
        {
            "key": "foreground",
            "label": "Foreground cuff",
            "image_id": "a" * 32,
            "location_hint": "Lower left",
            "finding_keys": ["mark"],
            "uncertainty": "Unverified.",
        }
    ]
    if problem == "duplicate":
        data["findings"].append(copy.deepcopy(data["findings"][0]))
    if problem == "missing_finding":
        data["sub_items"][0]["finding_keys"] = ["unknown"]
    if problem == "wrong_image":
        data["findings"][0]["image_id"] = "c" * 32
    if problem == "cross_image":
        data["sub_items"][0]["image_id"] = "b" * 32
    with pytest.raises(ValidationError) as exc:
        result = VisionResult.model_validate(data)
        result.validate_images({"a" * 32, "b" * 32})
    details = validation_diagnostics(exc.value)
    assert details[0]["rule_id"] == rule and details[0]["kind"] == "references"


def test_mixed_lot_valid_references_and_no_rewriting():
    data = payload()
    data["classification"] = "MIXED_LOT"
    data["sub_items"] = [
        {
            "key": "foreground",
            "label": "Foreground cuff",
            "image_id": "a" * 32,
            "location_hint": "Lower left",
            "finding_keys": ["mark"],
            "uncertainty": "Unverified.",
        }
    ]
    result = VisionResult.model_validate(data)
    result.validate_images({"a" * 32})
    assert result.sub_items[0].finding_keys == ["mark"]
    assert result.findings[0].description == data["findings"][0]["description"]


@pytest.mark.parametrize(
    "problem,rule",
    [
        ("empty_summary", "FIELD_LENGTH"),
        ("long_hallmark", "FIELD_LENGTH"),
        ("required", "REQUIRED_FIELD"),
        ("unknown_class", "ENUM_VALUE"),
        ("bad_id", "FIELD_PATTERN"),
        ("extra", "STRICT_FIELDS"),
        ("material_status", "UNVERIFIED_STATUS_REQUIRED"),
        ("too_many", "COLLECTION_LENGTH"),
    ],
)
def test_format_and_status_constraints_are_not_relaxed(problem, rule):
    data = payload()
    if problem == "empty_summary":
        data["summary"] = ""
    if problem == "long_hallmark":
        data["findings"][0]["hallmark_transcription"] = "A" * 41
    if problem == "required":
        del data["summary"]
    if problem == "unknown_class":
        data["classification"] = "INCONCLUSIVE"
    if problem == "bad_id":
        data["findings"][0]["image_id"] = "PRIVATE_FIELD"
    if problem == "extra":
        data["PRIVATE_FIELD"] = "PRIVATE_VALUE"
    if problem == "material_status":
        data["material_verification"] = "VERIFIED"
    if problem == "too_many":
        data["findings"] = data["findings"] * 21
    details = error_details(data)
    assert any(x["rule_id"] == rule for x in details)
    assert "PRIVATE_FIELD" not in json.dumps(details) and "PRIVATE_VALUE" not in json.dumps(details)


def test_prompt_names_valid_unknown_classification_and_version_changes():
    from radar.jewelry.schemas import PROMPT_VERSION, SCHEMA_VERSION

    assert "classification UNKNOWN" in SYSTEM_PROMPT and "INCONCLUSIVE" not in SYSTEM_PROMPT
    assert PROMPT_VERSION == "jewelry-3" and SCHEMA_VERSION == "3"


def test_diagnostic_unknown_types_context_messages_and_paths_cannot_leak():
    error = ValidationError.from_exception_data(
        "PRIVATE_TITLE",
        [
            {
                "type": PydanticCustomError(
                    "PRIVATE_CODE", "PRIVATE_MESSAGE", {"private": "PRIVATE_CONTEXT"}
                ),
                "loc": ("findings", 0, "PRIVATE_FIELD"),
                "input": "PRIVATE_INPUT",
            },
            {
                "type": "string_type",
                "loc": ("possible_maker_identity", "PRIVATE_FIELD"),
                "input": "PRIVATE_INPUT",
            },
        ],
    )
    details = validation_diagnostics(error)
    assert "PRIVATE_" not in json.dumps(details)
    assert details[0] == {
        "path": ["findings", 0, "$unknown"],
        "validation_type": "unknown_validation_type",
        "rule_id": "UNCLASSIFIED_VALIDATION",
        "kind": "unknown",
    }
    assert set(details[0]) == {"path", "validation_type", "rule_id", "kind"}


def test_usage_is_numeric_bounded_and_does_not_coerce_input():
    assert numeric_usage(
        {
            "input_tokens": "123",
            "output_tokens": True,
            "cache_read_input_tokens": -1,
            "cache_creation_input_tokens": 2**60,
            "PRIVATE_FIELD": "PRIVATE_VALUE",
            "output_tokens_details": {"thinking_tokens": 12, "raw": "PRIVATE_TEXT"},
        }
    ) == {"output_tokens_details": {"thinking_tokens": 12}}


def test_wrapped_validation_and_diagnostic_bounds():
    error = ValidationError.from_exception_data(
        "PRIVATE_TITLE",
        [{"type": "missing", "loc": ("summary",), "input": "PRIVATE_INPUT"}] * 40,
    )
    wrapped = APIResponseValidationError(
        response=httpx2.Response(
            200,
            request=httpx2.Request("POST", "https://example.invalid/v1/messages"),
            headers={"request-id": "req_SYNTHETIC"},
        ),
        body={"PRIVATE_BODY": "PRIVATE_TEXT"},
    )
    wrapped.__cause__ = error
    details = classify_failure(wrapped)
    assert details["category"] == "INVALID_RESULT"
    assert len(details["validation_errors"]) == 32
    assert details["validation_errors"][0]["path"] == ["summary"]
    assert "PRIVATE_" not in json.dumps(details)


@pytest.mark.parametrize("compressed", [False, True])
def test_sdk_accepts_caution_from_same_buffered_response_without_rewriting(compressed):
    async def check():
        data = payload()
        data["summary"] = "This piece is not authenticated."
        calls = []

        class Chunks(httpx2.AsyncByteStream):
            async def __aiter__(self):
                raw = json.dumps(
                    {
                        "id": "msg_SYNTHETIC",
                        "type": "message",
                        "role": "assistant",
                        "model": "claude-haiku-5-5",
                        "content": [{"type": "text", "text": json.dumps(data)}],
                        "usage": {"input_tokens": 20, "output_tokens": 30},
                        "stop_reason": "end_turn",
                    }
                ).encode()
                if compressed:
                    raw = gzip.compress(raw)
                yield raw[:100]
                yield raw[100:]

        async def handler(request):
            calls.append(1)
            return httpx2.Response(
                200,
                headers={
                    "request-id": "req_SYNTHETIC_SUCCESS",
                    **({"content-encoding": "gzip"} if compressed else {}),
                },
                stream=Chunks(),
            )

        async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as http:
            sdk = AsyncAnthropic(
                api_key="SYNTHETIC_TEST_KEY",
                http_client=http,
                max_retries=0,
                open_telemetry=False,
            )
            adapter = AnthropicVisionProvider("SYNTHETIC_TEST_KEY", client=sdk)
            response = await adapter.inspect(
                [VisionImage("a" * 32, "FRONT", b"SYNTHETIC_MOCK_ONLY")],
                {},
                model="claude-haiku-5-5",
                stage="QUICK",
                max_tokens=2048,
            )
            assert response.result.summary == data["summary"]
            assert adapter.response_metadata["usage"] == {"input_tokens": 20, "output_tokens": 30}
            assert adapter.response_metadata["stop_reason"] == "end_turn"
            assert calls == [1]
            assert not http.event_hooks["response"] and not http.event_hooks["request"]

    asyncio.run(check())


@pytest.mark.parametrize(
    "problem", ["unsafe", "too_long", "truncated", "wrong_reference", "incomplete", "oversize"]
)
def test_failed_parse_retains_only_safe_http_usage_and_keeps_hold(client, caplog, problem):
    cfg = pilot_test_config(client)
    case = new_case(client)
    photos = upload(client, case, count=1)
    data = payload()
    data["findings"][0]["image_id"] = photos[0].id
    if problem == "unsafe":
        data["findings"][0]["description"] = "This is sterling silver."
    if problem == "too_long":
        data["findings"][0]["hallmark_transcription"] = "PRIVATE_TEXT" * 80
    if problem == "wrong_reference":
        data["findings"][0]["image_id"] = "f" * 32
    calls = []

    async def handler(request):
        calls.append(1)
        if problem == "oversize":

            class Oversize(httpx2.AsyncByteStream):
                async def __aiter__(self):
                    for _ in range(40):
                        yield b"x" * (64 * 1024)

            return httpx2.Response(
                200, headers={"request-id": "req_SYNTHETIC_INVALID"}, stream=Oversize()
            )
        return httpx2.Response(
            200,
            headers={"request-id": "req_SYNTHETIC_INVALID"},
            json={
                "id": "msg_PRIVATE_VALUE",
                "type": "message",
                "role": "assistant",
                "model": "claude-haiku-5-5",
                "content": [
                    {
                        "type": "text",
                        "text": '{"PRIVATE_TEXT":' if problem == "truncated" else json.dumps(data),
                    }
                ],
                "stop_reason": "max_tokens"
                if problem in {"truncated", "incomplete"}
                else "end_turn",
                "stop_sequence": "PRIVATE_STOP",
                "PRIVATE_FIELD": "PRIVATE_ENVELOPE",
                "usage": {
                    "input_tokens": 7884,
                    "output_tokens": 2048 if problem in {"truncated", "incomplete"} else 300,
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": 0,
                    "PRIVATE_USAGE": "PRIVATE_VALUE",
                    "output_tokens_details": {"thinking_tokens": 0},
                },
            },
        )

    http = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
    sdk = AsyncAnthropic(
        api_key="SYNTHETIC_TEST_KEY", http_client=http, max_retries=0, open_telemetry=False
    )
    adapter = AnthropicVisionProvider("SYNTHETIC_TEST_KEY", client=sdk)
    client.app.state.vision_provider_factory = lambda *_: adapter
    fields = dict(
        provider="anthropic", paid_confirm="yes", approved_model=cfg.model, approved_amount="1"
    )
    assert submit(client, case, photos, **fields).status_code == 200
    with client.app.state.sessions() as s:
        row = s.scalar(select(VisionRun))
        d = row.data["failure_diagnostics"]
        meta = row.data["response_metadata"]
        assert row.status == "FAILED" and row.cost_status == "HELD_UNKNOWN"
        assert row.accounted_usd == row.reserved_usd and row.estimated_usd is None
        assert row.confirmed_usd is None and row.data["result"] is None
        assert not list(s.scalars(select(VisionFinding)))
        assert d["http_status"] == 200
        assert d["category"] == (
            "RESPONSE_TOO_LARGE" if problem == "oversize" else "INVALID_RESULT"
        )
        assert meta["request_id"] == "req_SYNTHETIC_INVALID"
        assert row.data["usage"] == meta["usage"]
        if problem == "oversize":
            assert meta["usage"] == {} and meta["stop_reason"] is None
            assert d["exception_class"] == "ResponseTooLargeError"
            assert "validation_errors" not in d
        else:
            assert meta["usage"]["input_tokens"] == 7884
            assert (
                d["validation_errors"][0]["rule_id"]
                == {
                    "unsafe": "NO_VERIFIED_MATERIAL_CLAIM",
                    "too_long": "FIELD_LENGTH",
                    "truncated": "VALID_JSON",
                    "wrong_reference": "AUTHORIZED_PHOTO_REFERENCE",
                    "incomplete": "COMPLETE_STRUCTURED_RESPONSE",
                }[problem]
            )
        assert meta["stop_reason"] == (
            "max_tokens"
            if problem in {"truncated", "incomplete"}
            else "end_turn"
            if problem != "oversize"
            else None
        )
        assert d["failure_stage"] == (
            "POST_RESPONSE_VALIDATION"
            if problem in {"wrong_reference", "incomplete"}
            else "RESPONSE_PARSING"
        )
        assert "PRIVATE_" not in json.dumps(row.data)
        assert "SYNTHETIC_TEST_KEY" not in json.dumps(row.data)
    assert (
        submit(client, case, photos, force="yes", force_confirm="yes", **fields).status_code == 422
    )
    assert calls == [1] and "PRIVATE_" not in caplog.text
    asyncio.run(sdk.close())


@pytest.mark.parametrize("status", [200, 500])
@pytest.mark.parametrize("mode", ["absent", "lying", "invalid", "advertised", "gzip"])
def test_response_limit_stops_stream_before_buffering_and_closes(status, mode):
    async def check():
        consumed = []
        closed = []

        class Chunks(httpx2.AsyncByteStream):
            async def __aiter__(self):
                if mode == "gzip":
                    consumed.append(1)
                    yield gzip.compress(b"x" * (MAX_RESPONSE_BYTES * 4))
                else:
                    for _ in range(100):
                        consumed.append(1)
                        yield b"x" * (64 * 1024)
                raise AssertionError("Must stop reading an oversized response")

            async def aclose(self):
                closed.append(1)

        headers = {"request-id": "req_SYNTHETIC_SIZE"}
        if mode in {"lying", "invalid", "advertised"}:
            headers["content-length"] = {
                "lying": "1",
                "invalid": "PRIVATE_INVALID",
                "advertised": str(MAX_RESPONSE_BYTES + 1),
            }[mode]
        if mode == "gzip":
            headers["content-encoding"] = "gzip"
        response = httpx2.Response(status, headers=headers, stream=Chunks())
        trace = FailureTrace("SYNTHETIC_SECRET")
        with pytest.raises(ResponseTooLargeError) as exc:
            await trace.response(response)
        assert len(consumed) == (0 if mode == "advertised" else 1 if mode == "gzip" else 33)
        assert closed == [1] and response.is_closed
        with pytest.raises(httpx2.ResponseNotRead):
            _ = response.content
        diagnostic = trace.classify(exc.value)
        assert diagnostic["category"] == "RESPONSE_TOO_LARGE"
        assert diagnostic["http_status"] == status
        assert diagnostic["failure_stage"] == (
            "RESPONSE_PARSING" if status == 200 else "HTTP_RESPONSE"
        )
        assert trace.response_metadata["usage"] == {}
        assert "PRIVATE_" not in json.dumps(diagnostic)

    asyncio.run(check())


@pytest.mark.parametrize("buffered", [False, True])
@pytest.mark.parametrize("extra", [0, 1])
def test_response_limit_exact_boundary_and_prebuffered_responses(buffered, extra):
    async def check():
        raw = b"{}" + b" " * (MAX_RESPONSE_BYTES - 2 + extra)

        class Chunks(httpx2.AsyncByteStream):
            async def __aiter__(self):
                for index in range(0, len(raw), 64 * 1024):
                    yield raw[index : index + 64 * 1024]

        response = httpx2.Response(200, **({"content": raw} if buffered else {"stream": Chunks()}))
        trace = FailureTrace("")
        if extra:
            with pytest.raises(ResponseTooLargeError):
                await trace.response(response)
        else:
            await trace.response(response)
            assert await response.aread() == raw
            assert response.json() == {}
        assert response.is_closed

    asyncio.run(check())


def test_safe_response_metadata_discards_text_stop_reason_and_request_id():
    async def check():
        trace = FailureTrace("SYNTHETIC_SECRET")
        response = httpx2.Response(
            200,
            headers={"request-id": "req_SYNTHETIC_SECRET"},
            json={
                "usage": {"input_tokens": 4, "output_tokens": 5, "private": "PRIVATE_TEXT"},
                "stop_reason": "PRIVATE_TEXT",
                "content": [{"text": "PRIVATE_TEXT"}],
            },
        )
        await trace.response(response)
        assert trace.response_metadata == {
            "http_status": 200,
            "request_id": None,
            "usage": {"input_tokens": 4, "output_tokens": 5},
            "stop_reason": None,
        }

    asyncio.run(check())
