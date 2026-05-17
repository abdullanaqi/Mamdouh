"""Catalyst module tests: news filter, cost tracker, classifier cache, gate."""
from __future__ import annotations

import json
import os
from datetime import date, datetime, time, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from halal_gap.catalyst import (
    CatalystFeature,
    ClassifierOutput,
    ClassifierResult,
    NewsItem,
    classify,
    decision_time,
    feature_for,
    passes_catalyst_gate,
)
from halal_gap.catalyst.cost_tracker import (
    PRICING_PER_1K,
    can_spend,
    estimate_cost,
    record_spend,
    reset,
    spent_today,
)
from halal_gap.catalyst.news_fetcher import _parse_news_df, fetch_news_before
from halal_gap.scanner.gap_scanner import GapCandidate

NY = ZoneInfo("America/New_York")


# ---------- cost tracker ----------------------------------------------------

def test_estimate_cost_known_model() -> None:
    cost = estimate_cost("gpt-4o-mini", in_tokens=1000, out_tokens=1000)
    assert cost == pytest.approx(PRICING_PER_1K["gpt-4o-mini"]["in"] + PRICING_PER_1K["gpt-4o-mini"]["out"])


def test_estimate_cost_unknown_falls_back() -> None:
    a = estimate_cost("does-not-exist", 1000, 1000)
    b = estimate_cost("gpt-4o-mini", 1000, 1000)
    assert a == pytest.approx(b)


def test_record_and_can_spend_round_trip() -> None:
    d = date(1999, 1, 1)
    reset(d)
    assert spent_today(d) == 0.0
    assert can_spend(d)
    record_spend(0.5, d)
    record_spend(0.25, d)
    assert spent_today(d) == pytest.approx(0.75)
    reset(d)


def test_can_spend_blocks_at_cap() -> None:
    d = date(1999, 1, 2)
    reset(d)
    # Default cap = 5.0, push over.
    record_spend(10.0, d)
    assert not can_spend(d)
    reset(d)


# ---------- news fetcher ----------------------------------------------------

def test_parse_news_df_handles_press_release_shape() -> None:
    df = pd.DataFrame(
        [
            {"date": "2024-01-22 08:00:00", "title": "Earnings beat", "text": "Strong quarter."},
            {"date": "2024-01-22 07:00:00", "title": "Dividend declared", "text": ""},
        ]
    )
    items = _parse_news_df(df, "press_release")
    assert len(items) == 2
    assert items[0].source == "press_release"
    assert items[0].title == "Earnings beat"


def test_parse_news_df_drops_empty() -> None:
    items = _parse_news_df(pd.DataFrame(), "news")
    assert items == []
    df = pd.DataFrame([{"publishedDate": "2024-01-01", "title": "", "text": ""}])
    items = _parse_news_df(df, "news")
    assert items == []


@pytest.mark.asyncio
async def test_fetch_news_before_drops_lookahead() -> None:
    """News at or after as_of_ts must be filtered out."""
    as_of = datetime(2024, 1, 22, 9, 25, tzinfo=NY)

    class FakeFMP:
        async def stock_news(self, *a, **k):
            return pd.DataFrame(
                [
                    {"publishedDate": "2024-01-22 08:30:00", "title": "Pre-market positive", "text": "OK"},
                    {"publishedDate": "2024-01-22 09:30:00", "title": "FORBIDDEN look-ahead", "text": "NO"},
                    {"publishedDate": "2024-01-22 09:25:00", "title": "AT cutoff", "text": "Borderline"},
                ]
            )

        async def press_releases(self, *a, **k):
            return pd.DataFrame()

    items = await fetch_news_before(FakeFMP(), "AAPL", as_of)  # type: ignore[arg-type]
    titles = [i.title for i in items]
    assert "Pre-market positive" in titles
    assert "FORBIDDEN look-ahead" not in titles
    assert "AT cutoff" not in titles


