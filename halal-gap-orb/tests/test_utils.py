"""Unit tests for indicator and time helpers."""
from __future__ import annotations

from datetime import datetime

import pandas as pd
import pytest

from halal_gap.utils.indicators import atr, ema, rvol, true_range, vwap
from halal_gap.utils.time_helpers import NY_TZ, ensure_ny, to_ny


def test_true_range_with_gap() -> None:
    df = pd.DataFrame({"high": [10, 12], "low": [9, 11], "close": [9.5, 11.5]})
    tr = true_range(df)
    assert tr.iloc[1] == pytest.approx(max(12 - 11, abs(12 - 9.5), abs(11 - 9.5)))


def test_atr_window() -> None:
    df = pd.DataFrame(
        {
            "high": [10, 11, 12, 13, 14, 15],
            "low": [9, 10, 11, 12, 13, 14],
            "close": [9.5, 10.5, 11.5, 12.5, 13.5, 14.5],
        }
    )
    a = atr(df, window=3)
    assert a.isna().iloc[1]   # not enough history yet
    assert not a.isna().iloc[2]
    assert not a.isna().iloc[5]


def test_vwap_monotonic_with_uptrend() -> None:
    df = pd.DataFrame(
        {
            "high": [10, 11, 12],
            "low": [9, 10, 11],
            "close": [9.5, 10.5, 11.5],
            "volume": [100, 100, 100],
        }
    )
    v = vwap(df)
    assert v.iloc[2] > v.iloc[0]


def test_ema_period() -> None:
    s = pd.Series([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], dtype=float)
    e = ema(s, period=3)
    assert pd.isna(e.iloc[1])
    assert not pd.isna(e.iloc[2])


def test_rvol_nan_guard() -> None:
    assert rvol(100.0, pd.Series([0, 0, 0])) != rvol(100.0, pd.Series([0, 0, 0]))  # NaN != NaN


def test_ensure_ny_rejects_naive() -> None:
    with pytest.raises(ValueError):
        ensure_ny(datetime(2024, 1, 1, 9, 30))


def test_to_ny_treats_naive_as_ny() -> None:
    out = to_ny("2024-01-01T09:30:00")
    assert out.tzinfo is not None
    assert out.tzinfo.utcoffset(out) == NY_TZ.utcoffset(out)
