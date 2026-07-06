from __future__ import annotations

import pandas as pd

MINUTE_COLS = ["ticker", "ts", "open", "high", "low", "close", "volume"]
DAILY_COLS = ["ticker", "date", "open", "high", "low", "close", "volume"]


class DataValidationError(Exception):
    pass


def require_columns(df: pd.DataFrame, cols: list[str], name: str = "frame") -> None:
    missing = [c for c in cols if c not in df.columns]
    if missing:
        raise DataValidationError(f"{name} missing columns: {missing}")


def assert_sorted_ts(df: pd.DataFrame, ts_col: str = "ts") -> None:
    if len(df) > 1 and not df[ts_col].is_monotonic_increasing:
        raise DataValidationError(f"{ts_col} not sorted ascending")


def assert_no_rows_at_or_after(df: pd.DataFrame, cutoff: pd.Timestamp, ts_col: str = "ts") -> None:
    """Hard leakage gate: raise if any row is at/after the decision time."""
    if len(df) and (df[ts_col] >= cutoff).any():
        bad = df.loc[df[ts_col] >= cutoff, ts_col].iloc[0]
        raise DataValidationError(f"LEAKAGE: row at {bad} >= cutoff {cutoff}")


def basic_ohlc_sanity(df: pd.DataFrame) -> pd.DataFrame:
    """Drop obviously corrupt bars; report count dropped via attrs."""
    n0 = len(df)
    ok = (
        (df["high"] >= df["low"])
        & (df["high"] >= df["open"]) & (df["high"] >= df["close"])
        & (df["low"] <= df["open"]) & (df["low"] <= df["close"])
        & (df["open"] > 0) & (df["volume"] >= 0)
    )
    out = df.loc[ok].copy()
    out.attrs["dropped_bad_bars"] = int(n0 - len(out))
    return out
