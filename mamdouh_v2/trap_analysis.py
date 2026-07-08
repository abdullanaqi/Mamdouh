"""Stage 11 (+ outcome-only post-entry fields): trap / deception analysis.

Everything in this module is OUTCOME-ONLY - it can only be computed after
15:55 (post_entry_outcome_features) or depends on columns that are
themselves outcome-only (return_0935_to_1555). Nothing here may ever be
selected into ranking_model.py / risk_model.py's feature matrix; see
config.FORBIDDEN_COLUMNS and leakage_checks.py which asserts this.

Trap label thresholds are the percentile-based heuristics defined in
config.py (MOMENTUM_TOP_QUINTILE/DECILE, VOLUME_TOP_QUINTILE,
LIQUIDITY_BOTTOM_QUINTILE, RETURN_SMALL_ABS_TERCILE). They are intentionally
expressed as within-day percentiles, never fixed price/volume levels, so
they adapt to each day's regime. analysis.py / the final report state which
of these are actually borne out by the data versus merely a candidate rule.

Definitions (all evaluated only for included_in_daily_study rows):
  looked_strong_before_entry_but_lost           top-quintile pre-entry momentum, negative final return
  high_pre_entry_momentum_then_negative_return  top-decile pre-entry momentum, negative final return
  positive_news_but_negative_return             had positive news pre-entry, negative final return
  high_volume_before_entry_but_failed           top-quintile pre-entry volume, negative final return
  early_spike_then_fade                         top-decile post-entry favorable excursion, closed flat/negative
  large_gain_given_back_after_entry             top-decile "give-back" (favorable excursion minus final return)
  green_at_entry_red_by_exit                    positive pre-entry momentum, negative final return
  strong_first_5min_but_closed_weak             top-quartile first-5-minute return, closed flat/negative
  low_liquidity_false_move                      bottom-quintile pre-entry $ volume, top-quintile |pre-entry move|
  repeated_news_but_no_follow_through           2+ pre-entry news items, bottom-tercile |final return|
  broad_news_misleading_ticker_reaction         broad/syndicated news pointed one way, price went the other
  possible_pump_and_fade_pattern                composite: spike-fade OR (momentum+volume failure) OR premarket-gap fade
  possible_buyer_trap_pattern                   composite: looked-strong-but-lost AND a confirming signal (news/volume/gap)
  premarket_pump_faded_at_open (bonus)          top-decile premarket gap up, negative final return
"""
from __future__ import annotations

import duckdb

import config
from db import skip_if_exists

_log = config.setup_logging(__name__)


def build_post_entry_outcome_features(con: duckdb.DuckDBPyConnection, force: bool = False) -> None:
    """Outcome-only: max favorable/adverse excursion while the position is
    held (09:35 open to 15:54 close - i.e. strictly before the 15:55 exit),
    and the return after the first 5 held minutes (09:35-09:39)."""
    if skip_if_exists(con, "post_entry_outcome_features", force, "Stage 11a"):
        return

    _log.info("Building post-entry outcome features (max favorable/adverse move, first-5min return)")
    con.execute(
        f"""
        CREATE OR REPLACE TABLE post_entry_outcome_features AS
        WITH hold_bars AS (
            SELECT ticker, trade_date, high, low
            FROM halal_minute_bars
            WHERE ts_ny_time BETWEEN TIME '{config.ENTRY_TIME}' AND TIME '15:54:00'
        ),
        hold_agg AS (
            SELECT ticker, trade_date, MAX(high) AS post_entry_high, MIN(low) AS post_entry_low
            FROM hold_bars GROUP BY ticker, trade_date
        ),
        first5 AS (
            SELECT ticker, trade_date, close AS close_first_5min
            FROM halal_minute_bars
            WHERE ts_ny_time = TIME '{config.FIRST_5MIN_END}'
        )
        SELECT
            e.ticker,
            e.trade_date,
            CASE WHEN e.has_entry_price AND h.post_entry_high IS NOT NULL AND e.entry_price_0935 != 0
                 THEN (h.post_entry_high - e.entry_price_0935) / e.entry_price_0935 END AS max_favorable_move_after_entry,
            CASE WHEN e.has_entry_price AND h.post_entry_low IS NOT NULL AND e.entry_price_0935 != 0
                 THEN (h.post_entry_low - e.entry_price_0935) / e.entry_price_0935 END AS max_adverse_move_after_entry,
            CASE WHEN e.has_entry_price AND f.close_first_5min IS NOT NULL AND e.entry_price_0935 != 0
                 THEN (f.close_first_5min - e.entry_price_0935) / e.entry_price_0935 END AS first_5min_return_after_entry
        FROM entry_exit_prices e
        LEFT JOIN hold_agg h ON h.ticker = e.ticker AND h.trade_date = e.trade_date
        LEFT JOIN first5 f ON f.ticker = e.ticker AND f.trade_date = e.trade_date
        """
    )
    n = con.execute("SELECT COUNT(*) FROM post_entry_outcome_features").fetchone()[0]
    _log.info(f"post_entry_outcome_features: {n:,} rows")


