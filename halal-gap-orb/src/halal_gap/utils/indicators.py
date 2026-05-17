"""Pure-function technical indicators. Always take a DataFrame, never global state."""
from __future__ import annotations

import numpy as np
import pandas as pd


def true_range(df: pd.DataFrame) -> pd.Series:
    """Wilder true-range. Expects columns: high, low, close."""
    prev_close = df["close"].shift(1)
    a = df["high"] - df["low"]
    b = (df["high"] - prev_close).abs()
    c = (df["low"] - prev_close).abs()
    return pd.concat([a, b, c], axis=1).max(axis=1)


def atr(df: pd.DataFrame, window: int = 14) -> pd.Series:
    """Average true range using a simple rolling mean (Zarattini-style)."""
    tr = true_range(df)
    return tr.rolling(window=window, min_periods=window).mean()


def vwap(df: pd.DataFrame) -> pd.Series:
    """Anchored VWAP from the first row in `df` forward.

    Expects columns: high, low, close, volume.
    """
    typical = (df["high"] + df["low"] + df["close"]) / 3.0
    pv = typical * df["volume"]
    cum_pv = pv.cumsum()
    cum_vol = df["volume"].cumsum().replace(0, np.nan)
    return cum_pv / cum_vol


def ema(series: pd.Series, period: int) -> pd.Series:
    """Exponential moving average."""
    return series.ewm(span=period, adjust=False, min_periods=period).mean()


def rvol(today_vol: float, history_vol: pd.Series) -> float:
    """Relative volume: today's volume divided by the mean of the history series."""
    h = history_vol.dropna()
    if h.empty:
        return float("nan")
    mean = float(h.mean())
    if mean <= 0:
        return float("nan")
    return float(today_vol) / mean
