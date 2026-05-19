"""Scoped real-data Stage 1 backtest.

Differs from `run_stage1_backtest.py` in two practical ways:
  1. Uses a curated halal-safe large-cap list instead of reconstructing the
     full S&P 500 from FMP's historical change log. This avoids ~500
     profile + EOD calls per universe rebuild and is the right tradeoff
     when we want to validate the pipeline end-to-end rather than test
     point-in-time membership logic.
  2. Builds UniverseRow objects in-memory from already-fetched EOD bars
     so the liquidity + ATR filter does not hit FMP per session.

Outputs (matching the strict runner so test_stage1_gate picks them up):
  reports/stage1_artifacts.parquet
  reports/stage1_equity.parquet
  reports/stage1_candidates.parquet
  reports/stage1_backtest_<ts>.html
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import date, timedelta

import pandas as pd

from halal_gap.backtest.engine import DailyBars, run_backtest
from halal_gap.backtest.metrics import compute_metrics
from halal_gap.backtest.report import render_report
from halal_gap.backtest.runner import _bars_for_symbol, trading_sessions
from halal_gap.data.fmp_client import FMPClient
from halal_gap.data.halal_filter import HalalFilter
from halal_gap.universe.builder import UniverseRow
from halal_gap.utils.config import reports_dir, settings
from halal_gap.utils.indicators import atr
from halal_gap.utils.logging import log


# Curated halal-safe S&P 500 large caps. Hand-picked from common Shariah
# screens to skip the FMP-profile loop for each backtest day.
HALAL_LARGE_CAPS: dict[str, tuple[str, str]] = {
    "AAPL":  ("Technology", "Consumer Electronics"),
    "MSFT":  ("Technology", "Software - Infrastructure"),
    "GOOGL": ("Communication Services", "Internet Content & Information"),
    "NVDA":  ("Technology", "Semiconductors"),
    "AMD":   ("Technology", "Semiconductors"),
    "ORCL":  ("Technology", "Software - Infrastructure"),
    "CRM":   ("Technology", "Software - Application"),
    "ADBE":  ("Technology", "Software - Infrastructure"),
    "INTC":  ("Technology", "Semiconductors"),
    "CSCO":  ("Technology", "Communication Equipment"),
    "IBM":   ("Technology", "Information Technology Services"),
    "QCOM":  ("Technology", "Semiconductors"),
    "TXN":   ("Technology", "Semiconductors"),
    "AVGO":  ("Technology", "Semiconductors"),
    "META":  ("Communication Services", "Internet Content & Information"),
    "NFLX":  ("Communication Services", "Entertainment"),
    "AMZN":  ("Consumer Cyclical", "Internet Retail"),
    "NKE":   ("Consumer Cyclical", "Footwear & Accessories"),
    "HD":    ("Consumer Cyclical", "Home Improvement Retail"),
    "COST":  ("Consumer Defensive", "Discount Stores"),
    "JNJ":   ("Healthcare", "Drug Manufacturers - General"),
    "MRK":   ("Healthcare", "Drug Manufacturers - General"),
    "PFE":   ("Healthcare", "Drug Manufacturers - General"),
    "ABT":   ("Healthcare", "Medical Devices"),
    "LLY":   ("Healthcare", "Drug Manufacturers - General"),
    "CAT":   ("Industrials", "Farm & Heavy Construction Machinery"),
    "DE":    ("Industrials", "Farm & Heavy Construction Machinery"),
    "UPS":   ("Industrials", "Integrated Freight & Logistics"),
    "XOM":   ("Energy", "Oil & Gas Integrated"),
    "CVX":   ("Energy", "Oil & Gas Integrated"),
}


def _universe_row(
    symbol: str, as_of: date, daily: pd.DataFrame
) -> UniverseRow | None:
    """Apply min_price / dollar volume / ATR filter from in-memory daily bars."""
    cfg = settings()["universe"]
    df = daily.copy()
    df["date"] = pd.to_datetime(df["date"])
    df = df[df["date"].dt.date <= as_of].sort_values("date").reset_index(drop=True)
    window = cfg["atr_window"]
    if len(df) < window + 1:
        return None

    prior_close = float(df["close"].iloc[-1])
    if prior_close < cfg["min_price"]:
        return None

    last20 = df.tail(20)
    dv = float((last20["close"] * last20["volume"]).mean())
    if dv < cfg["min_dollar_volume_20d"]:
        return None

    atr_series = atr(df, window=window)
    atr_val = float(atr_series.iloc[-1]) if not atr_series.empty else 0.0
    atr_pct = atr_val / prior_close if prior_close > 0 else 0.0
    if atr_pct < cfg["min_atr_pct"]:
        return None

    sector, industry = HALAL_LARGE_CAPS[symbol]
    return UniverseRow(
        symbol=symbol,
        as_of=as_of,
        prior_close=prior_close,
        dollar_volume_20d=dv,
        atr_pct=atr_pct,
        sector=sector,
        industry=industry,
    )


async def main_async(start: date, end: date, out_dir) -> None:
    sessions = trading_sessions(start, end)
    log.info(f"scoped Stage 1: {len(sessions)} sessions {start}..{end}")

    halal = HalalFilter.from_config()
    symbols = [
        s
        for s, (sec, ind) in HALAL_LARGE_CAPS.items()
        if halal.is_halal(ticker=s, sector=sec, industry=ind)
    ]
    log.info(f"halal-safe universe: {len(symbols)} symbols")

    async with FMPClient() as fmp:
        bars: dict[str, DailyBars] = {}
        for sym in symbols:
            try:
                bars[sym] = await _bars_for_symbol(fmp, sym, start, end)
                log.info(
                    f"  {sym}: intraday={len(bars[sym].intraday)} daily={len(bars[sym].daily)}"
                )
            except Exception as exc:  # noqa: BLE001
                log.warning(f"bars failed for {sym}: {exc}")

    universe_by_day: dict[date, list[UniverseRow]] = {}
    for d in sessions:
        rows: list[UniverseRow] = []
        for sym, bundle in bars.items():
            if bundle.daily.empty:
                continue
            row = _universe_row(sym, d, bundle.daily)
            if row is not None:
                rows.append(row)
        universe_by_day[d] = rows
    qual = sum(len(rs) for rs in universe_by_day.values())
    log.info(f"qualifying symbol-days: {qual} (avg {qual/len(sessions):.1f}/day)")

    art = run_backtest(sessions, universe_by_day, bars)
    metrics = compute_metrics(art.trades, art.equity)
    log.info(f"OOS metrics: {metrics.as_dict()}")

    out_dir.mkdir(parents=True, exist_ok=True)
    art.trades.to_parquet(out_dir / "stage1_artifacts.parquet", index=False)
    art.equity.to_parquet(out_dir / "stage1_equity.parquet", index=False)
    art.candidates.to_parquet(out_dir / "stage1_candidates.parquet", index=False)
    rpt = render_report(art, metrics)
    log.info(f"report -> {rpt}")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("start", help="YYYY-MM-DD start date")
    p.add_argument("end", help="YYYY-MM-DD end date")
    args = p.parse_args()
    asyncio.run(
        main_async(
            date.fromisoformat(args.start),
            date.fromisoformat(args.end),
            reports_dir(),
        )
    )


if __name__ == "__main__":
    main()
