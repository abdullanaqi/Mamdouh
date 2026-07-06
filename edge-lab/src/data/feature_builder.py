"""Decision-time features behind a hard point-in-time barrier.

Every feature is computed through PointInTimeView, which only ever exposes
minute bars with ts < decision_ts (strict) and daily rows with
date <= D-1. tests/test_no_lookahead.py proves that appending or
perturbing future bars cannot change any feature value.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

from src.config import CFG
from src.utils.time_utils import (ET, expected_cum_vol_frac, minute_of_session,
                                  premarket_open, rth_open)
from src.utils.validation import assert_no_rows_at_or_after


@dataclass
class PointInTimeView:
    """Holds one date's minute data + strictly-prior daily history."""
    date: pd.Timestamp
    _minute_by_ticker: dict
    _daily_panel_row: pd.DataFrame  # rows of daily panel at date==D (contains *_prev cols only from <=D-1)

    @classmethod
    def build(cls, d, minute_df: pd.DataFrame, daily_panel: pd.DataFrame) -> "PointInTimeView":
        d = pd.Timestamp(d)
        rows = daily_panel[daily_panel["date"] == d]
        # HARD GUARD: only *_prev columns of the panel are legal features;
        # same-day open is legal once the session has opened (it is observed
        # at 09:30). Same-day high/low/close/volume are NOT exposed.
        by_t = {}
        for t, g in minute_df.groupby("ticker", sort=False):
            g = g.sort_values("ts").reset_index(drop=True)
            by_t[t] = g
        return cls(date=d, _minute_by_ticker=by_t, _daily_panel_row=rows.set_index("ticker"))

    def tickers(self) -> list[str]:
        return list(self._minute_by_ticker.keys())

    def minutes_before(self, ticker: str, decision_ts: pd.Timestamp) -> pd.DataFrame:
        df = self._minute_by_ticker.get(ticker)
        if df is None:
            return pd.DataFrame()
        out = df[df["ts"] < decision_ts]
        assert_no_rows_at_or_after(out, decision_ts)  # belt-and-braces
        return out

    def daily_prev(self, ticker: str) -> pd.Series | None:
        """Trailing (<= D-1) stats for ticker from the daily panel."""
        if ticker not in self._daily_panel_row.index:
            return None
        row = self._daily_panel_row.loc[ticker]
        keep = [c for c in row.index if c.endswith("_prev") or c == "suspect_split"]
        return row[keep]

    # Used ONLY by the labeler/backtester (future data), never by features:
    def _full_day_unsafe(self, ticker: str) -> pd.DataFrame:
        return self._minute_by_ticker.get(ticker, pd.DataFrame())


def _vwap(df: pd.DataFrame) -> float:
    v = df["volume"].to_numpy(dtype=float)
    if v.sum() <= 0:
        return float("nan")
    px = ((df["high"] + df["low"] + df["close"]) / 3.0).to_numpy(dtype=float)
    return float((px * v).sum() / v.sum())


