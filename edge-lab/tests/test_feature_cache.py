"""Disk feature cache: round-trip fidelity and cache-hit fast paths."""
import numpy as np
import pandas as pd
import pytest

from src.data import feature_cache as fc
from src.utils.time_utils import ET


@pytest.fixture(autouse=True)
def tmp_cache_dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(fc, "FEAT_DIR", tmp_path / "features")
    monkeypatch.setattr(fc, "ROWS_DIR", tmp_path / "rows")
    monkeypatch.setattr(fc, "BARS_DIR", tmp_path / "candbars")
    fc.clear_memory()
    yield
    fc.clear_memory()


def _feats(d):
    ts = pd.Timestamp(f"{d} 09:45", tz=ET)
    return [
        {"ticker": "AAA", "decision_ts": ts, "gap_pct": 0.03, "rvol": 2.0,
         "ret_since_open": 0.01, "last_price": 10.0, "atr_pct_prev": 0.03},
        {"ticker": "BBB", "decision_ts": ts, "gap_pct": np.nan, "rvol": 0.5,
         "ret_since_open": -0.01, "last_price": 5.0, "atr_pct_prev": np.nan},
    ]


def test_round_trip_features_and_rows():
    d = "2025-01-06"
    feats = _feats(d)
    rows = [{**feats[0], "mfe_eod": 0.04, "mae_eod": -0.01, "net_ret_ref": 0.02}]
    fc.save_day(d, {"09:45": feats}, {"09:45": rows}, pd.DataFrame())
    got = fc.load_features(d, "09:45")
    assert [g["ticker"] for g in got] == ["AAA", "BBB"]
    assert got[0]["gap_pct"] == pytest.approx(0.03)
    assert np.isnan(got[1]["gap_pct"])
    assert got[0]["decision_ts"] == feats[0]["decision_ts"]
    gr = fc.load_rows(d, "09:45")
    assert len(gr) == 1 and gr[0]["net_ret_ref"] == pytest.approx(0.02)
    assert fc.has_rows(d, ["09:45"]) and not fc.has_rows(d, ["09:45", "10:30"])


def test_missing_day_returns_none():
    assert fc.load_features("2025-01-07", "09:45") is None
    assert fc.load_rows("2025-01-07", "09:45") is None
    assert fc.load_cand_bars("2025-01-07", "AAA") is None


def test_cand_bars_round_trip(mk_flat):
    d = "2025-01-06"
    bars = pd.concat([mk_flat(d, "AAA", "09:30", "16:00", px=10.0),
                      mk_flat(d, "BBB", "09:30", "16:00", px=20.0)],
                     ignore_index=True)
    fc.save_day(d, {}, {}, bars)
    got = fc.load_cand_bars(d, "AAA")
    assert got is not None and set(got["ticker"]) == {"AAA"}
    assert len(got) == len(bars) // 2
    assert got["ts"].iloc[0].tzname() is not None
    assert fc.load_cand_bars(d, "ZZZ") is None  # not covered -> caller falls back


def test_build_training_frame_uses_cache_without_minute_load():
    from src.research.strategy_search import build_training_frame
    d = "2025-01-06"
    feats = _feats(d)
    rows = [{**feats[0], "date": pd.Timestamp(d), "mfe_eod": 0.04,
             "mae_eod": -0.01, "net_ret_ref": 0.02}]
    fc.save_day(d, {"09:45": feats}, {"09:45": rows}, pd.DataFrame())

    def exploding_loader(_):
        raise AssertionError("minute loader must not be called on cache hit")

    out = build_training_frame([pd.Timestamp(d)], pd.DataFrame(),
                               ["09:45"], lambda f: True, exploding_loader)
    assert len(out) == 1 and out.iloc[0]["ticker"] == "AAA"


def test_lazy_view_serves_bars_from_cache(mk_flat):
    from src.backtest.intraday_backtester import _LazyDayView
    d = "2025-01-06"
    bars = mk_flat(d, "AAA", "09:30", "16:00", px=10.0)
    fc.save_day(d, {}, {}, bars)

    def exploding_loader(_):
        raise AssertionError("minute loader must not be called on cache hit")

    v = _LazyDayView(pd.Timestamp(d), pd.DataFrame(), exploding_loader)
    got = v._full_day_unsafe("AAA")
    assert len(got) == len(bars)
    # uncovered ticker falls back to the loader (here: an empty day)
    v2 = _LazyDayView(pd.Timestamp("2025-01-07"), pd.DataFrame(), lambda _: None)
    assert len(v2._full_day_unsafe("AAA")) == 0
