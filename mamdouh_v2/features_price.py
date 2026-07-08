"""Stage 6: pre-entry price/momentum/liquidity features.

Two pre-entry windows are used, both strictly before the 09:35 entry bar:
  - premarket:  04:00:00 - 09:29:00 (extended-hours session)
  - opening:    09:30:00 - 09:34:00 (regular session, up to the minute before entry)

Everything here is computable at or before 09:35 - nothing reaches into the
09:35 bar itself or beyond. Same-day percentiles are computed only among
rows flagged included_in_daily_study in the `eligibility` table, so the
cross-section being ranked matches the cross-section actually tradeable
that day.
"""
from __future__ import annotations

import duckdb

import config
from db import skip_if_exists

_log = config.setup_logging(__name__)


def build(con: duckdb.DuckDBPyConnection, force: bool = False) -> None:
    if skip_if_exists(con, "pre_entry_price_features", force, "Stage 6"):
        return

    _log.info("Building pre-entry price/momentum/liquidity features (premarket + opening window)")
    con.execute(
        f"""
        CREATE OR REPLACE TABLE pre_entry_price_features AS
        WITH opening_bars AS (
            SELECT ticker, trade_date, ts_ny_time, open, high, low, close, volume, transactions
            FROM halal_minute_bars
            WHERE ts_ny_time BETWEEN TIME '{config.PRE_ENTRY_START}' AND TIME '{config.PRE_ENTRY_END}'
        ),
        opening_agg AS (
            SELECT
                ticker, trade_date,
                arg_min(open, ts_ny_time)  AS open_0930,
                arg_max(close, ts_ny_time) AS close_0934,
                MAX(high) AS high_0930_to_before_entry,
                MIN(low)  AS low_0930_to_before_entry,
                SUM(volume) AS volume_0930_to_entry,
                SUM(transactions) AS transactions_0930_to_entry,
                SUM(volume * close) AS dollar_volume_0930_to_entry,
                COUNT(*) AS opening_bar_count
            FROM opening_bars
            GROUP BY ticker, trade_date
        ),
        premarket_bars AS (
            SELECT ticker, trade_date, ts_ny_time, open, high, low, close, volume, transactions
            FROM halal_minute_bars
            WHERE ts_ny_time BETWEEN TIME '{config.PREMARKET_START}' AND TIME '{config.PREMARKET_END}'
        ),
        premarket_agg AS (
            SELECT
                ticker, trade_date,
                arg_min(open, ts_ny_time)  AS premarket_open,
                arg_max(close, ts_ny_time) AS premarket_last_price,
                MAX(high) AS premarket_high,
                MIN(low)  AS premarket_low,
                SUM(volume) AS premarket_volume,
                SUM(transactions) AS premarket_transactions,
                SUM(volume * close) AS premarket_dollar_volume,
                COUNT(*) AS premarket_bar_count
            FROM premarket_bars
            GROUP BY ticker, trade_date
        ),
        regular_session_close AS (
            SELECT ticker, trade_date, close AS session_close
            FROM halal_minute_bars
            WHERE ts_ny_time = TIME '{config.REGULAR_CLOSE_BAR}'
        ),
        prior_close AS (
            SELECT
                ticker, trade_date,
                LAG(session_close) OVER (PARTITION BY ticker ORDER BY trade_date) AS prior_day_close
            FROM regular_session_close
        ),
        combined AS (
            SELECT
                u.ticker,
                u.trade_date,
                oa.open_0930,
                oa.high_0930_to_before_entry,
                oa.low_0930_to_before_entry,
                oa.volume_0930_to_entry,
                oa.transactions_0930_to_entry,
                oa.dollar_volume_0930_to_entry,
                oa.opening_bar_count,
                CASE WHEN oa.opening_bar_count > 0
                     THEN oa.volume_0930_to_entry::DOUBLE / oa.opening_bar_count END AS average_volume_per_minute_0930_to_entry,
                CASE WHEN oa.opening_bar_count > 0
                     THEN oa.transactions_0930_to_entry::DOUBLE / oa.opening_bar_count END AS average_transactions_per_minute_0930_to_entry,
                CASE WHEN eep.entry_price_0935 IS NOT NULL AND oa.open_0930 IS NOT NULL AND oa.open_0930 != 0
                     THEN (eep.entry_price_0935 - oa.open_0930) / oa.open_0930 END AS return_0930_to_entry,
                CASE WHEN oa.high_0930_to_before_entry IS NOT NULL
                          AND oa.high_0930_to_before_entry != oa.low_0930_to_before_entry
                     THEN (oa.close_0934 - oa.low_0930_to_before_entry)
                          / (oa.high_0930_to_before_entry - oa.low_0930_to_before_entry) END AS close_position_inside_pre_entry_range,

                pa.premarket_open,
                pa.premarket_last_price,
                pa.premarket_high,
                pa.premarket_low,
                pa.premarket_volume,
                pa.premarket_transactions,
                pa.premarket_dollar_volume,
                COALESCE(pa.premarket_bar_count, 0) AS premarket_bar_count,
                (pa.premarket_bar_count IS NOT NULL AND pa.premarket_bar_count > 0) AS has_premarket_data,
                CASE WHEN pa.premarket_high IS NOT NULL AND pa.premarket_high != pa.premarket_low
                     THEN (pa.premarket_last_price - pa.premarket_low) / (pa.premarket_high - pa.premarket_low) END AS premarket_range_position,
                CASE WHEN pc.prior_day_close IS NOT NULL AND pc.prior_day_close != 0 AND pa.premarket_last_price IS NOT NULL
                     THEN (pa.premarket_last_price - pc.prior_day_close) / pc.prior_day_close END AS gap_pct_premarket_vs_prior_close,
                CASE WHEN pc.prior_day_close IS NOT NULL AND pc.prior_day_close != 0 AND oa.open_0930 IS NOT NULL
                     THEN (oa.open_0930 - pc.prior_day_close) / pc.prior_day_close END AS gap_pct_open_vs_prior_close
            FROM trading_days_universe u
            LEFT JOIN opening_agg oa   ON oa.ticker = u.ticker AND oa.trade_date = u.trade_date
            LEFT JOIN premarket_agg pa ON pa.ticker = u.ticker AND pa.trade_date = u.trade_date
            LEFT JOIN prior_close pc   ON pc.ticker = u.ticker AND pc.trade_date = u.trade_date
            LEFT JOIN entry_exit_prices eep ON eep.ticker = u.ticker AND eep.trade_date = u.trade_date
        ),
        eligible_only AS (
            SELECT c.*
            FROM combined c
            JOIN eligibility e ON e.ticker = c.ticker AND e.trade_date = c.trade_date
            WHERE e.included_in_daily_study
        ),
        percentiles AS (
            SELECT
                ticker, trade_date,
                PERCENT_RANK() OVER (PARTITION BY trade_date ORDER BY return_0930_to_entry)        AS pre_entry_momentum_percentile,
                PERCENT_RANK() OVER (PARTITION BY trade_date ORDER BY volume_0930_to_entry)         AS pre_entry_volume_percentile,
                PERCENT_RANK() OVER (PARTITION BY trade_date ORDER BY dollar_volume_0930_to_entry)  AS pre_entry_dollar_volume_percentile,
                PERCENT_RANK() OVER (PARTITION BY trade_date ORDER BY transactions_0930_to_entry)   AS pre_entry_transactions_percentile,
                PERCENT_RANK() OVER (PARTITION BY trade_date ORDER BY premarket_volume)             AS premarket_volume_percentile,
                PERCENT_RANK() OVER (PARTITION BY trade_date ORDER BY premarket_dollar_volume)      AS premarket_dollar_volume_percentile,
                PERCENT_RANK() OVER (PARTITION BY trade_date ORDER BY gap_pct_premarket_vs_prior_close) AS premarket_gap_percentile
            FROM eligible_only
        )
        SELECT
            c.ticker,
            c.trade_date,
            c.open_0930,
            c.high_0930_to_before_entry,
            c.low_0930_to_before_entry,
            c.return_0930_to_entry,
            c.volume_0930_to_entry,
            c.transactions_0930_to_entry,
            c.dollar_volume_0930_to_entry,
            c.average_volume_per_minute_0930_to_entry,
            c.average_transactions_per_minute_0930_to_entry,
            c.close_position_inside_pre_entry_range,
            c.premarket_open,
            c.premarket_last_price,
            c.premarket_high,
            c.premarket_low,
            c.premarket_volume,
            c.premarket_transactions,
            c.premarket_dollar_volume,
            c.premarket_bar_count,
            c.has_premarket_data,
            c.premarket_range_position,
            c.gap_pct_premarket_vs_prior_close,
            c.gap_pct_open_vs_prior_close,
            p.pre_entry_momentum_percentile,
            p.pre_entry_volume_percentile,
            p.pre_entry_dollar_volume_percentile,
            p.pre_entry_transactions_percentile,
            p.premarket_volume_percentile,
            p.premarket_dollar_volume_percentile,
            p.premarket_gap_percentile
        FROM combined c
        LEFT JOIN percentiles p ON p.ticker = c.ticker AND p.trade_date = c.trade_date
        """
    )
    n = con.execute("SELECT COUNT(*) FROM pre_entry_price_features").fetchone()[0]
    n_pm = con.execute("SELECT SUM(has_premarket_data::INT) FROM pre_entry_price_features").fetchone()[0]
    _log.info(f"pre_entry_price_features: {n:,} rows | has_premarket_data={n_pm:,}")