def compute_features(view: PointInTimeView, ticker: str, decision_ts: pd.Timestamp) -> dict | None:
    """Returns a flat dict of decision-time features, or None if there is
    not enough prior data to trade this name responsibly."""
    d = view.date
    mins = view.minutes_before(ticker, decision_ts)
    prev = view.daily_prev(ticker)
    if prev is None or len(mins) == 0 or pd.isna(prev.get("close_prev")):
        return None

    o930 = rth_open(d.date())
    pm = mins[mins["ts"] < o930]
    ses = mins[mins["ts"] >= o930]
    if len(ses) == 0:
        return None  # need at least one completed RTH bar before deciding

    prev_close = float(prev["close_prev"])
    day_open = float(ses.iloc[0]["open"])
    last = ses.iloc[-1]
    last_px = float(last["close"])
    mso = minute_of_session(decision_ts)

    f: dict = {
        "ticker": ticker,
        "date": d,
        "decision_ts": decision_ts,
        "minute_of_session": mso,
        "last_price": last_px,
        "prev_close": prev_close,
        # ---- opening / gap ----
        "gap_pct": day_open / prev_close - 1.0,
        "open_vs_prev_close": day_open / prev_close - 1.0,
        "ret_since_open": last_px / day_open - 1.0,
        # ---- premarket ----
        "pm_volume": float(pm["volume"].sum()) if len(pm) else 0.0,
        "pm_dollar_vol": float((pm["close"] * pm["volume"]).sum()) if len(pm) else 0.0,
        "pm_high": float(pm["high"].max()) if len(pm) else float("nan"),
        "pm_low": float(pm["low"].min()) if len(pm) else float("nan"),
        "pm_vwap": _vwap(pm) if len(pm) else float("nan"),
        "pm_range_pct": (float(pm["high"].max() - pm["low"].min()) / prev_close) if len(pm) else float("nan"),
        "pm_trend": (float(pm.iloc[-1]["close"] / pm.iloc[0]["open"] - 1.0)) if len(pm) else float("nan"),
        # ---- session microstructure ----
        "session_vwap": _vwap(ses),
        "cum_volume": float(ses["volume"].sum()),
        "cum_dollar_vol": float((ses["close"] * ses["volume"]).sum()),
        "last_bar_dollar_vol": float(last["close"] * last["volume"]),
        "green_last_bar": float(last["close"] > last["open"]),
        # ---- daily history (all *_prev, <= D-1) ----
        "ret_1d_prev": float(prev.get("ret_1d_prev", np.nan)),
        "ret_3d_prev": float(prev.get("ret_3d_prev", np.nan)),
        "ret_5d_prev": float(prev.get("ret_5d_prev", np.nan)),
        "ret_20d_prev": float(prev.get("ret_20d_prev", np.nan)),
        "atr_pct_prev": float(prev.get("atr_pct_14d_prev", np.nan)),
        "vol_20d_prev": float(prev.get("vol_20d_prev", np.nan)),
        "med_dollar_vol_20d_prev": float(prev.get("med_dollar_vol_20d_prev", np.nan)),
        "spread_proxy_bps_prev": float(prev.get("spread_proxy_bps_prev", np.nan)),
        "suspect_split": bool(prev.get("suspect_split", False)),
    }

    # returns over first k minutes (only if observable by decision time)
    for k in (1, 5, 10):
        if mso >= k and len(ses) >= k:
            f[f"ret_first_{k}m"] = float(ses.iloc[min(k, len(ses)) - 1]["close"] / day_open - 1.0)
        else:
            f[f"ret_first_{k}m"] = float("nan")

    # opening range (first 15 completed minutes)
    ork = 15
    if mso > ork:
        orb = ses[ses["ts"] < o930 + pd.Timedelta(minutes=ork)]
        if len(orb):
            or_h, or_l = float(orb["high"].max()), float(orb["low"].min())
            f["or_high"], f["or_low"] = or_h, or_l
            f["orb_breakout"] = float(last_px > or_h)
            f["orb_failure"] = float(last_px < or_l)
            rng = max(or_h - or_l, 1e-9)
            f["or_position"] = (last_px - or_l) / rng
    f.setdefault("or_high", float("nan")); f.setdefault("or_low", float("nan"))
    f.setdefault("orb_breakout", float("nan")); f.setdefault("orb_failure", float("nan"))
    f.setdefault("or_position", float("nan"))

    # relative volume: session-so-far vs expected fraction of 20d median volume
    med_vol_shares = float("nan")
    mdv = f["med_dollar_vol_20d_prev"]
    if mdv and not math.isnan(mdv) and prev_close > 0:
        med_vol_shares = mdv / prev_close
    frac = expected_cum_vol_frac(mso)
    if med_vol_shares and not math.isnan(med_vol_shares) and frac > 0:
        f["rvol"] = f["cum_volume"] / (med_vol_shares * frac)
    else:
        f["rvol"] = float("nan")

    f["dist_vwap"] = last_px / f["session_vwap"] - 1.0 if f["session_vwap"] else float("nan")
    f["dist_pm_high"] = last_px / f["pm_high"] - 1.0 if f["pm_high"] and not math.isnan(f["pm_high"]) else float("nan")
    f["dist_pm_vwap"] = last_px / f["pm_vwap"] - 1.0 if f["pm_vwap"] and not math.isnan(f["pm_vwap"]) else float("nan")

    # risk / expected-move proxies
    f["expected_move_proxy"] = np.nanmax([f["atr_pct_prev"], f["pm_range_pct"]])
    f["slippage_bps_est"] = CFG.costs.one_way_bps(last_px)

    # mask history-dependent features around suspected splits (unadjusted data)
    if f["suspect_split"]:
        for c in ("ret_1d_prev", "ret_3d_prev", "ret_5d_prev", "ret_20d_prev", "gap_pct"):
            f[c] = float("nan")
    return f


FEATURE_COLUMNS = [
    "gap_pct", "ret_since_open", "ret_first_1m", "ret_first_5m", "ret_first_10m",
    "pm_volume", "pm_dollar_vol", "pm_range_pct", "pm_trend", "dist_pm_high", "dist_pm_vwap",
    "orb_breakout", "orb_failure", "or_position", "rvol", "dist_vwap", "green_last_bar",
    "ret_1d_prev", "ret_3d_prev", "ret_5d_prev", "ret_20d_prev",
    "atr_pct_prev", "vol_20d_prev", "med_dollar_vol_20d_prev", "spread_proxy_bps_prev",
    "minute_of_session", "last_price", "expected_move_proxy", "slippage_bps_est",
]
