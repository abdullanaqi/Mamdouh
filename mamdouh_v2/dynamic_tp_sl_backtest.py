"""Dynamic TP/SL backtest: exit levels scaled to each stock's own volatility,
plus trailing-stop variants - instead of one fixed percentage for every stock.

Why dynamic: a fixed 3% TP means something completely different for a calm
$200 mega-cap (huge target, never hit) than for a volatile $8 small-cap
(tiny target, hit by noise). Scaling levels to the stock's own pre-entry
volatility makes the rule comparable across the whole universe.

Volatility proxy (pre-entry only - leakage-safe):
    vol = clamp(max(opening_5min_range_pct, 0.5 * premarket_range_pct), 0.3%, 10%)
where opening_5min_range = high-low of 09:30-09:34 relative to entry price,
and premarket_range = premarket high-low. Both are known before 09:35.

Two dynamic modes:
  scaled - static levels set at entry: TP = entry*(1 + k_tp*vol),
           SL = entry*(1 - k_sl*vol); grid over multipliers k.
  trail  - trailing stop: exit when price drops `d` below the running high
           since entry (d fixed % or vol-scaled); optional hard TP on top.
           The trail level is computed from PRIOR bars' highs only, so a
           same-bar spike-then-drop cannot fantasy-fill (conservative).

Same fill assumptions and tiered cost model as tp_sl_backtest.py; same-bar
TP+stop conflicts resolve to the stop (conservative).

Usage:
    python dynamic_tp_sl_backtest.py                     # both modes, risk_filter top5, full period
    python dynamic_tp_sl_backtest.py --mode trail --days 30
    python dynamic_tp_sl_backtest.py --filter none
"""
from __future__ import annotations

import argparse
import os

import duckdb
import pandas as pd

import config

_log = config.setup_logging(__name__)

                                                                       
                                                                          
TP_MULTS = [None, 2.0, config.DYNAMIC_TP_VOL_MULT, 4.0, 6.0]
SL_MULTS = [None, config.DYNAMIC_SL_VOL_MULT, 3.0, 4.0, 6.0]

                                                                         
                                           
TRAIL_FIXED = [0.01, 0.02, 0.03, 0.05]
TRAIL_MULTS = [2.0, 3.0, 4.0, 6.0]
TRAIL_TP_OPTIONS = [None, 0.03]

FILTER_SQL = {
    "none": "TRUE",
    "risk_filter": "(risk_score IS NULL OR risk_percentile < 0.75)",
}


def _load_frames(con: duckdb.DuckDBPyConnection, filter_name: str, top_n: int, days: int | None) -> None:
    date_clause = (
        f"AND trade_date >= (SELECT MAX(trade_date) FROM candidates_scored) - INTERVAL {days} DAY"
        if days else ""
    )
    con.execute(
        f"""
        CREATE TEMP TABLE trades AS
        WITH ranked AS (
            SELECT trade_date, ticker, entry_price_0935, exit_price_1555,
                   -- pre-entry volatility proxy, clamped in config.py
                   {config.dynamic_vol_proxy_sql()} AS vol_proxy,
                   ROW_NUMBER() OVER (PARTITION BY trade_date ORDER BY ranking_score DESC) AS rn
            FROM candidates_scored
            WHERE ranking_score IS NOT NULL AND {FILTER_SQL[filter_name]} {date_clause}
        )
        SELECT * FROM ranked WHERE rn <= {top_n}
        """
    )
    n, vol = con.execute("SELECT COUNT(*), MEDIAN(vol_proxy) FROM trades").fetchone()
    _log.info(f"Simulating {n:,} trades ({filter_name}, top {top_n}"
              f"{f', last {days}d' if days else ''}) | median vol_proxy={vol:.2%}")

    con.execute(
        """
        CREATE TEMP TABLE trade_bars AS
        SELECT t.trade_date, t.ticker, t.entry_price_0935, t.vol_proxy,
               b.ts_ny_time, b.open, b.high, b.low
        FROM trades t
        JOIN halal_minute_bars b ON b.ticker = t.ticker AND b.trade_date = t.trade_date
        WHERE b.ts_ny_time >= TIME '09:35:00' AND b.ts_ny_time <= TIME '15:54:00'
        """
    )