@pytest.mark.asyncio
async def test_fetch_news_before_lookback_window() -> None:
    """News older than lookback_hours must be dropped."""
    as_of = datetime(2024, 1, 22, 9, 25, tzinfo=NY)

    class FakeFMP:
        async def stock_news(self, *a, **k):
            old = (as_of - timedelta(days=5)).strftime("%Y-%m-%d %H:%M:%S")
            recent = (as_of - timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S")
            return pd.DataFrame(
                [
                    {"publishedDate": recent, "title": "recent", "text": "x"},
                    {"publishedDate": old, "title": "stale", "text": "x"},
                ]
            )

        async def press_releases(self, *a, **k):
            return pd.DataFrame()

    items = await fetch_news_before(FakeFMP(), "AAPL", as_of, lookback_hours=18)  # type: ignore[arg-type]
    titles = [i.title for i in items]
    assert "recent" in titles
    assert "stale" not in titles


# ---------- classifier ------------------------------------------------------

def _news(*titles: str, source: str = "news") -> list[NewsItem]:
    out = []
    for i, t in enumerate(titles):
        out.append(
            NewsItem(
                published_at=datetime(2024, 1, 22, 8, 30 - i, tzinfo=NY),
                title=t,
                body=t,
                source=source,
            )
        )
    return out


def test_classify_no_items_returns_skipped() -> None:
    r = classify("AAPL", datetime(2024, 1, 22, 9, 25, tzinfo=NY), gap_pct=0.04, items=[])
    assert r.skipped_reason == "no_news"
    assert r.output.catalyst_type == "none"
    assert r.cost_usd == 0.0


def test_classify_no_api_key_uses_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    r = classify(
        "AAPL",
        datetime(2024, 1, 22, 9, 25, tzinfo=NY),
        gap_pct=0.04,
        items=_news("Apple beats earnings estimates"),
    )
    assert r.skipped_reason == "no_api_key"
    assert r.output.direction == "bullish"
    assert r.cost_usd == 0.0


def test_classify_hits_disk_cache(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """A second call with the same items should be served from cache."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    items = _news("Apple raises guidance")
    a = classify("AAPL", datetime(2024, 1, 22, 9, 25, tzinfo=NY), 0.04, items)
    b = classify("AAPL", datetime(2024, 1, 22, 9, 25, tzinfo=NY), 0.04, items)
    assert b.cached is True
    assert b.output.direction == a.output.direction


def test_classify_cost_cap_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "fake-key-for-test")
    d = date(1999, 1, 3)
    reset(d)
    record_spend(1000.0, d)
    items = _news("Major M&A deal announced")
    # Use a unique title so it doesn't hit the cache from prior tests
    items[0] = NewsItem(
        published_at=datetime(1999, 1, 3, 8, 0, tzinfo=NY),
        title="UNIQUE-COST-CAP-MARKER-19990103",
        body="x",
        source="news",
    )
    r = classify(
        "AAPL",
        datetime(1999, 1, 3, 9, 25, tzinfo=NY),
        0.05,
        items,
    )
    assert r.skipped_reason == "cost_cap"
    reset(d)


# ---------- pipeline gate + feature ----------------------------------------

def _result(direction: str, strength: int, confidence: float, ctype: str = "earnings") -> ClassifierResult:
    return ClassifierResult(
        symbol="X",
        as_of=datetime(2024, 1, 22, 9, 25, tzinfo=NY),
        prompt_version="v1",
        item_count=1,
        output=ClassifierOutput(
            direction=direction, strength=strength, confidence=confidence,
            catalyst_type=ctype, summary="t",
        ),
    )


def test_gate_accepts_strong_bullish() -> None:
    assert passes_catalyst_gate(_result("bullish", 4, 0.8))


def test_gate_rejects_weak_bullish() -> None:
    assert not passes_catalyst_gate(_result("bullish", 2, 0.8))


def test_gate_rejects_bearish() -> None:
    assert not passes_catalyst_gate(_result("bearish", 5, 0.9))


def test_gate_rejects_low_confidence() -> None:
    assert not passes_catalyst_gate(_result("bullish", 5, 0.3))


def test_gate_blocks_no_news() -> None:
    r = _result("neutral", 1, 0.5, "none")
    r.skipped_reason = "no_news"
    assert not passes_catalyst_gate(r)


def test_gate_passes_through_cost_cap() -> None:
    r = _result("neutral", 1, 0.5, "none")
    r.skipped_reason = "cost_cap"
    assert passes_catalyst_gate(r)  # don't block trades when budget is gone


def test_feature_from_result_signs_direction() -> None:
    bull = feature_for(_result("bullish", 4, 0.8))
    assert bull.direction_score == 1.0
    bear = feature_for(_result("bearish", 4, 0.8))
    assert bear.direction_score == -1.0
    neut = feature_for(_result("neutral", 1, 0.5, "none"))
    assert neut.direction_score == 0.0
    assert neut.has_catalyst is False  # type "none" and / or no items


# ---------- decision_time helper -------------------------------------------

def test_decision_time_is_925_ny() -> None:
    cand = GapCandidate(
        symbol="X",
        as_of=date(2024, 1, 22),
        prior_close=100.0,
        premarket_last=104.0,
        gap_pct=0.04,
        premarket_dollar_volume=10_000_000.0,
        premarket_rvol=3.0,
    )
    t = decision_time(cand)
    assert t.tzinfo is not None
    assert (t.hour, t.minute) == (9, 25)