def build_trap_labels(con: duckdb.DuckDBPyConnection, force: bool = False) -> None:
    if skip_if_exists(con, "trap_labels", force, "Stage 11b"):
        return

    _log.info("Deriving trap/deception labels (outcome-only)")
    con.execute(
        f"""
        CREATE OR REPLACE TABLE trap_labels AS
        WITH d AS (
            SELECT
                e.ticker, e.trade_date,
                r.return_0935_to_1555,
                pf.return_0930_to_entry,
                pf.pre_entry_momentum_percentile,
                pf.pre_entry_volume_percentile,
                pf.pre_entry_dollar_volume_percentile,
                pf.premarket_gap_percentile,
                nf.has_positive_news_before_entry,
                nf.has_negative_news_before_entry,
                nf.news_count_before_entry,
                nf.is_broad_news,
                po.max_favorable_move_after_entry,
                po.first_5min_return_after_entry
            FROM eligibility e
            JOIN ranking_labels r ON r.ticker = e.ticker AND r.trade_date = e.trade_date
            LEFT JOIN pre_entry_price_features pf ON pf.ticker = e.ticker AND pf.trade_date = e.trade_date
            LEFT JOIN pre_entry_news_features nf ON nf.ticker = e.ticker AND nf.trade_date = e.trade_date
            LEFT JOIN post_entry_outcome_features po ON po.ticker = e.ticker AND po.trade_date = e.trade_date
            WHERE e.included_in_daily_study
        ),
        pcts AS (
            SELECT
                ticker, trade_date,
                PERCENT_RANK() OVER (PARTITION BY trade_date ORDER BY max_favorable_move_after_entry) AS favorable_move_percentile,
                PERCENT_RANK() OVER (PARTITION BY trade_date ORDER BY (max_favorable_move_after_entry - return_0935_to_1555)) AS giveback_percentile,
                PERCENT_RANK() OVER (PARTITION BY trade_date ORDER BY first_5min_return_after_entry) AS first_5min_percentile,
                PERCENT_RANK() OVER (PARTITION BY trade_date ORDER BY ABS(return_0930_to_entry)) AS abs_pre_entry_momentum_percentile,
                PERCENT_RANK() OVER (PARTITION BY trade_date ORDER BY ABS(return_0935_to_1555)) AS abs_final_return_percentile
            FROM d
        )
        SELECT
            d.ticker,
            d.trade_date,
            (d.pre_entry_momentum_percentile >= {config.MOMENTUM_TOP_QUINTILE} AND d.return_0935_to_1555 < 0)
                AS looked_strong_before_entry_but_lost,
            (d.pre_entry_momentum_percentile >= {config.MOMENTUM_TOP_DECILE} AND d.return_0935_to_1555 < 0)
                AS high_pre_entry_momentum_then_negative_return,
            (d.has_positive_news_before_entry AND d.return_0935_to_1555 < 0)
                AS positive_news_but_negative_return,
            (d.pre_entry_volume_percentile >= {config.VOLUME_TOP_QUINTILE} AND d.return_0935_to_1555 < 0)
                AS high_volume_before_entry_but_failed,
            (p.favorable_move_percentile >= {config.MOMENTUM_TOP_DECILE} AND d.return_0935_to_1555 <= 0)
                AS early_spike_then_fade,
            (p.giveback_percentile >= {config.MOMENTUM_TOP_DECILE})
                AS large_gain_given_back_after_entry,
            (d.return_0930_to_entry > 0 AND d.return_0935_to_1555 < 0)
                AS green_at_entry_red_by_exit,
            (p.first_5min_percentile >= 0.75 AND d.return_0935_to_1555 <= 0)
                AS strong_first_5min_but_closed_weak,
            (d.pre_entry_dollar_volume_percentile <= {config.LIQUIDITY_BOTTOM_QUINTILE}
                AND p.abs_pre_entry_momentum_percentile >= {config.MOMENTUM_TOP_QUINTILE})
                AS low_liquidity_false_move,
            (d.news_count_before_entry >= 2 AND p.abs_final_return_percentile <= {config.RETURN_SMALL_ABS_TERCILE})
                AS repeated_news_but_no_follow_through,
            (d.is_broad_news AND (
                (d.has_positive_news_before_entry AND d.return_0935_to_1555 < 0)
                OR (d.has_negative_news_before_entry AND d.return_0935_to_1555 > 0)
            )) AS broad_news_misleading_ticker_reaction,
            (
                (p.favorable_move_percentile >= {config.MOMENTUM_TOP_DECILE} AND d.return_0935_to_1555 <= 0)
                OR (d.pre_entry_momentum_percentile >= {config.MOMENTUM_TOP_DECILE}
                    AND d.pre_entry_volume_percentile >= {config.VOLUME_TOP_QUINTILE}
                    AND d.return_0935_to_1555 < 0)
                OR (d.premarket_gap_percentile >= {config.MOMENTUM_TOP_DECILE} AND d.return_0935_to_1555 < 0)
            ) AS possible_pump_and_fade_pattern,
            (
                (d.pre_entry_momentum_percentile >= {config.MOMENTUM_TOP_QUINTILE} AND d.return_0935_to_1555 < 0)
                AND (
                    d.has_positive_news_before_entry
                    OR d.pre_entry_volume_percentile >= {config.VOLUME_TOP_QUINTILE}
                    OR d.premarket_gap_percentile >= {config.MOMENTUM_TOP_QUINTILE}
                )
            ) AS possible_buyer_trap_pattern,
            (d.premarket_gap_percentile >= {config.MOMENTUM_TOP_DECILE} AND d.return_0935_to_1555 < 0)
                AS premarket_pump_faded_at_open
        FROM d
        JOIN pcts p ON p.ticker = d.ticker AND p.trade_date = d.trade_date
        """
    )
    row = con.execute(
        """
        SELECT COUNT(*), SUM(possible_buyer_trap_pattern::INT), SUM(possible_pump_and_fade_pattern::INT)
        FROM trap_labels
        """
    ).fetchone()
    _log.info(f"trap_labels: {row[0]:,} rows | possible_buyer_trap_pattern={row[1]:,} | possible_pump_and_fade_pattern={row[2]:,}")


