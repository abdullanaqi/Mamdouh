"""Stage 3-5 and final assembly (Stage 8): session filtering, entry/exit
price extraction, basic eligibility, and joining everything into the final
halal_daily_candidates table.

Pipeline of tables built here:
  halal_minute_bars       (Stage 3) halal-only, regular-session (09:30-16:00) bars
  trading_days_universe   (helper)  distinct (ticker, trade_date) with any session data
  entry_exit_prices       (Stage 4) entry@09:35 / exit@15:55 open prices
  eligibility             (Stage 5) basic eligibility flags, NO liquidity filter
  halal_daily_candidates  (Stage 8, finalized after Stages 6/7/9/11 have run)
"""
from __future__ import annotations

import duckdb

import config
from db import skip_if_exists

_log = config.setup_logging(__name__)


def build_halal_minute_bars(con: duckdb.DuckDBPyConnection, force: bool = False) -> None:
    """Stage 3: filter minute_bars early to halal tickers + regular session."""
    if skip_if_exists(con, "halal_minute_bars", force, "Stage 3"):
        return

    _log.info("Building halal_minute_bars (halal tickers x regular session window)")
    con.execute(
        f"""
        CREATE OR REPLACE TABLE halal_minute_bars AS
        SELECT
            m.ticker,
            CAST(m.ts_ny AS DATE) AS trade_date,
            m.ts_ny,
            m.ts_utc,
            CAST(m.ts_ny AS TIME) AS ts_ny_time,
            m.open, m.high, m.low, m.close, m.volume, m.transactions
        FROM src.minute_bars m
        JOIN halal_universe h ON h.ticker = m.ticker
        WHERE CAST(m.ts_ny AS TIME) >= TIME '{config.SESSION_START}'
          AND CAST(m.ts_ny AS TIME) <  TIME '{config.SESSION_END}'
        ORDER BY m.ticker, m.ts_ny
        """
    )
    n = con.execute("SELECT COUNT(*) FROM halal_minute_bars").fetchone()[0]
    n_tickers = con.execute("SELECT COUNT(DISTINCT ticker) FROM halal_minute_bars").fetchone()[0]
    n_days = con.execute("SELECT COUNT(DISTINCT trade_date) FROM halal_minute_bars").fetchone()[0]
    _log.info(f"halal_minute_bars: {n:,} rows, {n_tickers:,} tickers, {n_days:,} trading days")


def build_trading_days_universe(con: duckdb.DuckDBPyConnection, force: bool = False) -> None:
    """Helper: every (ticker, trade_date) with any session-window data at all.

    This is the 'study universe' spine - per the spec we study every halal
    ticker that has usable data that day, not a forced full cross-product of
    all tickers x all days.
    """
    if skip_if_exists(con, "trading_days_universe", force, "Stage 3b"):
        return

    con.execute(
        """
        CREATE OR REPLACE TABLE trading_days_universe AS
        SELECT DISTINCT ticker, trade_date FROM halal_minute_bars
        """
    )
    n = con.execute("SELECT COUNT(*) FROM trading_days_universe").fetchone()[0]
    _log.info(f"trading_days_universe: {n:,} (ticker, trade_date) pairs")


def build_entry_exit_prices(con: duckdb.DuckDBPyConnection, force: bool = False) -> None:
    """Stage 4: entry price = OPEN of the 09:35:00 bar, exit price = OPEN of
    the 15:55:00 bar. Using OPEN on both sides keeps the convention
    symmetric: each price is "known at exactly that wall-clock instant,"
    with zero look-ahead into the rest of that minute. exit_price_1555 is an
    outcome-only value (never usable as a ranking feature).
    """
    if skip_if_exists(con, "entry_exit_prices", force, "Stage 4"):
        return

    _log.info("Extracting entry (open@09:35) and exit (open@15:55) prices")
    con.execute(
        f"""
        CREATE OR REPLACE TABLE entry_exit_prices AS
        SELECT
            u.ticker,
            u.trade_date,
            e.entry_price_0935,
            e.entry_ts_utc,
            x.exit_price_1555,
            (e.entry_price_0935 IS NOT NULL) AS has_entry_price,
            (x.exit_price_1555 IS NOT NULL) AS has_exit_price
        FROM trading_days_universe u
        LEFT JOIN (
            SELECT ticker, trade_date, open AS entry_price_0935, ts_utc AS entry_ts_utc
            FROM halal_minute_bars WHERE ts_ny_time = TIME '{config.ENTRY_TIME}'
        ) e ON e.ticker = u.ticker AND e.trade_date = u.trade_date
        LEFT JOIN (
            SELECT ticker, trade_date, open AS exit_price_1555
            FROM halal_minute_bars WHERE ts_ny_time = TIME '{config.EXIT_TIME}'
        ) x ON x.ticker = u.ticker AND x.trade_date = u.trade_date
        """
    )
    row = con.execute(
        "SELECT COUNT(*), SUM(has_entry_price::INT), SUM(has_exit_price::INT) FROM entry_exit_prices"
    ).fetchone()
    _log.info(f"entry_exit_prices: {row[0]:,} rows | has_entry_price={row[1]:,} | has_exit_price={row[2]:,}")


