import pandas as pd
import pytest

from src.backtest.execution_simulator import ExitSpec, simulate_trade
from src.utils.time_utils import ts_et
from tests.conftest import bars, flat_bars

D = "2025-06-02"
T = ts_et(D, "10:00")


def _run(day, tp=0.03, sl=0.02, **kw):
    return simulate_trade(day, "AAA", D, T, ExitSpec(tp_pct=tp, sl_pct=sl, **kw))


def test_tp_hit():
    day = bars(D, "AAA", [("10:00", 100, 100.2, 99.9, 100.1, 500_000),
                          ("10:01", 100.1, 104.5, 100.0, 104.0, 500_000)])
    r = _run(day)
    assert r.exit_reason == "tp"
    assert r.exit_price == pytest.approx(r.entry_price * 1.03)
    assert r.net_ret < r.gross_ret  # fees charged


def test_sl_hit_and_worse_than_level():
    day = bars(D, "AAA", [("10:00", 100, 100.2, 99.9, 100.1, 500_000),
                          ("10:01", 100.0, 100.1, 97.0, 97.2, 500_000)])
    r = _run(day)
    assert r.exit_reason == "sl"
    assert r.exit_price < r.entry_price * (1 - 0.02) + 1e-9  # stop slippage applied


def test_both_touched_same_bar_counts_as_sl():
    day = bars(D, "AAA", [("10:00", 100, 100.2, 99.9, 100.1, 500_000),
                          ("10:01", 100.0, 106.0, 95.0, 101.0, 500_000)])
    assert _run(day).exit_reason == "sl"


def test_gap_through_sl_fills_at_open():
    day = bars(D, "AAA", [("10:00", 100, 100.2, 99.9, 100.1, 500_000),
                          ("10:01", 96.0, 96.5, 95.5, 96.2, 500_000)])
    r = _run(day)
    assert r.exit_reason == "sl"
    assert r.exit_price < r.entry_price * (1 - 0.02)  # worse than the stop level


def test_gap_through_tp_fills_at_tp_not_open():
    day = bars(D, "AAA", [("10:00", 100, 100.2, 99.9, 100.1, 500_000),
                          ("10:01", 105.0, 105.5, 104.8, 105.2, 500_000)])
    r = _run(day)
    assert r.exit_reason == "tp"
    assert r.exit_price == pytest.approx(r.entry_price * 1.03)  # conservative


def test_no_bar_at_decision_means_no_fill():
    day = bars(D, "AAA", [("10:01", 100, 100.2, 99.9, 100.1, 500_000)])
    assert _run(day).exit_reason == "no_fill"


def test_time_stop_exits_at_configured_time():
    day = flat_bars(D, "AAA", "10:00", "15:59")
    r = _run(day, tp=0.10, sl=0.10)
    assert r.exit_reason == "time_stop"
    assert r.exit_ts == ts_et(D, "15:55")


def test_trailing_stop_locks_in_gains():
    spec = [("10:00", 100, 100.1, 99.95, 100.05, 500_000)]
    for i, px in enumerate([101, 102, 103, 104], start=1):
        spec.append((f"10:{i:02d}", px - 0.5, px + 0.1, px - 0.6, px, 500_000))
    spec.append(("10:05", 103.5, 103.6, 100.5, 100.6, 500_000))  # pullback
    day = bars(D, "AAA", spec)
    r = simulate_trade(day, "AAA", D, T, ExitSpec(tp_pct=0.20, sl_pct=0.05, trail_pct=0.02))
    assert r.exit_reason == "sl"
    assert r.gross_ret > 0  # trailed stop exited in profit