def _metrics(df: pd.DataFrame, label: dict) -> dict:
    df["gross_return"] = (df["exit_price"] - df["entry_price_0935"]) / df["entry_price_0935"]
    df["net_return"] = df["gross_return"] - df["entry_price_0935"].map(config.cost_bps_for_price) / 10000.0
    daily = df.groupby("trade_date")["net_return"].mean().sort_index()
    equity = (1 + daily).cumprod()
    kinds = df["exit_kind"].value_counts(normalize=True)
    out = dict(label)
    out.update({
        "n_trades": len(df),
        "gross_avg_return": df["gross_return"].mean(),
        "net_avg_return": df["net_return"].mean(),
        "net_win_rate": (df["net_return"] > 0).mean(),
        "net_avg_daily_return": daily.mean(),
        "net_max_drawdown": (equity / equity.cummax() - 1).min(),
        "pct_exit_tp": kinds.get("tp", 0.0),
        "pct_exit_stop": kinds.get("stop", 0.0),
        "pct_exit_time": kinds.get("time", 0.0),
    })
    return out


def _simulate_scaled(con: duckdb.DuckDBPyConnection, k_tp: float | None, k_sl: float | None) -> dict:
    tp_level = f"entry_price_0935 * (1 + {k_tp} * vol_proxy)" if k_tp is not None else "NULL"
    sl_level = f"entry_price_0935 * (1 - {k_sl} * vol_proxy)" if k_sl is not None else "NULL"
    tp_hit = f"(high >= {tp_level})" if k_tp is not None else "FALSE"
    sl_hit = f"(low <= {sl_level})" if k_sl is not None else "FALSE"

    df = con.execute(
        f"""
        WITH touches AS (
            SELECT trade_date, ticker, ts_ny_time, open, entry_price_0935, vol_proxy,
                   {tp_hit} AS tp_hit, {sl_hit} AS sl_hit
            FROM trade_bars WHERE {tp_hit} OR {sl_hit}
        ),
        first_touch AS (
            SELECT *, ROW_NUMBER() OVER (PARTITION BY trade_date, ticker ORDER BY ts_ny_time) AS k
            FROM touches
        ),
        exits AS (
            SELECT trade_date, ticker,
                   CASE WHEN sl_hit THEN LEAST(open, {sl_level})
                        ELSE GREATEST(open, {tp_level}) END AS early_exit_price,
                   CASE WHEN sl_hit THEN 'stop' ELSE 'tp' END AS exit_kind
            FROM first_touch WHERE k = 1
        )
        SELECT t.trade_date, t.ticker, t.entry_price_0935,
               COALESCE(e.early_exit_price, t.exit_price_1555) AS exit_price,
               COALESCE(e.exit_kind, 'time') AS exit_kind
        FROM trades t
        LEFT JOIN exits e ON e.trade_date = t.trade_date AND e.ticker = t.ticker
        """
    ).fetchdf()
    return _metrics(df, {"mode": "scaled", "tp": f"{k_tp}x vol" if k_tp else "-",
                         "sl": f"{k_sl}x vol" if k_sl else "-"})


