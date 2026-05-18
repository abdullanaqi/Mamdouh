"""KillSwitch rule-by-rule tests."""
from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from halal_gap.execution.kill_switch import (
    KillSwitch,
    _consec_losing_days,
    _equity_to_daily_returns,
    _max_pairwise_corr,
    _rolling_drawdown,
)


def _equity_curve(values: list[float]) -> pd.DataFrame:
    base = pd.Timestamp("2024-01-02")
    return pd.DataFrame(
        {"date": [base + pd.Timedelta(days=i) for i in range(len(values))],
         "equity": values}
    )


def test_consec_losing_days_counts_tail_run() -> None:
    rets = pd.Series([0.01, -0.01, -0.01, -0.005])
    assert _consec_losing_days(rets) == 3
    rets = pd.Series([0.01, -0.01, 0.001])
    assert _consec_losing_days(rets) == 0


def test_rolling_drawdown_negative_at_trough() -> None:
    eq = _equity_curve([100, 110, 108, 95, 90])
    dd = _rolling_drawdown(eq, window_days=10)
    # peak 110 -> trough 90 -> -18.18%
    assert dd == pytest.approx(-0.1818, rel=1e-3)


def test_kill_switch_passes_clean_state() -> None:
    eq = _equity_curve([100_000, 100_500, 101_000])
    decision = KillSwitch().evaluate(equity=eq, vix_closes=[15, 16, 17])
    assert not decision.tripped
    assert decision.reason == "ok"


def test_kill_switch_trips_consec_losing_days() -> None:
    # 3 consecutive losing days -> trip (config consec_losing_days=3)
    eq = _equity_curve([100, 99, 98, 97])
    decision = KillSwitch().evaluate(equity=eq)
    assert decision.tripped
    assert any("consec_losing_days" in r for r in decision.triggered_rules)


def test_kill_switch_trips_rolling_drawdown() -> None:
    eq = _equity_curve([100, 120, 80])  # -33% DD, exceeds 15% cap
    decision = KillSwitch().evaluate(equity=eq)
    assert decision.tripped
    assert any("rolling_dd" in r for r in decision.triggered_rules)


def test_kill_switch_trips_vix() -> None:
    eq = _equity_curve([100, 100.1, 100.2])
    # VIX threshold 30 for 3 consecutive closes
    decision = KillSwitch().evaluate(equity=eq, vix_closes=[35, 36, 32])
    assert decision.tripped
    assert any("vix" in r for r in decision.triggered_rules)


def test_kill_switch_vix_not_consecutive() -> None:
    eq = _equity_curve([100, 100.1, 100.2])
    # Only 2 highs at the tail then a dip -> rule does NOT fire
    decision = KillSwitch().evaluate(equity=eq, vix_closes=[35, 20, 36, 35])
    triggers = [r for r in decision.triggered_rules if "vix" in r]
    assert triggers == []


def test_kill_switch_trips_sharpe_divergence() -> None:
    eq = _equity_curve([100, 100.1, 100.2])
    paper = [0.01] * 10        # paper looks great
    live = [-0.01] * 10        # live is bleeding
    decision = KillSwitch().evaluate(equity=eq, paper_returns=paper, live_returns=live)
    assert decision.tripped
    assert any("sharpe_divergence" in r for r in decision.triggered_rules)


def test_kill_switch_skips_sharpe_when_no_live() -> None:
    eq = _equity_curve([100, 100.1, 100.2])
    decision = KillSwitch().evaluate(equity=eq, paper_returns=[0.01] * 10, live_returns=[])
    # Rule must skip silently when there's no live history
    assert not any("sharpe_divergence" in r for r in decision.triggered_rules)


def test_kill_switch_trips_position_correlation() -> None:
    eq = _equity_curve([100, 100.1, 100.2])
    corr = pd.DataFrame(
        [[1.0, 0.95, 0.10], [0.95, 1.0, 0.20], [0.10, 0.20, 1.0]],
        index=["A", "B", "C"], columns=["A", "B", "C"],
    )
    decision = KillSwitch().evaluate(equity=eq, position_corr=corr)
    assert decision.tripped
    assert any("position_corr" in r for r in decision.triggered_rules)


def test_max_pairwise_corr_ignores_diagonal() -> None:
    corr = pd.DataFrame(
        [[1.0, 0.3, 0.2], [0.3, 1.0, 0.4], [0.2, 0.4, 1.0]],
        index=list("ABC"), columns=list("ABC"),
    )
    assert _max_pairwise_corr(corr) == pytest.approx(0.4)


def test_kill_switch_detail_carries_metrics() -> None:
    eq = _equity_curve([100, 99, 98, 97])
    decision = KillSwitch().evaluate(equity=eq, vix_closes=[10])
    assert "consec_losing_days" in decision.detail
    assert "rolling_dd" in decision.detail
