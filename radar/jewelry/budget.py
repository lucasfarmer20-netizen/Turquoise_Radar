from datetime import UTC, datetime
from decimal import ROUND_CEILING, Decimal

from sqlalchemy import select

from radar.db import VisionPrice, VisionRun
from radar.jewelry.schemas import OWNER, PriceProfile


def money(value):
    return Decimal(value).quantize(Decimal("0.000001"), rounding=ROUND_CEILING)


def profile_for(session, model):
    row = session.scalar(
        select(VisionPrice).where(VisionPrice.model == model).order_by(VisionPrice.id.desc())
    )
    if not row:
        return None
    profile = PriceProfile.model_validate(row.data)
    age = (datetime.now(UTC).date() - profile.verified_on).days
    return profile if 0 <= age <= 30 and profile.model_access_verified else None


def reserve_amount(profile):
    # Full documented input-context ceiling, not an optimistic image-token guess.
    return money(
        (
            profile.max_input_tokens * profile.input_per_million
            + profile.max_output_tokens * profile.output_per_million
        )
        / Decimal(1000000)
    )


def budget_state(session, config, now=None):
    now = now or datetime.now(UTC)
    runs = list(
        session.scalars(
            select(VisionRun).where(VisionRun.owner_id == OWNER, VisionRun.provider == "anthropic")
        )
    )
    day = sum(r.created_at.date() == now.date() for r in runs)
    monthly = sum(
        (
            Decimal(r.accounted_usd)
            for r in runs
            if r.created_at.strftime("%Y-%m") == now.strftime("%Y-%m")
            or r.status in {"RESERVED", "SENT"}
            or r.cost_status == "HELD_UNKNOWN"
        ),
        Decimal(0),
    )
    return dict(
        daily_used=day,
        daily_limit=config.daily_limit,
        monthly_accounted=monthly,
        monthly_budget=config.monthly_budget,
        remaining=max(Decimal(0), config.monthly_budget - monthly),
        period="UTC calendar day/month; older unresolved reservations carry forward",
    )


def usage_cost(usage, profile):
    def count(name):
        value = usage.get(name)
        if type(value) is not int or value < 0:
            raise ValueError("Token usage unavailable")
        return value

    try:
        input_ = count("input_tokens")
        output = count("output_tokens")
        cache_read = usage.get("cache_read_input_tokens") or 0
        cache_write = usage.get("cache_creation_input_tokens") or 0
        if any(type(v) is not int or v < 0 for v in [cache_read, cache_write]):
            return None
        if (
            input_ + cache_read + cache_write > profile.max_input_tokens
            or output > profile.max_output_tokens
        ):
            return None
        if usage.get("service_tier") not in (None, "standard") or usage.get("speed") not in (
            None,
            "standard",
        ):
            return None
        if usage.get("inference_geo") not in (None, "global", "not_available"):
            return None  # No region-dependent pricing condition is assumed covered.
        if usage.get("server_tool_use") and any(usage["server_tool_use"].values()):
            return None  # Tools are never requested; unexpected paid dimensions stay unknown.
        total = input_ * profile.input_per_million + output * profile.output_per_million
        if cache_read:
            if profile.cache_read_per_million is None:
                return None
            total += cache_read * profile.cache_read_per_million
        if cache_write:
            breakdown = usage.get("cache_creation") or {}
            five = breakdown.get("ephemeral_5m_input_tokens", 0)
            hour = breakdown.get("ephemeral_1h_input_tokens", 0)
            if any(type(v) is not int or v < 0 for v in [five, hour]) or five + hour != cache_write:
                return None
            if (five and profile.cache_write_5m_per_million is None) or (
                hour and profile.cache_write_1h_per_million is None
            ):
                return None
            total += five * (profile.cache_write_5m_per_million or 0) + hour * (
                profile.cache_write_1h_per_million or 0
            )
        return money(total / Decimal(1000000))
    except (ValueError, TypeError, KeyError):
        return None