def _simulate_trail(
    con: duckdb.DuckDBPyConnection,
    trail_fixed: float | None,
    trail_mult: float | None,
    tp_pct: float | None,
) -> dict:
    trail_dist = f"{trail_fixed}" if trail_fixed is not None else f"{trail_mult} * vol_proxy"
    tp_level = f"entry_price_0935 * (1 + {tp_pct})" if tp_pct is not None else "NULL"
    tp_hit = f"(high >= {tp_level})" if tp_pct is not None else "FALSE"

    df = con.execute(
        f"""
        WITH seq AS (
            SELECT *,
                   -- high-water mark from PRIOR bars only (conservative: a bar's own
                   -- spike cannot raise the trail level used against that same bar)
                   GREATEST(
                       COALESCE(MAX(high) OVER (
                           PARTITION BY trade_date, ticker ORDER BY ts_ny_time
                           ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
                       ), entry_price_0935),
                       entry_price_0935
                   ) AS hwm
            FROM trade_bars
        ),
        touches AS (
            SELECT trade_date, ticker, ts_ny_time, open, entry_price_0935, vol_proxy,
                   hwm * (1 - ({trail_dist})) AS trail_level,
                   (low <= hwm * (1 - ({trail_dist}))) AS stop_hit,
                   {tp_hit} AS tp_hit
            FROM seq
            WHERE (low <= hwm * (1 - ({trail_dist}))) OR {tp_hit}
        ),
        first_touch AS (
            SELECT *, ROW_NUMBER() OVER (PARTITION BY trade_date, ticker ORDER BY ts_ny_time) AS k
            FROM touches
        ),
        exits AS (
            SELECT trade_date, ticker,
                   CASE WHEN stop_hit THEN LEAST(open, trail_level)
                        ELSE GREATEST(open, {tp_level}) END AS early_exit_price,
                   CASE WHEN stop_hit THEN 'stop' ELSE 'tp' END AS exit_kind
            FROM first_touch WHERE k = 1
        )
        SELECT t.trade_date, t.ticker, t.entry_price_0935,
               COALESCE(e.early_exit_price, t.exit_price_1555) AS exit_price,
               COALESCE(e.exit_kind, 'time') AS exit_kind
        FROM trades t
        LEFT JOIN exits e ON e.trade_date = t.trade_date AND e.ticker = t.ticker
        """
    ).fetchdf()
    trail_label = f"{trail_fixed:.0%} fixed" if trail_fixed is not None else f"{trail_mult}x vol"
    return _metrics(df, {"mode": "trail", "tp": f"{tp_pct:.0%}" if tp_pct else "-",
                         "sl": f"trail {trail_label}"})


def run(mode: str, filter_name: str, top_n: int, days: int | None) -> None:
    os.makedirs(config.REPORT_DIR, exist_ok=True)
    suffix = f"_{filter_name}_top{top_n}" + (f"_last{days}d" if days else "")
    out_path = os.path.join(config.REPORT_DIR, f"dynamic_tp_sl{suffix}.csv")

    con = duckdb.connect(config.OUTPUT_DB_PATH, read_only=True)
    try:
        _load_frames(con, filter_name, top_n, days)
        rows = [_simulate_scaled(con, None, None)]                          
        rows[0]["mode"] = "baseline"

        if mode in ("scaled", "both"):
            for k_tp in TP_MULTS:
                for k_sl in SL_MULTS:
                    if k_tp is None and k_sl is None:
                        continue
                    rows.append(_simulate_scaled(con, k_tp, k_sl))

        if mode in ("trail", "both"):
            for tp_pct in TRAIL_TP_OPTIONS:
                for d in TRAIL_FIXED:
                    rows.append(_simulate_trail(con, d, None, tp_pct))
                for m in TRAIL_MULTS:
                    rows.append(_simulate_trail(con, None, m, tp_pct))

        for r in rows:
            _log.info(f"[{r['mode']:>8s} tp={r['tp']:>8s} sl={r['sl']:>14s}] "
                      f"net_avg={r['net_avg_return']:+.4f} win={r['net_win_rate']:.1%} "
                      f"dd={r['net_max_drawdown']:+.1%} exits tp/stop/time="
                      f"{r['pct_exit_tp']:.0%}/{r['pct_exit_stop']:.0%}/{r['pct_exit_time']:.0%}")
    finally:
        con.close()

    result = pd.DataFrame(rows).sort_values("net_avg_return", ascending=False)
    result.to_csv(out_path, index=False)
    _log.info(f"Wrote {out_path}")
    best, base = result.iloc[0], result[result["mode"] == "baseline"].iloc[0]
    _log.info(f"Best: [{best['mode']} tp={best['tp']} sl={best['sl']}] net_avg={best['net_avg_return']:+.4f} "
              f"vs baseline {base['net_avg_return']:+.4f}")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--mode", choices=["scaled", "trail", "both"], default="both")
    p.add_argument("--filter", choices=list(FILTER_SQL), default="risk_filter")
    p.add_argument("--top", type=int, default=5)
    p.add_argument("--days", type=int, default=None)
    args = p.parse_args()
    run(args.mode, args.filter, args.top, args.days)
