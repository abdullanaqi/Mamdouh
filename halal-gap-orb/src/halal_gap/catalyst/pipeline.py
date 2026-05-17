"""End-to-end orchestration: GapCandidate -> news fetch -> LLM -> feature row."""
from __future__ import annotations

from datetime import datetime, time

from halal_gap.catalyst.llm_classifier import classify
from halal_gap.catalyst.models import CatalystFeature, ClassifierResult, NewsItem
from halal_gap.catalyst.news_fetcher import fetch_news_before
from halal_gap.data.fmp_client import FMPClient
from halal_gap.scanner.gap_scanner import GapCandidate
from halal_gap.utils.config import settings
from halal_gap.utils.logging import log
from halal_gap.utils.time_helpers import at_ny


def decision_time(cand: GapCandidate) -> datetime:
    """The no-look-ahead decision cutoff for a candidate: 09:25 ET on its date."""
    return at_ny(cand.as_of, time(9, 25))


async def classify_candidate(
    cand: GapCandidate,
    fmp: FMPClient,
    *,
    news_override: list[NewsItem] | None = None,
) -> ClassifierResult:
    """Fetch news (or accept an override) and classify a single candidate."""
    as_of_ts = decision_time(cand)
    items = (
        news_override
        if news_override is not None
        else await fetch_news_before(fmp, cand.symbol, as_of_ts)
    )
    return classify(cand.symbol, as_of_ts, cand.gap_pct, items)


async def classify_candidates(
    candidates: list[GapCandidate], fmp: FMPClient
) -> list[ClassifierResult]:
    """Classify a batch of candidates serially (cache + cost cap make parallelism
    less valuable; OpenAI rate limits also make it safer)."""
    out: list[ClassifierResult] = []
    for c in candidates:
        out.append(await classify_candidate(c, fmp))
    return out


def passes_catalyst_gate(result: ClassifierResult) -> bool:
    """The Stage-2 gate: only accept long candidates with a credible bull catalyst.

    Thresholds come from config.llm.min_strength. We require:
        - direction bullish (long-only system)
        - strength >= min_strength
        - confidence >= 0.5
    Skipped-cost-cap results are treated as a pass-through (do not block trades);
    skipped-no-news results are blocked because they argue the gap is unexplained.
    """
    cfg = settings()["llm"]
    if result.skipped_reason == "cost_cap":
        log.warning(f"{result.symbol} catalyst gate: cost cap -> default to PASS")
        return True
    if result.skipped_reason == "no_news":
        return False
    out = result.output
    return (
        out.direction == "bullish"
        and out.strength >= int(cfg["min_strength"])
        and out.confidence >= 0.5
    )


def feature_for(result: ClassifierResult) -> CatalystFeature:
    """Project a classifier result into the row consumed by the ML scoring layer."""
    return CatalystFeature.from_result(result)
