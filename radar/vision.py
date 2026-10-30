from abc import ABC, abstractmethod

from pydantic import BaseModel, Field, HttpUrl


class VisionObservation(BaseModel):
    visible_hallmark_text: str | None = None
    construction_notes: list[str] = Field(default_factory=list)
    stone_appearance: str | None = None
    factory_warning_signs: list[str] = Field(default_factory=list)
    possible_maker: str | None = None
    supporting_evidence: list[str] = Field(default_factory=list)
    uncertainty: str


class VisionProvider(ABC):
    """No paid API calls. Use individual full-resolution photos; never infer tribal
    authenticity, stone mine origin, or numeric weight from appearance.
    """

    @abstractmethod
    async def inspect(self, images: list[HttpUrl]) -> VisionObservation:
        pass
