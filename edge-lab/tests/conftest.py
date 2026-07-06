import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd
import pytest

from src.utils.time_utils import ET


def bars(date_str: str, ticker: str, spec: list[tuple]) -> pd.DataFrame:
    """spec: list of (hhmm, o, h, l, c, v)."""
    rows = []
    for hhmm, o, h, l, c, v in spec:
        hh, mm = int(hhmm[:2]), int(hhmm[3:])
        d = pd.Timestamp(date_str)
        ts = pd.Timestamp(d.year, d.month, d.day, hh, mm, tz=ET)
        rows.append({"ticker": ticker, "ts": ts, "open": o, "high": h,
                     "low": l, "close": c, "volume": v, "transactions": v // 10})
    return pd.DataFrame(rows)


def flat_bars(date_str, ticker, start_hhmm, end_hhmm, px=100.0, v=200_000):
    d = pd.Timestamp(date_str)
    t0 = pd.Timestamp(d.year, d.month, d.day, int(start_hhmm[:2]), int(start_hhmm[3:]), tz=ET)
    t1 = pd.Timestamp(d.year, d.month, d.day, int(end_hhmm[:2]), int(end_hhmm[3:]), tz=ET)
    idx = pd.date_range(t0, t1, freq="min")
    return pd.DataFrame({"ticker": ticker, "ts": idx, "open": px,
                         "high": px * 1.0005, "low": px * 0.9995, "close": px,
                         "volume": v, "transactions": v // 10})


def panel_row(date_str: str, ticker: str, prev_close=100.0, mdv=5e7,
              atr=0.03, suspect_split=False) -> pd.DataFrame:
    return pd.DataFrame([{
        "ticker": ticker, "date": pd.Timestamp(date_str),
        "close_prev": prev_close, "dollar_vol_prev": mdv,
        "med_dollar_vol_20d_prev": mdv, "atr_pct_14d_prev": atr,
        "vol_20d_prev": 0.02, "spread_proxy_bps_prev": 12.0,
        "ret_1d_prev": 0.01, "ret_3d_prev": 0.02, "ret_5d_prev": 0.03,
        "ret_20d_prev": 0.05, "suspect_split": suspect_split,
    }])


@pytest.fixture
def mk_bars():
    return bars


@pytest.fixture
def mk_flat():
    return flat_bars


@pytest.fixture
def mk_panel():
    return panel_row
