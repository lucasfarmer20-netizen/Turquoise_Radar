"""Output-allowance regressions using synthetic images, databases and HTTP only."""

import asyncio
import copy
import json
from datetime import UTC, datetime
from decimal import Decimal
from html import unescape
from pathlib import Path

import httpx2
import pytest
from anthropic import AsyncAnthropic
from pydantic import ValidationError
from sqlalchemy import select
from test_vision import FakeProvider, new_case, picture, submit, upload
from test_vision import no_paid_or_image_network as no_paid_or_image_network

from radar.db import VisionCase, VisionPrice, VisionRun
from radar.jewelry.budget import reserve_amount, usage_cost
from radar.jewelry.config import HAIKU_QUICK_MAX_TOKENS, VisionConfig
from radar.jewelry.errors import MAX_RESPONSE_BYTES
from radar.jewelry.schemas import PriceProfile
from radar.jewelry.service import cache_identity, plan
from radar.vision import AnthropicVisionProvider, FixtureVisionProvider, VisionImage


def reviewed_profile():
    return PriceProfile.model_validate_json(
        Path("docs/anthropic/2026-10-30/claude-haiku-5-5.price-profile.json").read_text()
    )


def current_test_profile():
    # Fresh synthetic copy for deterministic route tests; never refresh owner evidence.
    return reviewed_profile().model_copy(
        update={"verified_on": datetime.now(UTC).date(), "conditions": "SYNTHETIC profile test"}
    )


@pytest.mark.parametrize("limit", [2048, 8192, 32768, 32769])
def test_price_profile_output_bounds_preserve_historical_profiles(limit):
    data = reviewed_profile().model_dump()
    data["max_output_tokens"] = limit
    if limit > 32768:
        with pytest.raises(ValidationError):
            PriceProfile.model_validate(data)
    else:
        assert PriceProfile.model_validate(data).max_output_tokens == limit


def test_reviewed_haiku_reservation_and_thinking_usage_accounting():
    profile = reviewed_profile()
    assert profile.max_output_tokens == HAIKU_QUICK_MAX_TOKENS == 32768
    assert profile.max_input_tokens == 1000000
    assert (profile.input_per_million, profile.output_per_million) == (
        Decimal("0.50"),
        Decimal("2.50"),
    )
    assert reserve_amount(profile) == Decimal("0.581920")
    assert usage_cost({"input_tokens": 1000000, "output_tokens": 32768}, profile) == Decimal(
        "0.581920"
    )
    usage = {
        "input_tokens": 7926,
        "output_tokens": 32768,
        "output_tokens_details": {"thinking_tokens": 1872},
    }
    assert usage_cost(usage, profile) == Decimal("0.085883")
    assert usage_cost({**usage, "output_tokens": 32769}, profile) is None
    provenance = json.loads(
        Path("docs/anthropic/2026-10-30/claude-haiku-5-5.pricing.json").read_text()
    )
    assert provenance["reservation_usd"] == "0.581920"
    assert provenance["limits"]["application_max_output_tokens"] == 32768
    sonnet = PriceProfile.model_validate_json(
        Path("docs/anthropic/2026-10-30/claude-sonnet-5-5.price-profile.json").read_text()
    )
    assert not sonnet.model_access_verified and sonnet.max_output_tokens == 2048
    assert MAX_RESPONSE_BYTES == 2 * 1024 * 1024


def test_profile_append_changes_quote_and_cache_without_rewriting_history(client):
    config = VisionConfig()
    page = client.get("/vision/settings")
    assert '"max_output_tokens": 32768' in unescape(page.text)
    with client.app.state.sessions() as s:
        assert plan(s, config, "QUICK", "anthropic")["output_limit"] == 32768
        assert plan(s, config, "DEEP", "anthropic")["output_limit"] == 2048
    case_id = new_case(client)
    new = current_test_profile()
    old = new.model_copy(update={"max_output_tokens": 2048})
    with client.app.state.sessions.begin() as s:
        old_price = VisionPrice(model=old.model, data=old.model_dump(mode="json"))
        old_run = VisionRun(
            case_id=case_id,
            provider="anthropic",
            model=old.model,
            stage="QUICK",
            status="FAILED",
            cache_key="SYNTHETIC_OLD_AUTHORIZATION",
            reserved_usd="0.505120",
            accounted_usd="0.505120",
            cost_status="HELD_UNKNOWN",
            data={
                "authorization_id": "SYNTHETIC_CONSUMED",
                "price_profile": old.model_dump(mode="json"),
            },
        )
        s.add_all([old_price, old_run])
        s.flush()
        old_id, run_id = old_price.id, old_run.id
        saved_data = copy.deepcopy(old_run.data)
        old_quote = plan(s, config, "QUICK", "anthropic")
        assert old_quote["output_limit"] == 2048
    assert (
        client.post(
            "/vision/settings/pricing", data={"confirm": "yes", "payload": new.model_dump_json()}
        ).status_code
        == 200
    )
    with client.app.state.sessions() as s:
        quote = plan(s, config, "QUICK", "anthropic")
        assert quote["output_limit"] == 32768 and quote["reservation"] == Decimal("0.581920")
        assert any("Paid vision is disabled" in reason for reason in quote["reasons"])
        assert s.get(VisionPrice, old_id).data == old.model_dump(mode="json")
        run = s.get(VisionRun, run_id)
        assert run.data == saved_data and run.accounted_usd == run.reserved_usd == "0.505120"
        assert run.status == "FAILED" and run.cost_status == "HELD_UNKNOWN"
        pilot = config.model_copy(update={"pilot_one_shot": True})
        assert any("already consumed" in r for r in plan(s, pilot, "QUICK", "anthropic")["reasons"])
        assert plan(s, pilot, "DEEP", "anthropic")["reasons"]
        case = s.get(VisionCase, case_id)
        assert cache_identity(case, "anthropic", "QUICK", old_quote, [], {}) != cache_identity(
            case, "anthropic", "QUICK", quote, [], {}
        )


