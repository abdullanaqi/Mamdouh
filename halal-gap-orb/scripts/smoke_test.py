"""In-session smoke test that exercises the full Stage 1 pipeline using
synthetic 5-minute bars. No FMP_API_KEY required.

Run with:
    uv run python scripts/smoke_test.py
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from halal_gap.backtest.engine import DailyBars, run_backtest
from halal_gap.backtest.metrics import compute_metrics
from halal_gap.backtest.report import render_report
from halal_gap.universe.builder import UniverseRow
from halal_gap.utils.config import reports_dir
from halal_gap.utils.logging import log

NY = ZoneInfo("America/New_York")


def _make_intraday(d: date, *, gap: float, body_dir: int, prior_close: float) -> pd.DataFrame:
    rng = np.random.default_rng(int(d.strftime("%Y%m%d")))
    pre_price = prior_close * (1 + gap)
    bars: list[dict[str, object]] = []
    cur = datetime.combine(d, time(4, 0), tzinfo=NY)
    end = datetime.combine(d, time(16, 0), tzinfo=NY)
    price = pre_price
    while cur < end:
        if cur.time() < time(9, 30):
            o, c = price, price + rng.normal(0, 0.05)
            h, l = max(o, c) + 0.05, min(o, c) - 0.05
            vol = 120_000
        elif cur.time() == time(9, 30):
            o = price
            c = price + body_dir * (0.4 + abs(rng.normal(0, 0.1)))
            h, l = max(o, c) + 0.1, min(o, c) - 0.1
            vol = 900_000
        else:
            o = price
            c = price + body_dir * 0.05 + rng.normal(0, 0.08)
            h, l = max(o, c) + 0.05, min(o, c) - 0.05
            vol = 50_000
        price = c
        bars.append(
            {
                "date": cur.strftime("%Y-%m-%d %H:%M:%S"),
                "open": round(o, 4), "high": round(h, 4),
                "low": round(l, 4), "close": round(c, 4),
                "volume": vol,
            }
        )
        cur += timedelta(minutes=5)
    return pd.DataFrame(bars)


def _make_daily(sessions: list[date], close: float) -> pd.DataFrame:
    """Daily bars covering 60 days BEFORE the first session plus all sessions,
    so ATR(14) has enough warmup."""
    first = sessions[0]
    rows: list[dict[str, object]] = []
    cur = first - timedelta(days=60)
    while cur < first:
        if cur.weekday() < 5:
            rows.append(
                {"date": cur.isoformat(), "open": close - 0.5, "high": close + 1.5,
                 "low": close - 1.5, "close": close, "volume": 40_000_000}
            )
        cur += timedelta(days=1)
    for d in sessions:
        rows.append(
            {"date": d.isoformat(), "open": close - 0.5, "high": close + 1.5,
             "low": close - 1.5, "close": close, "volume": 40_000_000}
        )
    return pd.DataFrame(rows)


def main() -> None:
    sessions: list[date] = []
    cur = date(2024, 1, 8)
    while len(sessions) < 30:
        if cur.weekday() < 5:
            sessions.append(cur)
        cur += timedelta(days=1)

    symbols = ["AAA", "BBB", "CCC", "DDD"]
    bars_by_symbol = {}
    universe_by_day = {}
    for sym_idx, sym in enumerate(symbols):
        intraday_chunks = []
        for i, d in enumerate(sessions):
            gap = 0.04 if (i + sym_idx) % 3 == 0 else 0.0
            body_dir = 1 if (i + sym_idx) % 3 == 0 else (-1 if i % 7 == 0 else 0)
            intraday_chunks.append(_make_intraday(d, gap=gap, body_dir=body_dir, prior_close=100.0))
        intraday = pd.concat(intraday_chunks, ignore_index=True)
        daily = _make_daily(sessions, 100.0)
        bars_by_symbol[sym] = DailyBars(intraday=intraday, daily=daily)

    for d in sessions:
        universe_by_day[d] = [
            UniverseRow(
                symbol=s, as_of=d, prior_close=100.0,
                dollar_volume_20d=120_000_000.0, atr_pct=0.02,
                sector="Technology", industry="Software",
            )
            for s in symbols
        ]

    log.info(f"smoke: {len(sessions)} sessions, {len(symbols)} symbols")
    art = run_backtest(sessions, universe_by_day, bars_by_symbol)
    metrics = compute_metrics(art.trades, art.equity)
    log.info(f"smoke metrics: {metrics.as_dict()}")
    out = render_report(art, metrics, out_path=reports_dir() / "smoke_test.html")
    log.info(f"smoke report -> {out}")


if __name__ == "__main__":
    main()
