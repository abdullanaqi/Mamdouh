"""Stage 1 gate tests.

These tests assert the Zarattini-style Stage 1 thresholds:
  * Sharpe >= 0.8 on out-of-sample 6 months
  * Hit rate >= 42%
  * Max DD <= 25%
  * Trades/day between 0.5 and 5
  * Universe at any historical date differs from the current S&P 500 list

The full backtest needs FMP_API_KEY + a 2-year intraday pull; we mark the
expensive checks as integration tests and skip them when no key is set OR
when the artifact `reports/stage1_artifacts.parquet` is absent.
"""
from __future__ import annotations

import os
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from halal_gap.backtest.metrics import compute_metrics
from halal_gap.data.halal_filter import HalalFilter
from halal_gap.universe.builder import sp500_as_of

ARTIFACTS = Path(__file__).resolve().parents[1] / "reports" / "stage1_artifacts.parquet"
EQUITY = Path(__file__).resolve().parents[1] / "reports" / "stage1_equity.parquet"


def test_survivorship_bias_guard() -> None:
    """A historical universe must differ from today's S&P 500."""
    history = pd.DataFrame(
        [{"date": "2099-01-01", "symbol": "NEW", "removedTicker": "OLD"}]
    )
    current = pd.DataFrame({"symbol": ["AAPL", "NEW"]})
    hist_set = sp500_as_of(history, current, date(2024, 1, 1))
    cur_set = set(current["symbol"])
    assert hist_set != cur_set, "point-in-time set must differ from current"


def test_halal_filter_loads_from_config() -> None:
    f = HalalFilter.from_config()
    # Standard well-known financial names must be rejected by config
    assert not f.is_halal(
        ticker="JPM", sector="Financial Services", industry="Banks - Diversified"
    )
    assert f.is_halal(
        ticker="AAPL", sector="Technology", industry="Consumer Electronics"
    )


@pytest.mark.skipif(
    not ARTIFACTS.exists() or not EQUITY.exists(),
    reason="run scripts/run_stage1_backtest.py first (needs FMP_API_KEY)",
)
def test_stage1_sharpe_gate() -> None:
    trades = pd.read_parquet(ARTIFACTS)
    equity = pd.read_parquet(EQUITY)
    # Use only the final 6 months as the OOS window
    eq = equity.sort_values("date").reset_index(drop=True)
    cutoff = eq["date"].iloc[-1] - pd.Timedelta(days=182)
    eq_oos = eq[eq["date"] >= cutoff]
    tr_oos = trades[trades["as_of"] >= cutoff]
    m = compute_metrics(tr_oos, eq_oos)
    assert m.sharpe >= 0.8, f"Stage 1 Sharpe = {m.sharpe:.2f}, gate is 0.8"


@pytest.mark.skipif(
    not ARTIFACTS.exists() or not EQUITY.exists(),
    reason="run scripts/run_stage1_backtest.py first (needs FMP_API_KEY)",
)
def test_stage1_hit_rate_gate() -> None:
    trades = pd.read_parquet(ARTIFACTS)
    equity = pd.read_parquet(EQUITY)
    m = compute_metrics(trades, equity)
    assert m.hit_rate >= 0.42, f"Stage 1 hit rate = {m.hit_rate:.3f}, gate is 0.42"


@pytest.mark.skipif(
    not ARTIFACTS.exists() or not EQUITY.exists(),
    reason="run scripts/run_stage1_backtest.py first (needs FMP_API_KEY)",
)
def test_stage1_max_dd_gate() -> None:
    trades = pd.read_parquet(ARTIFACTS)
    equity = pd.read_parquet(EQUITY)
    m = compute_metrics(trades, equity)
    assert m.max_drawdown >= -0.25, f"Stage 1 Max DD = {m.max_drawdown:.3f}, gate is -25%"


@pytest.mark.skipif(
    not ARTIFACTS.exists() or not EQUITY.exists(),
    reason="run scripts/run_stage1_backtest.py first (needs FMP_API_KEY)",
)
def test_stage1_trades_per_day_gate() -> None:
    trades = pd.read_parquet(ARTIFACTS)
    equity = pd.read_parquet(EQUITY)
    m = compute_metrics(trades, equity)
    assert 0.5 <= m.trades_per_day <= 5.0, (
        f"Stage 1 trades/day = {m.trades_per_day:.2f}, gate is [0.5, 5.0]"
    )
