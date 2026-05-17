"""Pre-market gap scanner. Produces ranked candidate setups at 9:25 ET."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

import pandas as pd

from halal_gap.universe.builder import UniverseRow
from halal_gap.utils.config import settings
from halal_gap.utils.indicators import rvol
from halal_gap.utils.logging import log
from halal_gap.utils.time_helpers import at_ny, premarket_bounds, to_ny


@dataclass(slots=True, frozen=True)
class GapCandidate:
    """A pre-market gapper that survived the scanner filter."""

    symbol: str
    as_of: date
    prior_close: float
    premarket_last: float
    gap_pct: float
    premarket_dollar_volume: float
    premarket_rvol: float


def _ensure_ny_index(bars: pd.DataFrame) -> pd.DataFrame:
    """Return a copy with a tz-aware NY datetime column `ts`."""
    df = bars.copy()
    if "date" in df.columns:
        df["ts"] = df["date"].map(to_ny)
    elif "datetime" in df.columns:
        df["ts"] = df["datetime"].map(to_ny)
    elif "ts" not in df.columns:
        raise ValueError("intraday bars need a 'date' or 'datetime' column")
    return df.sort_values("ts").reset_index(drop=True)


def premarket_slice(bars: pd.DataFrame, d: date) -> pd.DataFrame:
    """Return bars in [04:00, 09:30) NY for date `d`. `bars` must be 5-min OHLCV."""
    df = _ensure_ny_index(bars)
    start, end = premarket_bounds(d)
    return df[(df["ts"] >= start) & (df["ts"] < end)].reset_index(drop=True)


def premarket_metrics(
    bars: pd.DataFrame, d: date, as_of_ts: datetime | None = None
) -> tuple[float, float] | None:
    """Compute (last_price, dollar_volume) for the pre-market session on `d`.

    `as_of_ts` enforces a no-look-ahead cutoff (only bars with ts <= as_of_ts).
    """
    pre = premarket_slice(bars, d)
    if as_of_ts is not None:
        pre = pre[pre["ts"] <= as_of_ts]
    if pre.empty:
        return None
    last = float(pre["close"].iloc[-1])
    typical = (pre["high"] + pre["low"] + pre["close"]) / 3.0
    dv = float((typical * pre["volume"]).sum())
    return last, dv


def scan_one(
    row: UniverseRow,
    bars: pd.DataFrame,
    history_premarket_dv: pd.Series,
    *,
    as_of_ts: datetime | None = None,
) -> GapCandidate | None:
    """Apply scanner filters to a single symbol on `row.as_of`.

    Args:
        row: universe row (provides prior_close + dollar_volume_20d).
        bars: 5-min OHLCV bars covering at least the pre-market of row.as_of.
        history_premarket_dv: prior `lookback` days of pre-market dollar volume
            (one float per session). Used to compute pre-market RVOL.
        as_of_ts: optional decision-time cutoff for no-look-ahead.
    """
    cfg = settings()["scanner"]
    pm = premarket_metrics(bars, row.as_of, as_of_ts=as_of_ts)
    if pm is None:
        return None
    last, dv = pm

    gap_pct = (last / row.prior_close) - 1.0 if row.prior_close > 0 else 0.0
    threshold = (
        cfg["min_gap_pct_large_cap"]
        if row.dollar_volume_20d >= cfg["large_cap_dollar_volume_threshold"]
        else cfg["min_gap_pct_default"]
    )
    if gap_pct < threshold:
        return None
    if dv < cfg["min_premarket_dollar_volume"]:
        return None

    pm_rvol = rvol(dv, history_premarket_dv)
    if pm_rvol < cfg["min_premarket_rvol"] or pm_rvol != pm_rvol:  # NaN check
        return None

    return GapCandidate(
        symbol=row.symbol,
        as_of=row.as_of,
        prior_close=row.prior_close,
        premarket_last=last,
        gap_pct=gap_pct,
        premarket_dollar_volume=dv,
        premarket_rvol=pm_rvol,
    )


def aggregate_premarket_history(
    bars: pd.DataFrame, sessions: list[date]
) -> pd.Series:
    """Per-session pre-market dollar volume for the supplied dates."""
    vals: list[float] = []
    for d in sessions:
        m = premarket_metrics(bars, d, as_of_ts=at_ny(d, premarket_bounds(d)[1].time()))
        vals.append(m[1] if m else 0.0)
    return pd.Series(vals, index=pd.to_datetime(sessions), name="premarket_dv")


def previous_sessions(as_of: date, n: int) -> list[date]:
    """Return the previous `n` weekdays before `as_of` (calendar holidays NOT excluded)."""
    out: list[date] = []
    cur = as_of
    while len(out) < n:
        cur = cur - timedelta(days=1)
        if cur.weekday() < 5:
            out.append(cur)
    return list(reversed(out))


def scan_day(
    universe: list[UniverseRow],
    bars_by_symbol: dict[str, pd.DataFrame],
    *,
    as_of_ts: datetime | None = None,
) -> list[GapCandidate]:
    """Run the scanner over every universe row for a single day."""
    cfg = settings()["scanner"]
    lookback_days = cfg["premarket_rvol_lookback_days"]
    out: list[GapCandidate] = []
    for row in universe:
        bars = bars_by_symbol.get(row.symbol)
        if bars is None or bars.empty:
            continue
        sessions = previous_sessions(row.as_of, lookback_days)
        hist_dv = aggregate_premarket_history(bars, sessions)
        cand = scan_one(row, bars, hist_dv, as_of_ts=as_of_ts)
        if cand is not None:
            out.append(cand)
    log.info(f"scanner[{universe[0].as_of if universe else '?'}]: {len(out)} gap candidates")
    return out