def test_sdk_request_changes_only_max_tokens_preserving_thinking_and_schema():
    async def check():
        images = [VisionImage("a" * 32, "FRONT", picture())]
        fixture = await FixtureVisionProvider().inspect(
            images, {}, model="fixture", stage="QUICK", max_tokens=2048
        )
        requests = []

        async def handler(request):
            requests.append(json.loads(request.content))
            return httpx2.Response(
                200,
                headers={"request-id": "req_SYNTHETIC_ALLOWANCE"},
                json={
                    "id": "msg_SYNTHETIC",
                    "type": "message",
                    "role": "assistant",
                    "model": "claude-haiku-5-5",
                    "stop_reason": "end_turn",
                    "content": [{"type": "text", "text": fixture.result.model_dump_json()}],
                    "usage": {
                        "input_tokens": 100,
                        "output_tokens": 512,
                        "output_tokens_details": {"thinking_tokens": 100},
                    },
                },
            )

        async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as http:
            sdk = AsyncAnthropic(
                api_key="SYNTHETIC_TEST_KEY", http_client=http, max_retries=0, open_telemetry=False
            )
            adapter = AnthropicVisionProvider("SYNTHETIC_TEST_KEY", client=sdk)
            for limit in (2048, reviewed_profile().max_output_tokens):
                response = await adapter.inspect(
                    images, {}, model="claude-haiku-5-5", stage="QUICK", max_tokens=limit
                )
                assert response.result == fixture.result
            assert sdk.max_retries == 0
        before, after = requests
        assert before.pop("max_tokens") == 2048
        assert after.pop("max_tokens") == 32768
        assert before == after
        assert "thinking" not in after and "effort" not in after["output_config"]
        assert not any(k in after for k in ["speed", "tools", "cache_control"])
        assert after["service_tier"] == "standard_only"

    asyncio.run(check())


def test_new_allowance_requires_matching_approval_and_budget_and_holds_on_failure(client):
    config = VisionConfig(pilot_one_shot=True, api_key="SYNTHETIC_TEST_KEY")
    client.app.state.vision_config_factory = lambda: config
    profile = current_test_profile()
    assert (
        client.post(
            "/vision/settings/pricing",
            data={"confirm": "yes", "payload": profile.model_dump_json()},
        ).status_code
        == 200
    )
    case = new_case(client)
    photos = upload(client, case, count=1)
    calls = []

    class Failed(FakeProvider):
        async def inspect(self, images, context, **kwargs):
            calls.append(kwargs)
            with client.app.state.sessions() as s:
                row = s.scalar(select(VisionRun))
                assert row.reserved_usd == row.accounted_usd == "0.581920"
            raise TimeoutError("SYNTHETIC timeout")

    client.app.state.vision_provider_factory = lambda *_: Failed()
    fields = dict(provider="anthropic", paid_confirm="yes", approved_model=config.model)
    assert submit(client, case, photos, approved_amount="0.505120", **fields).status_code == 422
    assert not calls
    with client.app.state.sessions() as s:
        assert not s.scalar(select(VisionRun))
    config.monthly_budget = Decimal("0.55")  # Fits the old allowance, not the new one.
    assert submit(client, case, photos, approved_amount="0.581920", **fields).status_code == 422
    assert not calls
    with client.app.state.sessions() as s:
        assert not s.scalar(select(VisionRun))
    config.monthly_budget = Decimal("5.00")
    assert submit(client, case, photos, approved_amount="0.581920", **fields).status_code == 200
    assert calls == [{"model": config.model, "stage": "QUICK", "max_tokens": 32768}]
    with client.app.state.sessions() as s:
        row = s.scalar(select(VisionRun))
        assert row.status == "FAILED" and row.cost_status == "HELD_UNKNOWN"
        assert row.accounted_usd == "0.581920"
    assert submit(client, case, photos, approved_amount="0.581920", **fields).status_code == 422
    assert len(calls) == 1
