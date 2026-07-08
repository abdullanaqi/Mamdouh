"""TP/SL grid backtest: what if trades exited at a take-profit or stop-loss
level instead of always holding to 15:55?

Simulates, on real minute bars, every combination of a TP/SL grid for the
selected strategy's trades (top-N by ranking_score, optionally risk-filtered).
A trade exits at the FIRST minute bar whose high touches the TP level or
whose low touches the SL level; if neither is hit by 15:54, it exits at the
15:55 open exactly like the baseline.

Fill assumptions (disclosed, conservative):
- If a bar's OPEN already gaps beyond the level, the fill is at the open
  (worse than the level for SL, better for TP) - no fantasy fills at prices
  that never traded.
- If BOTH levels are touched inside the same 1-minute bar, the order of
  events inside the bar is unknowable from bar data - the SL is assumed to
  have hit first (conservative: worst case for the strategy).
- No slippage beyond the tiered round-trip cost model in config.py, which
  is applied identically to every variant so comparisons stay fair.

Usage:
    python tp_sl_backtest.py                          # risk_filter top5, full period
    python tp_sl_backtest.py --filter none --top 5
    python tp_sl_backtest.py --days 30                # restrict to last 30 days

Outputs reports/tp_sl_grid[...].csv - one row per TP/SL combination,
including the no-TP/no-SL baseline for reference.
"""
from __future__ import annotations

import argparse
import os

import duckdb
import pandas as pd

import config

_log = config.setup_logging(__name__)

TP_GRID = [None, 0.01, 0.02, 0.03, 0.05]                          
SL_GRID = [None, 0.01, 0.02, 0.03, 0.05]                        

FILTER_SQL = {
    "none": "TRUE",
    "risk_filter": "(risk_score IS NULL OR risk_percentile < 0.75)",
}


def _load_frames(con: duckdb.DuckDBPyConnection, filter_name: str, top_n: int, days: int | None):
    date_clause = (
        f"AND trade_date >= (SELECT MAX(trade_date) FROM candidates_scored) - INTERVAL {days} DAY"
        if days else ""
    )
    con.execute(
        f"""
        CREATE TEMP TABLE trades AS
        WITH ranked AS (
            SELECT trade_date, ticker, entry_price_0935, exit_price_1555,
                   return_0935_to_1555,
                   ROW_NUMBER() OVER (PARTITION BY trade_date ORDER BY ranking_score DESC) AS rn
            FROM candidates_scored
            WHERE ranking_score IS NOT NULL AND {FILTER_SQL[filter_name]} {date_clause}
        )
        SELECT * FROM ranked WHERE rn <= {top_n}
        """
    )
    n = con.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
    _log.info(f"Simulating {n:,} trades ({filter_name}, top {top_n}{f', last {days}d' if days else ''})")

                                                                          
    con.execute(
        """
        CREATE TEMP TABLE trade_bars AS
        SELECT t.trade_date, t.ticker, t.entry_price_0935, b.ts_ny_time, b.open, b.high, b.low
        FROM trades t
        JOIN halal_minute_bars b ON b.ticker = t.ticker AND b.trade_date = t.trade_date
        WHERE b.ts_ny_time >= TIME '09:35:00' AND b.ts_ny_time <= TIME '15:54:00'
        """
    )


