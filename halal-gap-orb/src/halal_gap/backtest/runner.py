"""Stage 1 backtest orchestration: load data, run engine, render report."""
from __future__ import annotations

import asyncio
from datetime import date, datetime, time, timedelta
from pathlib import Path

import pandas as pd

from halal_gap.backtest.engine import DailyBars, run_backtest
from halal_gap.backtest.metrics import compute_metrics
from halal_gap.backtest.report import render_report
from halal_gap.data.fmp_client import FMPClient
from halal_gap.data.halal_filter import HalalFilter
from halal_gap.universe.builder import UniverseRow, build_universe
from halal_gap.utils.config import settings
from halal_gap.utils.logging import log


async def _bars_for_symbol(
    fmp: FMPClient, symbol: str, start: date, end: date
) -> DailyBars:
    """Fetch full intraday + daily history for a symbol over the period.

    FMP's 5-minute endpoint pages internally; for a 2-year window we batch
    by 30-day chunks to stay within payload limits.
    """
    intraday_chunks: list[pd.DataFrame] = []
    cur = start
    while cur <= end:
        chunk_end = min(cur + timedelta(days=30), end)
        df = await fmp.intraday_5min(symbol, cur, chunk_end)
        if not df.empty:
            intraday_chunks.append(df)
        cur = chunk_end + timedelta(days=1)
    intraday = (
        pd.concat(intraday_chunks, ignore_index=True) if intraday_chunks else pd.DataFrame()
    )
    daily = await fmp.historical_price_eod(symbol, start - timedelta(days=60), end)
    return DailyBars(intraday=intraday, daily=daily)


async def _build_universe_for_period(
    fmp: FMPClient, sessions: list[date]
) -> dict[date, list[UniverseRow]]:
    """Build the universe for each session. Sampled weekly to limit FMP load."""
    halal = HalalFilter.from_config()
    out: dict[date, list[UniverseRow]] = {}
    last_uni: list[UniverseRow] = []
    last_built: date | None = None
    for d in sessions:
        if last_built is None or (d - last_built).days >= 5:
            try:
                last_uni = await build_universe(d, fmp, halal=halal)
                last_built = d
            except Exception as exc:  # noqa: BLE001
                log.warning(f"universe build failed for {d}: {exc}")
        out[d] = list(last_uni)
    return out


def trading_sessions(start: date, end: date) -> list[date]:
    """Mon-Fri only; does not honour US market holidays."""
    out: list[date] = []
    cur = start
    while cur <= end:
        if cur.weekday() < 5:
            out.append(cur)
        cur += timedelta(days=1)
    return out


async def run_stage1(
    start: date | None = None,
    end: date | None = None,
    out_path: Path | None = None,
) -> Path:
    """End-to-end Stage 1: build universe -> scan -> trade -> report."""
    bt_cfg = settings()["backtest"]
    start = start or date.fromisoformat(bt_cfg["start_date"])
    end = end or date.fromisoformat(bt_cfg["end_date"])
    sessions = trading_sessions(start, end)
    log.info(f"stage1: {len(sessions)} sessions from {start} to {end}")

    async with FMPClient() as fmp:
        universe_by_day = await _build_universe_for_period(fmp, sessions)
        symbols = {row.symbol for rows in universe_by_day.values() for row in rows}
        log.info(f"stage1: fetching bars for {len(symbols)} unique symbols")
        bars: dict[str, DailyBars] = {}
        for sym in sorted(symbols):
            try:
                bars[sym] = await _bars_for_symbol(fmp, sym, start, end)
            except Exception as exc:  # noqa: BLE001
                log.warning(f"bars failed for {sym}: {exc}")

    artifacts = run_backtest(sessions, universe_by_day, bars)
    metrics = compute_metrics(artifacts.trades, artifacts.equity)
    log.info(f"stage1 metrics: {metrics}")
    return render_report(artifacts, metrics, out_path=out_path)


def main() -> None:
    asyncio.run(run_stage1())


if __name__ == "__main__":
    main()
