"""Daily universe builder: point-in-time S&P 500 + halal + liquidity + ATR filter."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

import pandas as pd

from halal_gap.data.fmp_client import FMPClient
from halal_gap.data.halal_filter import HalalFilter
from halal_gap.utils.config import settings
from halal_gap.utils.indicators import atr
from halal_gap.utils.logging import log


@dataclass(slots=True, frozen=True)
class UniverseRow:
    """One row of the daily universe."""

    symbol: str
    as_of: date
    prior_close: float
    dollar_volume_20d: float
    atr_pct: float
    sector: str | None
    industry: str | None


def sp500_as_of(history: pd.DataFrame, current: pd.DataFrame, as_of: date) -> set[str]:
    """Reconstruct the S&P 500 constituent set on `as_of` from FMP change history.

    `history` columns: dateAdded, removedTicker (or removed_ticker), addedSecurity, symbol
    `current` columns: symbol (today's list)

    Logic: start from today's list. For each historical change with date > as_of,
    reverse it (remove the added ticker, add back the removed ticker).
    """
    if "symbol" not in current.columns:
        raise ValueError("current S&P 500 list missing `symbol` column")
    membership: set[str] = set(current["symbol"].astype(str).str.upper())
    if history.empty:
        return membership

    hist = history.copy()
    # FMP uses 'date' for the change date and a 'removedTicker' / 'symbol' pair.
    date_col = "date" if "date" in hist.columns else "dateAdded"
    if date_col not in hist.columns:
        return membership
    hist[date_col] = pd.to_datetime(hist[date_col], errors="coerce")
    hist = hist.dropna(subset=[date_col])

    cutoff = pd.Timestamp(as_of)
    future = hist[hist[date_col] > cutoff].sort_values(date_col, ascending=False)

    for _, row in future.iterrows():
        added = str(row.get("symbol", "")).upper() or None
        removed = str(row.get("removedTicker", "") or row.get("removed_ticker", "")).upper() or None
        # Reverse: undo the change that happened AFTER as_of
        if added and added in membership:
            membership.discard(added)
        if removed:
            membership.add(removed)
    return membership


async def _eod_window(
    fmp: FMPClient, symbol: str, end: date, days: int
) -> pd.DataFrame:
    start = end - timedelta(days=days * 2 + 10)
    df = await fmp.historical_price_eod(symbol, start, end)
    if df.empty or "date" not in df.columns:
        return pd.DataFrame()
    df = df.copy()
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)
    # Keep only rows on/before end and the most recent `days` rows
    df = df[df["date"].dt.date <= end].tail(days)
    return df


async def _liquidity_ok(
    fmp: FMPClient, symbol: str, as_of: date
) -> tuple[bool, float, float, float]:
    """Return (passes, prior_close, dollar_volume_20d, atr_pct)."""
    cfg = settings()["universe"]
    window = max(cfg["atr_window"] + 1, 21)
    df = await _eod_window(fmp, symbol, as_of, days=window + 5)
    if df.empty or len(df) < cfg["atr_window"] + 1:
        return False, 0.0, 0.0, 0.0

    prior_close = float(df["close"].iloc[-1])
    if prior_close < cfg["min_price"]:
        return False, prior_close, 0.0, 0.0

    # 20-day average dollar volume on the most recent 20 sessions
    last20 = df.tail(20)
    if {"volume", "close"}.issubset(last20.columns):
        dv = float((last20["close"] * last20["volume"]).mean())
    else:
        dv = 0.0
    if dv < cfg["min_dollar_volume_20d"]:
        return False, prior_close, dv, 0.0

    # 14-day ATR as a percent of last close
    atr_series = atr(df, window=cfg["atr_window"])
    atr_val = float(atr_series.iloc[-1]) if not atr_series.empty else 0.0
    atr_pct = atr_val / prior_close if prior_close > 0 else 0.0
    if atr_pct < cfg["min_atr_pct"]:
        return False, prior_close, dv, atr_pct

    return True, prior_close, dv, atr_pct


async def build_universe(
    as_of: date,
    fmp: FMPClient,
    halal: HalalFilter | None = None,
) -> list[UniverseRow]:
    """Return the qualifying tickers for trading on `as_of`.

    Order of filters:
      1. Point-in-time S&P 500 membership
      2. Halal exclusions (sector / industry / ticker blacklist)
      3. Prior close >= min_price
      4. 20-day avg dollar volume >= threshold
      5. 14-day ATR / prior_close >= min_atr_pct
    """
    halal = halal or HalalFilter.from_config()
    history = await fmp.sp500_historical_constituents()
    current = await fmp.sp500_current_constituents()
    membership = sp500_as_of(history, current, as_of)
    log.info(f"universe[{as_of}]: SP500 membership = {len(membership)} names")

    out: list[UniverseRow] = []
    for symbol in sorted(membership):
        profile = await fmp.company_profile(symbol)
        sector = (
            str(profile["sector"].iloc[0]) if "sector" in profile.columns and len(profile) else None
        )
        industry = (
            str(profile["industry"].iloc[0])
            if "industry" in profile.columns and len(profile)
            else None
        )
        if not halal.is_halal(ticker=symbol, sector=sector, industry=industry):
            continue
        ok, prior_close, dv, atr_pct = await _liquidity_ok(fmp, symbol, as_of)
        if not ok:
            continue
        out.append(
            UniverseRow(
                symbol=symbol,
                as_of=as_of,
                prior_close=prior_close,
                dollar_volume_20d=dv,
                atr_pct=atr_pct,
                sector=sector,
                industry=industry,
            )
        )
    log.info(f"universe[{as_of}]: passed all filters = {len(out)} names")
    return out
