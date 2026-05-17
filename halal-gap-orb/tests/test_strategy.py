"""Strategy unit tests: opening range, setup builder, trade simulator."""
from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from halal_gap.scanner.gap_scanner import GapCandidate
from halal_gap.strategy.orb import (
    build_setup,
    opening_range,
    rank_and_select,
    simulate_trade,
)


def _cand(as_of: date) -> GapCandidate:
    return GapCandidate(
        symbol="FAKE",
        as_of=as_of,
        prior_close=100.0,
        premarket_last=103.0,
        gap_pct=0.03,
        premarket_dollar_volume=20_000_000.0,
        premarket_rvol=5.0,
    )


def test_opening_range_bullish(synthetic_intraday: pd.DataFrame) -> None:
    cand = _cand(date(2024, 1, 24))
    hist_vol = pd.Series([100_000.0, 100_000.0])
    rng = opening_range(cand, synthetic_intraday, hist_vol)
    assert rng is not None
    assert rng.direction == "long"
    assert rng.high > rng.low
    assert rng.rvol_5min > 5.0  # 800k / 100k = 8


def test_build_setup_long_only(synthetic_intraday: pd.DataFrame) -> None:
    cand = _cand(date(2024, 1, 24))
    hist_vol = pd.Series([100_000.0, 100_000.0])
    rng = opening_range(cand, synthetic_intraday, hist_vol)
    assert rng is not None
    setup = build_setup(cand, rng, atr_value=2.0)
    assert setup is not None
    assert setup.entry_stop == rng.high
    assert setup.initial_stop < setup.entry_stop
    assert setup.risk_per_share > 0


def test_simulate_trade_executes(synthetic_intraday: pd.DataFrame) -> None:
    cand = _cand(date(2024, 1, 24))
    hist_vol = pd.Series([100_000.0, 100_000.0])
    rng = opening_range(cand, synthetic_intraday, hist_vol)
    setup = build_setup(cand, rng, atr_value=2.0)
    assert setup is not None
    result = simulate_trade(setup, synthetic_intraday, nav=100_000.0)
    # With a bullish synthetic day the trade should either fill or skip cleanly
    assert result.shares >= 0
    assert result.exit_reason in {"stop", "eod", "no_fill", "no_bars", "zero_shares"}


def test_rank_and_select_caps_count() -> None:
    setups = [
        type("S", (), {"rvol_5min": float(i)})() for i in range(20)
    ]
    out = rank_and_select(setups)  # type: ignore[arg-type]
    assert len(out) <= 10
    # Sorted descending by rvol
    assert all(out[i].rvol_5min >= out[i + 1].rvol_5min for i in range(len(out) - 1))