def build_trap_summaries(con: duckdb.DuckDBPyConnection, force: bool = False) -> None:
    """Aggregate trap-pattern analysis tables answering the Stage-11 questions:
    which tickers/sources/price-ranges/liquidity deciles are trap-prone.

    Reads from the finalized halal_daily_candidates table, so this must run
    after build_candidates.finalize_halal_daily_candidates().
    """
    if skip_if_exists(con, "trap_summary_by_ticker", force, "Stage 11c"):
        return

    _log.info("Building trap-pattern aggregate summaries (by ticker, source, price range, liquidity/momentum decile)")

    con.execute(
        """
        CREATE OR REPLACE TABLE trap_summary_by_ticker AS
        SELECT
            c.ticker,
            COUNT(*) AS n_days,
            AVG(c.return_0935_to_1555) AS avg_return,
            SUM(c.possible_buyer_trap_pattern::INT) AS n_buyer_trap_days,
            SUM(c.possible_pump_and_fade_pattern::INT) AS n_pump_and_fade_days,
            AVG(c.possible_buyer_trap_pattern::INT) AS buyer_trap_rate,
            AVG(c.possible_pump_and_fade_pattern::INT) AS pump_and_fade_rate,
            AVG(c.looked_strong_before_entry_but_lost::INT) AS looked_strong_but_lost_rate
        FROM halal_daily_candidates c
        WHERE c.included_in_daily_study
        GROUP BY c.ticker
        HAVING COUNT(*) >= 5
        ORDER BY buyer_trap_rate DESC
        """
    )

    con.execute(
        """
        CREATE OR REPLACE TABLE trap_summary_by_source AS
        SELECT
            c.latest_news_source_before_entry AS source,
            COUNT(*) AS n_days,
            AVG(c.return_0935_to_1555) AS avg_return,
            AVG(c.positive_news_but_negative_return::INT) AS positive_news_false_positive_rate,
            AVG(c.possible_buyer_trap_pattern::INT) AS buyer_trap_rate,
            AVG(c.broad_news_misleading_ticker_reaction::INT) AS broad_news_misleading_rate
        FROM halal_daily_candidates c
        WHERE c.included_in_daily_study AND c.has_news_before_entry
        GROUP BY c.latest_news_source_before_entry
        HAVING COUNT(*) >= 5
        ORDER BY buyer_trap_rate DESC
        """
    )

    con.execute(
        """
        CREATE OR REPLACE TABLE trap_summary_by_price_bucket AS
        SELECT
            CASE
                WHEN entry_price_0935 < 10 THEN '05-10'
                WHEN entry_price_0935 < 20 THEN '10-20'
                WHEN entry_price_0935 < 50 THEN '20-50'
                WHEN entry_price_0935 < 100 THEN '50-100'
                ELSE '100+'
            END AS price_bucket,
            COUNT(*) AS n_days,
            AVG(return_0935_to_1555) AS avg_return,
            STDDEV(return_0935_to_1555) AS stddev_return,
            AVG(possible_buyer_trap_pattern::INT) AS buyer_trap_rate,
            AVG(possible_pump_and_fade_pattern::INT) AS pump_and_fade_rate,
            AVG(is_top_10_percent::INT) AS top_10_pct_rate
        FROM halal_daily_candidates
        WHERE included_in_daily_study
        GROUP BY 1
        ORDER BY 1
        """
    )

    con.execute(
        """
        CREATE OR REPLACE TABLE trap_summary_by_liquidity_decile AS
        SELECT
            CAST(FLOOR(pre_entry_dollar_volume_percentile * 10) AS INT) AS liquidity_decile,
            COUNT(*) AS n_days,
            AVG(return_0935_to_1555) AS avg_return,
            STDDEV(return_0935_to_1555) AS stddev_return,
            AVG(possible_buyer_trap_pattern::INT) AS buyer_trap_rate,
            AVG(low_liquidity_false_move::INT) AS low_liquidity_false_move_rate,
            AVG(is_top_10_percent::INT) AS top_10_pct_rate
        FROM halal_daily_candidates
        WHERE included_in_daily_study AND pre_entry_dollar_volume_percentile IS NOT NULL
        GROUP BY 1
        ORDER BY 1
        """
    )

    con.execute(
        """
        CREATE OR REPLACE TABLE trap_summary_by_momentum_decile AS
        SELECT
            CAST(FLOOR(pre_entry_momentum_percentile * 10) AS INT) AS momentum_decile,
            COUNT(*) AS n_days,
            AVG(return_0935_to_1555) AS avg_return,
            STDDEV(return_0935_to_1555) AS stddev_return,
            AVG(looked_strong_before_entry_but_lost::INT) AS looked_strong_but_lost_rate,
            AVG(is_winner::INT) AS win_rate,
            AVG(is_top_10_percent::INT) AS top_10_pct_rate
        FROM halal_daily_candidates
        WHERE included_in_daily_study AND pre_entry_momentum_percentile IS NOT NULL
        GROUP BY 1
        ORDER BY 1
        """
    )

    _log.info("Built trap_summary_by_ticker / _source / _price_bucket / _liquidity_decile / _momentum_decile")


def build_pre_candidates(con: duckdb.DuckDBPyConnection, force: bool = False) -> None:
    """Everything in this module that must run BEFORE halal_daily_candidates
    is assembled (post_entry_outcome_features and trap_labels are joined
    into it). build_trap_summaries() runs AFTER, since it reads the
    finalized table - call it separately from main.py."""
    build_post_entry_outcome_features(con, force)
    build_trap_labels(con, force)
