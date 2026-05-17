"""Run the Stage 1 backtest end-to-end against live FMP data.

Usage:
    export FMP_API_KEY=...
    uv run python scripts/run_stage1_backtest.py 2022-01-03 2023-12-29

Persists `reports/stage1_artifacts.parquet` and `reports/stage1_equity.parquet`
so the `tests/test_stage1_gate.py` integration tests can pick them up.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import date

from halal_gap.backtest.engine import run_backtest
from halal_gap.backtest.metrics import compute_metrics
from halal_gap.backtest.report import render_report
from halal_gap.backtest.runner import _build_universe_for_period, _bars_for_symbol, trading_sessions
from halal_gap.data.fmp_client import FMPClient
from halal_gap.utils.config import reports_dir, settings
from halal_gap.utils.logging import log


async def main_async(start: date, end: date) -> None:
    sessions = trading_sessions(start, end)
    log.info(f"Stage1: {len(sessions)} sessions {start}..{end}")

    async with FMPClient() as fmp:
        universe_by_day = await _build_universe_for_period(fmp, sessions)
        symbols = {row.symbol for rows in universe_by_day.values() for row in rows}
        log.info(f"Fetching bars for {len(symbols)} symbols")
        bars: dict = {}
        for sym in sorted(symbols):
            try:
                bars[sym] = await _bars_for_symbol(fmp, sym, start, end)
            except Exception as exc:  # noqa: BLE001
                log.warning(f"bars failed for {sym}: {exc}")

    art = run_backtest(sessions, universe_by_day, bars)
    metrics = compute_metrics(art.trades, art.equity)
    log.info(f"Metrics: {metrics.as_dict()}")

    rdir = reports_dir()
    art.trades.to_parquet(rdir / "stage1_artifacts.parquet", index=False)
    art.equity.to_parquet(rdir / "stage1_equity.parquet", index=False)
    art.candidates.to_parquet(rdir / "stage1_candidates.parquet", index=False)
    report_path = render_report(art, metrics)
    log.info(f"Report written to {report_path}")


def main() -> None:
    cfg = settings()["backtest"]
    p = argparse.ArgumentParser()
    p.add_argument("start", nargs="?", default=cfg["start_date"])
    p.add_argument("end", nargs="?", default=cfg["end_date"])
    args = p.parse_args()
    asyncio.run(main_async(date.fromisoformat(args.start), date.fromisoformat(args.end)))


if __name__ == "__main__":
    main()
