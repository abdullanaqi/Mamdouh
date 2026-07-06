import pandas as pd
import pytest

from src.backtest import metrics


def _trades():
    return pd.DataFrame({
        "date": pd.to_datetime(["2025-06-02", "2025-06-03", "2025-06-04", "2025-06-05"]),
        "ticker": ["AAA", "BBB", "AAA", "CCC"],
        "net_ret": [0.02, -0.01, 0.03, -0.005],
        "gross_ret": [0.021, -0.009, 0.031, -0.004],
        "pnl_usd": [50.0, -27.0, 73.0, -14.5],
        "exit_reason": ["tp", "sl", "tp", "time_stop"],
        "entry_ts": pd.to_datetime(["2025-06-02"] * 4),
        "entry_price": [10, 20, 30, 40],
    })


def test_summary_numbers():
    s = metrics.summarize(_trades())
    assert s["total_trades"] == 4
    assert s["win_rate"] == pytest.approx(0.5)
    assert s["avg_win"] == pytest.approx(0.025)
    assert s["avg_loss"] == pytest.approx(-0.0075)
    assert s["profit_factor"] == pytest.approx(0.05 / 0.015, abs=1e-3)
    assert s["expectancy"] == pytest.approx(0.00875)
    assert s["worst_day"] == pytest.approx(-0.01)
    assert s["max_drawdown"] == pytest.approx(-0.01, abs=1e-6)  # 1.02 -> 1.0098 is exactly -1%
    assert s["net_pnl_usd"] == pytest.approx(81.5)


def test_flat_days_count_in_daily_series():
    days = pd.DatetimeIndex(pd.date_range("2025-06-02", periods=8, freq="B"))
    dr = metrics.daily_return_series(_trades(), days)
    assert len(dr) == 8
    assert (dr.iloc[4:] == 0).all()


def test_no_fill_trades_excluded():
    t = _trades()
    t.loc[len(t)] = {"date": pd.Timestamp("2025-06-06"), "ticker": "DDD",
                     "net_ret": 0.0, "gross_ret": 0.0, "pnl_usd": 0.0,
                     "exit_reason": "no_fill",
                     "entry_ts": pd.Timestamp("2025-06-06"), "entry_price": 1}
    assert metrics.summarize(t)["total_trades"] == 4


def test_stability_flags_concentration():
    st = metrics.stability_checks(_trades())
    assert st["top_ticker"] == "AAA"
    assert st["n_unique_tickers"] == 3
    assert st["top_ticker_pnl_share"] > 0.9
