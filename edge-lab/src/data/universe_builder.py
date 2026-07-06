"""Point-in-time tradable universe.

For each date D, membership is computed ONLY from daily rows with
date <= D-1. Because flat files contain every ticker that traded
historically (including later-delisted names), deriving the universe from
the files themselves avoids the worst survivorship bias. Remaining gaps
are documented in README_TRUTH.md.

Split handling: flat files are unadjusted and corporate-action files are
not wired in yet. Days where |overnight gap| > 40% are flagged
`suspect_split`; multi-day features for those names are masked around the
event rather than silently poisoned.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from src.config import CFG
from src.utils.logging import get_logger

log = get_logger(__name__)


def build_daily_panel(daily: pd.DataFrame) -> pd.DataFrame:
    """Adds trailing per-ticker stats used for universe gates + features.
    Every rolling stat is shifted by 1 day so row(date=D) contains only
    information from dates <= D-1 in the *_prev columns."""
    df = daily.sort_values(["ticker", "date"]).copy()
    g = df.groupby("ticker", sort=False)
    df["dollar_vol"] = df["close"] * df["volume"]
    df["ret_1d"] = g["close"].pct_change()
    prev_close = g["close"].shift(1)
    df["overnight_gap"] = df["open"] / prev_close - 1.0
    df["suspect_split"] = df["overnight_gap"].abs() > 0.40
    df["hl_range_pct"] = (df["high"] - df["low"]) / df["close"]

    def _trail(col, window, fn):
        return g[col].transform(lambda s, w=window, f=fn: getattr(s.rolling(w, min_periods=max(5, w // 2)), f)().shift(1))

    df["med_dollar_vol_20d_prev"] = _trail("dollar_vol", 20, "median")
    df["atr_pct_14d_prev"] = _trail("hl_range_pct", 14, "mean")
    df["vol_20d_prev"] = _trail("ret_1d", 20, "std")
    df["spread_proxy_bps_prev"] = _trail("hl_range_pct", 10, "median") * 10_000 * 0.10
    # 0.10 factor: crude assumption that effective spread ~ 10% of daily
    # range for liquid names. Replace with quote-derived spreads when
    # quotes_v1 is wired in. Documented in README_TRUTH.md.
    df["close_prev"] = prev_close
    df["dollar_vol_prev"] = g["dollar_vol"].shift(1)
    for n in (3, 5, 20):
        df[f"ret_{n}d_prev"] = g["close"].transform(lambda s, k=n: s.pct_change(k).shift(1))
    df["ret_1d_prev"] = g["ret_1d"].shift(1)
    return df


def universe_for_date(panel: pd.DataFrame, d: pd.Timestamp) -> pd.DataFrame:
    """Tickers passing gates as of date d, using *_prev (<= d-1) info only."""
    u = CFG.universe
    day = panel[panel["date"] == pd.Timestamp(d)]
    ok = (
        day["close_prev"].between(u.min_price, u.max_price)
        & (day["med_dollar_vol_20d_prev"] >= u.min_median_dollar_vol_20d)
        & (day["dollar_vol_prev"] >= u.min_prev_day_dollar_vol)
        & (day["spread_proxy_bps_prev"] <= u.max_spread_proxy_bps)
        & day["close_prev"].notna()
    )
    out = day.loc[ok].copy()
    log.debug("universe %s: %d names", d.date(), len(out))
    return out


def save_universe(panel: pd.DataFrame) -> None:
    CFG.paths.universe.mkdir(parents=True, exist_ok=True)
    panel.to_parquet(CFG.paths.universe / "daily_panel.parquet", index=False)


def load_universe_panel() -> pd.DataFrame:
    p = CFG.paths.universe / "daily_panel.parquet"
    if not p.exists():
        raise FileNotFoundError("Run scripts/build_dataset.py first (builds daily panel).")
    df = pd.read_parquet(p)
    df["date"] = pd.to_datetime(df["date"])
    return df