def _simulate_combo(con: duckdb.DuckDBPyConnection, tp: float | None, sl: float | None) -> dict:
    tp_level = f"entry_price_0935 * (1 + {tp})" if tp is not None else "NULL"
    sl_level = f"entry_price_0935 * (1 - {sl})" if sl is not None else "NULL"
    tp_hit = f"(high >= {tp_level})" if tp is not None else "FALSE"
    sl_hit = f"(low <= {sl_level})" if sl is not None else "FALSE"

    df = con.execute(
        f"""
        WITH touches AS (
            SELECT trade_date, ticker, entry_price_0935, ts_ny_time, open,
                   {tp_hit} AS tp_hit, {sl_hit} AS sl_hit
            FROM trade_bars
            WHERE {tp_hit} OR {sl_hit}
        ),
        first_touch AS (
            SELECT *, ROW_NUMBER() OVER (PARTITION BY trade_date, ticker ORDER BY ts_ny_time) AS k
            FROM touches
        ),
        exits AS (
            SELECT
                trade_date, ticker,
                CASE
                    -- both touched in the same bar: assume SL first (conservative)
                    WHEN sl_hit THEN LEAST(open, {sl_level})
                    ELSE GREATEST(open, {tp_level})
                END AS early_exit_price,
                CASE WHEN sl_hit THEN 'sl' ELSE 'tp' END AS exit_kind
            FROM first_touch WHERE k = 1
        )
        SELECT
            t.trade_date, t.ticker, t.entry_price_0935,
            COALESCE(e.early_exit_price, t.exit_price_1555) AS exit_price,
            COALESCE(e.exit_kind, 'time') AS exit_kind
        FROM trades t
        LEFT JOIN exits e ON e.trade_date = t.trade_date AND e.ticker = t.ticker
        """
    ).fetchdf()

    df["gross_return"] = (df["exit_price"] - df["entry_price_0935"]) / df["entry_price_0935"]
    df["net_return"] = df["gross_return"] - df["entry_price_0935"].map(config.cost_bps_for_price) / 10000.0
    daily = df.groupby("trade_date")["net_return"].mean().sort_index()
    equity = (1 + daily).cumprod()
    drawdown = (equity / equity.cummax() - 1).min()
    kinds = df["exit_kind"].value_counts(normalize=True)

    return {
        "tp_pct": tp, "sl_pct": sl,
        "n_trades": len(df),
        "gross_avg_return": df["gross_return"].mean(),
        "net_avg_return": df["net_return"].mean(),
        "net_win_rate": (df["net_return"] > 0).mean(),
        "net_avg_daily_return": daily.mean(),
        "net_max_drawdown": drawdown,
        "pct_exit_tp": kinds.get("tp", 0.0),
        "pct_exit_sl": kinds.get("sl", 0.0),
        "pct_exit_time": kinds.get("time", 0.0),
    }


def run(filter_name: str, top_n: int, days: int | None) -> None:
    os.makedirs(config.REPORT_DIR, exist_ok=True)
    suffix = f"_{filter_name}_top{top_n}" + (f"_last{days}d" if days else "")
    out_path = os.path.join(config.REPORT_DIR, f"tp_sl_grid{suffix}.csv")

    con = duckdb.connect(config.OUTPUT_DB_PATH, read_only=True)
    try:
        _load_frames(con, filter_name, top_n, days)
        rows = []
        for tp in TP_GRID:
            for sl in SL_GRID:
                r = _simulate_combo(con, tp, sl)
                rows.append(r)
                label = f"tp={tp if tp is not None else '-'} sl={sl if sl is not None else '-'}"
                _log.info(f"[{label:>14s}] net_avg={r['net_avg_return']:+.4f} win={r['net_win_rate']:.1%} "
                          f"dd={r['net_max_drawdown']:+.1%} exits tp/sl/time="
                          f"{r['pct_exit_tp']:.0%}/{r['pct_exit_sl']:.0%}/{r['pct_exit_time']:.0%}")
    finally:
        con.close()

    result = pd.DataFrame(rows).sort_values("net_avg_return", ascending=False)
    result.to_csv(out_path, index=False)
    _log.info(f"Wrote {out_path}")
    best = result.iloc[0]
    baseline = result[(result.tp_pct.isna()) & (result.sl_pct.isna())].iloc[0]
    _log.info(
        f"Best combo: tp={best['tp_pct']} sl={best['sl_pct']} net_avg={best['net_avg_return']:+.4f} "
        f"vs baseline (hold to 15:55) net_avg={baseline['net_avg_return']:+.4f}"
    )


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--filter", choices=list(FILTER_SQL), default="risk_filter")
    p.add_argument("--top", type=int, default=5)
    p.add_argument("--days", type=int, default=None, help="Restrict to last N calendar days (default: full period)")
    args = p.parse_args()
    run(args.filter, args.top, args.days)
