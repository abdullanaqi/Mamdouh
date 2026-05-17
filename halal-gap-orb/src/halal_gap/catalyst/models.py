"""Pydantic models for catalyst classifications and per-symbol feature rows."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

Direction = Literal["bullish", "bearish", "neutral"]
CatalystType = Literal[
    "earnings",
    "guidance",
    "analyst",
    "fda",
    "mna",
    "contract",
    "product",
    "legal",
    "macro",
    "other",
    "none",
]


class NewsItem(BaseModel):
    """One news article or press release returned by FMP."""

    published_at: datetime
    title: str
    body: str
    source: str  # "news" | "press_release"
    url: str | None = None


class ClassifierOutput(BaseModel):
    """The LLM's structured response. The JSON schema is enforced server-side."""

    direction: Direction = Field(description="Net directional read of catalyst.")
    strength: int = Field(ge=1, le=5, description="Catalyst impact, 1=trivial 5=major.")
    confidence: float = Field(ge=0.0, le=1.0, description="Model self-rated confidence.")
    catalyst_type: CatalystType
    summary: str = Field(max_length=400)


class ClassifierResult(BaseModel):
    """Persisted classification: the LLM output plus provenance + cost."""

    symbol: str
    as_of: datetime
    prompt_version: str
    item_count: int
    output: ClassifierOutput
    cached: bool = False
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    skipped_reason: str | None = None  # "cost_cap", "no_news", "no_api_key"


class CatalystFeature(BaseModel):
    """Per (symbol, as_of) feature row consumed by the ML scoring layer."""

    symbol: str
    as_of: datetime
    has_catalyst: bool
    direction_score: float  # bullish=+1, neutral=0, bearish=-1
    strength: int
    confidence: float
    catalyst_type: CatalystType
    item_count: int

    @classmethod
    def from_result(cls, r: ClassifierResult) -> "CatalystFeature":
        sign = {"bullish": 1.0, "neutral": 0.0, "bearish": -1.0}[r.output.direction]
        return cls(
            symbol=r.symbol,
            as_of=r.as_of,
            has_catalyst=r.output.catalyst_type != "none" and r.item_count > 0,
            direction_score=sign,
            strength=r.output.strength,
            confidence=r.output.confidence,
            catalyst_type=r.output.catalyst_type,
            item_count=r.item_count,
        )
