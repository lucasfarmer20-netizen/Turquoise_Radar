import os
from decimal import Decimal
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr


class VisionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: Literal["fixture", "anthropic"] = "fixture"
    live_enabled: bool = False
    model: str = "claude-haiku-5-5"
    deep_model: str = "claude-sonnet-5-5"
    api_key: SecretStr = Field(default_factory=lambda: SecretStr(""), repr=False)
    daily_limit: int = Field(default=10, ge=0, le=1000)
    monthly_budget: Decimal = Field(default=Decimal("5.00"), ge=0, le=10000, allow_inf_nan=False)
    timeout: float = Field(default=45, ge=1, le=120)

    @classmethod
    def from_env(cls):
        return cls(
            provider=os.getenv("VISION_PROVIDER", "fixture"),
            live_enabled=os.getenv("VISION_LIVE_ENABLED", "false").lower() == "true",
            model=os.getenv("VISION_MODEL", "claude-haiku-5-5"),
            deep_model=os.getenv("VISION_DEEP_MODEL", "claude-sonnet-5-5"),
            api_key=os.getenv("ANTHROPIC_API_KEY", ""),
            daily_limit=os.getenv("VISION_DAILY_LIMIT", "10"),
            monthly_budget=os.getenv("VISION_MONTHLY_BUDGET_USD", "5.00"),
        )


def image_root(engine):
    configured = os.getenv("VISION_IMAGE_DIR")
    database = Path(engine.url.database or "data/radar.db")
    root = Path(configured) if configured else database.parent / (database.stem + "-vision-images")
    root = root.absolute()
    static = Path(__file__).resolve().parents[1] / "static"
    if root.resolve().is_relative_to(static):
        raise ValueError("Vision storage cannot be public static storage")
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    root.chmod(0o700)
    return root.resolve()
