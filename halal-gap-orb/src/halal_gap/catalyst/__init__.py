"""Stage 2: LLM catalyst classifier."""
from halal_gap.catalyst.llm_classifier import PROMPT_VERSION, classify
from halal_gap.catalyst.models import (
    CatalystFeature,
    CatalystType,
    ClassifierOutput,
    ClassifierResult,
    Direction,
    NewsItem,
)
from halal_gap.catalyst.news_fetcher import fetch_news_before
from halal_gap.catalyst.pipeline import (
    classify_candidate,
    classify_candidates,
    decision_time,
    feature_for,
    passes_catalyst_gate,
)

__all__ = [
    "PROMPT_VERSION",
    "CatalystFeature",
    "CatalystType",
    "ClassifierOutput",
    "ClassifierResult",
    "Direction",
    "NewsItem",
    "classify",
    "classify_candidate",
    "classify_candidates",
    "decision_time",
    "feature_for",
    "fetch_news_before",
    "passes_catalyst_gate",
]
