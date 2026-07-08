"""Export trade-level backtest results to CSV.

Exports the baseline strategy's daily selections (top-N by ranking_score)
over the last --days calendar days, with gross and cost-adjusted returns,
model scores, and pre-entry context per trade - plus a daily portfolio
summary. Both files land in reports/.

Usage:
    python export_backtest.py                  # last 30 days, top 5
    python export_backtest.py --days 60 --top 10
"""
from __future__ import annotations

import argparse
import os

import duckdb

import config

_log = config.setup_logging(__name__)


def export(days: int, top_n: int) -> None:
    os.makedirs(config.REPORT_DIR, exist_ok=True)
    trades_path = os.path.join(config.REPORT_DIR, f"backtest_trades_last_{days}d.csv")
    daily_path = os.path.join(config.REPORT_DIR, f"backtest_daily_last_{days}d.csv")

                                                                         
                                         
    con = duckdb.connect(config.OUTPUT_DB_PATH, read_only=True)
    try:
        trades = con.execute(
            f"""
            WITH ranked AS (
                SELECT
                    trade_date, ticker,
                    ROW_NUMBER() OVER (PARTITION BY trade_date ORDER BY ranking_score DESC) AS rank_in_day,
                    entry_price_0935, exit_price_1555,
                    return_0935_to_1555 AS gross_return,
                    {config.dynamic_vol_proxy_sql()} AS dynamic_vol_proxy,
                    entry_price_0935 * (1 + {config.DYNAMIC_TP_VOL_MULT} * ({config.dynamic_vol_proxy_sql()})) AS dynamic_tp_price,
                    entry_price_0935 * (1 - {config.DYNAMIC_SL_VOL_MULT} * ({config.dynamic_vol_proxy_sql()})) AS dynamic_sl_price,
                    ranking_score, risk_score, risk_percentile,
                    (risk_score IS NULL OR risk_percentile < 0.75) AS passes_risk_filter,
                    pre_entry_momentum_percentile, pre_entry_dollar_volume_percentile,
                    gap_pct_premarket_vs_prior_close AS premarket_gap,
                    has_news_before_entry, latest_news_source_before_entry AS news_source,
                    latest_news_sentiment_before_entry AS news_sentiment,
                    latest_news_title_before_entry AS news_title
                FROM candidates_scored
                WHERE ranking_score IS NOT NULL
                  AND trade_date >= (SELECT MAX(trade_date) FROM candidates_scored) - INTERVAL {days} DAY
            )
            SELECT * FROM ranked WHERE rank_in_day <= {top_n}
            ORDER BY trade_date, rank_in_day
            """
        ).fetchdf()

                                                                                
        trades["cost_bps"] = trades["entry_price_0935"].map(config.cost_bps_for_price)
        trades["net_return"] = trades["gross_return"] - trades["cost_bps"] / 10000.0
        trades["win_gross"] = trades["gross_return"] > 0
        trades["win_net"] = trades["net_return"] > 0
        trades.to_csv(trades_path, index=False)

        daily = (
            trades.groupby("trade_date")
            .agg(
                n_trades=("ticker", "count"),
                gross_portfolio_return=("gross_return", "mean"),
                net_portfolio_return=("net_return", "mean"),
                gross_win_rate=("win_gross", "mean"),
                risk_filter_passed=("passes_risk_filter", "sum"),
            )
            .reset_index()
        )
        daily_rf = (
            trades[trades["passes_risk_filter"]]
            .groupby("trade_date")["net_return"]
            .mean()
            .rename("net_return_risk_filtered")
            .reset_index()
        )
        daily = daily.merge(daily_rf, on="trade_date", how="left")
        daily.to_csv(daily_path, index=False)

        _log.info(f"{len(trades):,} trades over {trades['trade_date'].nunique()} days "
                  f"({trades['trade_date'].min()} to {trades['trade_date'].max()})")
        _log.info(f"Wrote {trades_path}")
        _log.info(f"Wrote {daily_path}")
    finally:
        con.close()


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--days", type=int, default=30, help="Calendar days back from the latest scored date")
    p.add_argument("--top", type=int, default=5, help="Top-N candidates per day")
    args = p.parse_args()
    export(args.days, args.top)