def build_eligibility(con: duckdb.DuckDBPyConnection, force: bool = False) -> None:
    """Stage 5: basic eligibility only - halal, valid entry/exit, price >= $5.
    Deliberately NO liquidity filter here (spec requirement)."""
    if skip_if_exists(con, "eligibility", force, "Stage 5"):
        return

    _log.info("Applying basic eligibility (halal, has entry/exit, price >= $5) - no liquidity filter")
    con.execute(
        f"""
        CREATE OR REPLACE TABLE eligibility AS
        SELECT
            ticker,
            trade_date,
            TRUE AS is_halal,
            has_entry_price,
            has_exit_price,
            (has_entry_price AND entry_price_0935 >= {config.MIN_ENTRY_PRICE}) AS price_above_5_at_entry,
            (
                has_entry_price AND has_exit_price
                AND entry_price_0935 >= {config.MIN_ENTRY_PRICE}
            ) AS included_in_daily_study
        FROM entry_exit_prices
        """
    )
    row = con.execute(
        "SELECT COUNT(*), SUM(included_in_daily_study::INT) FROM eligibility"
    ).fetchone()
    _log.info(f"eligibility: {row[0]:,} rows | included_in_daily_study={row[1]:,}")


def finalize_halal_daily_candidates(con: duckdb.DuckDBPyConnection, force: bool = False) -> None:
    """Stage 8 (finalized): join eligibility + entry/exit + pre-entry price
    features + pre-entry news features + Stage-9 ranking labels + Stage-11
    trap/outcome fields into the single wide halal_daily_candidates table.

    Requires pre_entry_price_features, pre_entry_news_features,
    ranking_labels, post_entry_outcome_features and trap_labels to already
    exist (built by features_price.py, features_news.py, ranking_labels.py
    and trap_analysis.py respectively).
    """
    if skip_if_exists(con, "halal_daily_candidates", force, "Stage 8 (final)"):
        return

    _log.info("Assembling final halal_daily_candidates table")
    con.execute(
        """
        CREATE OR REPLACE TABLE halal_daily_candidates AS
        SELECT
            elig.trade_date,
            elig.ticker,
            eep.entry_price_0935,
            eep.exit_price_1555,
            rl.return_0935_to_1555,
            rl.daily_rank_by_return,
            rl.daily_percentile_by_return,
            rl.is_top_5_percent,
            rl.is_top_10_percent,
            rl.is_winner,
            rl.is_loser,

            elig.is_halal,
            elig.has_entry_price,
            elig.has_exit_price,
            elig.price_above_5_at_entry,
            elig.included_in_daily_study,

            pf.open_0930,
            pf.high_0930_to_before_entry,
            pf.low_0930_to_before_entry,
            pf.return_0930_to_entry,
            pf.volume_0930_to_entry,
            pf.transactions_0930_to_entry,
            pf.dollar_volume_0930_to_entry,
            pf.average_volume_per_minute_0930_to_entry,
            pf.average_transactions_per_minute_0930_to_entry,
            pf.close_position_inside_pre_entry_range,
            pf.premarket_open,
            pf.premarket_last_price,
            pf.premarket_high,
            pf.premarket_low,
            pf.premarket_volume,
            pf.premarket_transactions,
            pf.premarket_dollar_volume,
            pf.premarket_bar_count,
            pf.has_premarket_data,
            pf.premarket_range_position,
            pf.gap_pct_premarket_vs_prior_close,
            pf.gap_pct_open_vs_prior_close,
            pf.pre_entry_momentum_percentile,
            pf.pre_entry_volume_percentile,
            pf.pre_entry_dollar_volume_percentile,
            pf.pre_entry_transactions_percentile,
            pf.premarket_volume_percentile,
            pf.premarket_dollar_volume_percentile,
            pf.premarket_gap_percentile,

            nf.news_count_before_entry,
            nf.positive_news_count_before_entry,
            nf.negative_news_count_before_entry,
            nf.neutral_news_count_before_entry,
            nf.latest_news_time_before_entry,
            nf.latest_news_title_before_entry,
            nf.latest_news_source_before_entry,
            nf.latest_news_sentiment_before_entry,
            nf.minutes_between_latest_news_and_entry,
            nf.has_news_before_entry,
            nf.has_positive_news_before_entry,
            nf.has_negative_news_before_entry,
            nf.duplicate_news_id_count_across_tickers,
            nf.is_broad_news,
            nf.is_direct_news,

            po.max_favorable_move_after_entry,
            po.max_adverse_move_after_entry,
            po.first_5min_return_after_entry,

            tl.looked_strong_before_entry_but_lost,
            tl.high_pre_entry_momentum_then_negative_return,
            tl.positive_news_but_negative_return,
            tl.high_volume_before_entry_but_failed,
            tl.early_spike_then_fade,
            tl.large_gain_given_back_after_entry,
            tl.green_at_entry_red_by_exit,
            tl.strong_first_5min_but_closed_weak,
            tl.low_liquidity_false_move,
            tl.repeated_news_but_no_follow_through,
            tl.broad_news_misleading_ticker_reaction,
            tl.possible_pump_and_fade_pattern,
            tl.possible_buyer_trap_pattern
        FROM eligibility elig
        JOIN entry_exit_prices eep USING (ticker, trade_date)
        LEFT JOIN pre_entry_price_features pf USING (ticker, trade_date)
        LEFT JOIN pre_entry_news_features nf USING (ticker, trade_date)
        LEFT JOIN ranking_labels rl USING (ticker, trade_date)
        LEFT JOIN post_entry_outcome_features po USING (ticker, trade_date)
        LEFT JOIN trap_labels tl USING (ticker, trade_date)
        """
    )
    row = con.execute(
        "SELECT COUNT(*), SUM(included_in_daily_study::INT) FROM halal_daily_candidates"
    ).fetchone()
    _log.info(f"halal_daily_candidates: {row[0]:,} rows | included_in_daily_study={row[1]:,}")
