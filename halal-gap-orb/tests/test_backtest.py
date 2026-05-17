"""End-to-end smoke test of the backtester against synthetic data."""
from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from halal_gap.backtest.engine import DailyBars, run_backtest
from halal_gap.backtest.metrics import compute_metrics
from halal_gap.backtest.report import render_report
from halal_gap.universe.builder import UniverseRow


def _daily_from_synth() -> pd.DataFrame:
    """20 daily bars ending 2024-01-24 so ATR(14) is well-defined."""
    rows = []
    base = pd.Timestamp("2024-01-24")
    for offset in range(20, 0, -1):
        d = (base - pd.Timedelta(days=offset)).date().isoformat()
        rows.append(
            {
                "date": d,
                "open": 100.0,
                "high": 102.0,
                "low": 98.0,
                "close": 100.0,
                "volume": 50_000_000,
            }
        )
    for d in ["2024-01-22", "2024-01-23", "2024-01-24"]:
        rows.append(
            {"date": d, "open": 100.0, "high": 102.0, "low": 98.0, "close": 100.0, "volume": 50_000_000}
        )
    return pd.DataFrame(rows)


def test_backtest_runs_end_to_end(synthetic_intraday: pd.DataFrame, tmp_path) -> None:
    sessions = [date(2024, 1, 22), date(2024, 1, 23), date(2024, 1, 24)]
    uni_row = UniverseRow(
        symbol="FAKE",
        as_of=sessions[-1],
        prior_close=100.0,
        dollar_volume_20d=150_000_000.0,
        atr_pct=0.02,
        sector="Technology",
        industry="Software",
    )
    universe_by_day = {d: [uni_row] for d in sessions}
    bars = {"FAKE": DailyBars(intraday=synthetic_intraday, daily=_daily_from_synth())}

    art = run_backtest(sessions, universe_by_day, bars)
    assert "equity" in art.equity.columns
    assert not art.equity.empty
    # candidates dataframe captured at least one scan
    metrics = compute_metrics(art.trades, art.equity)
    assert metrics.trading_days >= 1

    out = render_report(art, metrics, out_path=tmp_path / "smoke.html")
    assert out.exists()
    assert out.stat().st_size > 1000
