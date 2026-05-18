"""Feature builder + dataset assembly tests."""
from __future__ import annotations

from datetime import date, time
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from halal_gap.catalyst.models import CatalystFeature, ClassifierOutput, ClassifierResult
from halal_gap.features.builder import (
    CATALYST_TYPES,
    build_feature_row,
    feature_columns,
)
from halal_gap.features.dataset import build_dataset
from halal_gap.scanner.gap_scanner import GapCandidate
from halal_gap.strategy.orb import OpeningRange, TradeSetup

NY = ZoneInfo("America/New_York")


def _setup(symbol: str = "AAA", as_of: date = date(2024, 1, 22)) -> tuple[TradeSetup, OpeningRange]:
    cand = GapCandidate(
        symbol=symbol, as_of=as_of, prior_close=100.0, premarket_last=104.0,
        gap_pct=0.04, premarket_dollar_volume=20_000_000.0, premarket_rvol=4.0,
    )
    opening = OpeningRange(
        symbol=symbol, as_of=as_of, open=104.0, high=105.0, low=103.8,
        close=104.8, volume=900_000.0, rvol_5min=6.0, direction="long",
    )
    setup = TradeSetup(
        symbol=symbol, as_of=as_of, entry_stop=105.0, initial_stop=104.0,
        risk_per_share=1.0, atr_value=2.0, rvol_5min=6.0, direction="long",
        candidate=cand,
    )
    return setup, opening


def test_feature_columns_canonical() -> None:
    cols = feature_columns()
    assert "gap_pct" in cols
    for t in CATALYST_TYPES:
        assert f"cat_type_{t}" in cols
    assert len(cols) == len(set(cols))


def test_build_feature_row_no_catalyst() -> None:
    setup, opening = _setup()
    row = build_feature_row(setup, opening)
    cols = feature_columns()
    for c in cols:
        assert c in row, f"missing {c}"
    assert row["gap_pct"] == pytest.approx(0.04)
    assert row["risk_pct"] == pytest.approx(1.0 / 105.0)
    assert row["atr_pct"] == pytest.approx(2.0 / 105.0)
    assert row["gap_to_atr"] == pytest.approx(0.04 / (2.0 / 105.0))
    assert row["cat_has"] == 0
    assert row["cat_type_none"] == 1


def test_build_feature_row_with_catalyst_feature() -> None:
    setup, opening = _setup()
    cat = CatalystFeature(
        symbol="AAA", as_of=pd.Timestamp("2024-01-22 09:25", tz="America/New_York"),
        has_catalyst=True, direction_score=1.0, strength=4, confidence=0.8,
        catalyst_type="earnings", item_count=2,
    )
    row = build_feature_row(setup, opening, catalyst=cat)
    assert row["cat_has"] == 1
    assert row["cat_strength"] == 4
    assert row["cat_dir"] == 1.0
    assert row["cat_type_earnings"] == 1
    assert row["cat_type_none"] == 0


def test_build_feature_row_with_classifier_result() -> None:
    setup, opening = _setup()
    r = ClassifierResult(
        symbol="AAA",
        as_of=pd.Timestamp("2024-01-22 09:25", tz="America/New_York").to_pydatetime(),
        prompt_version="v1", item_count=3,
        output=ClassifierOutput(direction="bearish", strength=5, confidence=0.9,
                                catalyst_type="legal", summary="x"),
    )
    row = build_feature_row(setup, opening, catalyst=r)
    assert row["cat_dir"] == -1.0
    assert row["cat_type_legal"] == 1


def test_build_dataset_with_label() -> None:
    trades = pd.DataFrame(
        [
            {"symbol": "A", "as_of": "2024-01-22", "filled": True,
             "gap_pct": 0.03, "premarket_rvol": 3.0, "rvol_5min": 4.0,
             "atr_value": 2.0, "entry_stop": 100.0, "risk_per_share": 1.0,
             "premarket_dollar_volume": 10_000_000.0, "pnl_r": 1.5},
            {"symbol": "B", "as_of": "2024-01-23", "filled": True,
             "gap_pct": 0.05, "premarket_rvol": 5.0, "rvol_5min": 6.0,
             "atr_value": 3.0, "entry_stop": 100.0, "risk_per_share": 1.5,
             "premarket_dollar_volume": 30_000_000.0, "pnl_r": -1.0},
            {"symbol": "C", "as_of": "2024-01-24", "filled": False,
             "gap_pct": 0.02, "premarket_rvol": 2.5, "rvol_5min": 2.0,
             "atr_value": 1.5, "entry_stop": 100.0, "risk_per_share": 1.0,
             "premarket_dollar_volume": 5_000_000.0, "pnl_r": 0.0},
        ]
    )
    df = build_dataset(trades)
    assert len(df) == 2  # unfilled row dropped
    assert "label" in df.columns
    assert (df["label"].tolist()) == [1, 0]
    for c in feature_columns():
        assert c in df.columns


def test_build_dataset_merges_catalysts() -> None:
    trades = pd.DataFrame(
        [{"symbol": "A", "as_of": "2024-01-22", "filled": True,
          "gap_pct": 0.03, "premarket_rvol": 3.0, "rvol_5min": 4.0,
          "atr_value": 2.0, "entry_stop": 100.0, "risk_per_share": 1.0,
          "premarket_dollar_volume": 10_000_000.0, "pnl_r": 1.5}]
    )
    cats = pd.DataFrame(
        [{"symbol": "A", "as_of": "2024-01-22", "direction": "bullish", "strength": 4,
          "confidence": 0.8, "catalyst_type": "earnings", "item_count": 3,
          "summary": "x"}]
    )
    df = build_dataset(trades, cats)
    assert df.iloc[0]["cat_strength"] == 4
    assert df.iloc[0]["cat_type_earnings"] == 1
    assert df.iloc[0]["cat_type_none"] == 0


def test_build_dataset_no_leakage_columns() -> None:
    """The label-relevant cols (entry_price, exit_*, pnl_dollars) must not be features."""
    trades = pd.DataFrame(
        [{"symbol": "A", "as_of": "2024-01-22", "filled": True,
          "gap_pct": 0.03, "premarket_rvol": 3.0, "rvol_5min": 4.0,
          "atr_value": 2.0, "entry_stop": 100.0, "risk_per_share": 1.0,
          "premarket_dollar_volume": 10_000_000.0, "pnl_r": 1.5,
          "entry_price": 100.1, "exit_price": 102.5, "pnl_dollars": 250.0,
          "exit_reason": "eod", "high_water_r": 2.5}]
    )
    df = build_dataset(trades)
    leak_cols = {"entry_price", "exit_price", "pnl_dollars", "exit_reason",
                 "high_water_r", "pnl_r"}
    assert not (leak_cols & set(df.columns))
