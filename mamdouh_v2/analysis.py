"""Stage 10: winners-vs-rest analysis across the pre-entry feature set.

All tables here read from the finalized halal_daily_candidates and are
restricted to included_in_daily_study rows (the actual basic-eligible
universe, no liquidity filter applied). These are analysis/reporting
tables only - report.py turns them into the prose findings.
"""
from __future__ import annotations

import duckdb

import config
from db import skip_if_exists

_log = config.setup_logging(__name__)

_PRICE_BUCKET_SQL = """
    CASE
        WHEN entry_price_0935 < 10 THEN '05-10'
        WHEN entry_price_0935 < 20 THEN '10-20'
        WHEN entry_price_0935 < 50 THEN '20-50'
        WHEN entry_price_0935 < 100 THEN '50-100'
        ELSE '100+'
    END
"""

_NEWS_COUNT_BUCKET_SQL = """
    CASE
        WHEN news_count_before_entry = 0 THEN '0'
        WHEN news_count_before_entry = 1 THEN '1'
        WHEN news_count_before_entry <= 3 THEN '2-3'
        ELSE '4+'
    END
"""


def build_group_comparison(con: duckdb.DuckDBPyConnection, force: bool = False) -> None:
    """Compare top-5%, top-10% (excluding top-5%), and the rest across every
    pre-entry signal - the core 'what did winners have in common' table."""
    if skip_if_exists(con, "analysis_group_comparison", force, "Stage 10a"):
        return

    con.execute(
        """
        CREATE OR REPLACE TABLE analysis_group_comparison AS
        SELECT
            CASE
                WHEN is_top_5_percent THEN 'top_5_percent'
                WHEN is_top_10_percent THEN 'top_10_percent_excl_top_5'
                ELSE 'rest'
            END AS group_name,
            COUNT(*) AS n,
            AVG(return_0935_to_1555) AS avg_return,
            MEDIAN(return_0935_to_1555) AS median_return,
            AVG(entry_price_0935) AS avg_entry_price,
            AVG(return_0930_to_entry) AS avg_pre_entry_momentum,
            AVG(pre_entry_momentum_percentile) AS avg_momentum_percentile,
            AVG(volume_0930_to_entry) AS avg_pre_entry_volume,
            AVG(pre_entry_volume_percentile) AS avg_volume_percentile,
            AVG(dollar_volume_0930_to_entry) AS avg_dollar_volume,
            AVG(pre_entry_dollar_volume_percentile) AS avg_dollar_volume_percentile,
            AVG(transactions_0930_to_entry) AS avg_transactions,
            AVG(pre_entry_transactions_percentile) AS avg_transactions_percentile,
            AVG(gap_pct_premarket_vs_prior_close) AS avg_premarket_gap,
            AVG(premarket_gap_percentile) AS avg_premarket_gap_percentile,
            AVG(news_count_before_entry) AS avg_news_count,
            AVG(has_news_before_entry::INT) AS pct_with_news,
            AVG(has_positive_news_before_entry::INT) AS pct_with_positive_news,
            AVG(has_negative_news_before_entry::INT) AS pct_with_negative_news,
            AVG(is_broad_news::INT) AS pct_broad_news
        FROM halal_daily_candidates
        WHERE included_in_daily_study
        GROUP BY 1
        ORDER BY 1
        """
    )
    _log.info("Built analysis_group_comparison (top5% / top10% / rest)")


def build_decile_and_bucket_tables(con: duckdb.DuckDBPyConnection, force: bool = False) -> None:
    """Performance (not trap-rate) lens on liquidity/momentum deciles and
    price buckets - decile/percentile based, never a fixed threshold."""
    if skip_if_exists(con, "analysis_by_liquidity_decile", force, "Stage 10b"):
        return

    con.execute(
        """
        CREATE OR REPLACE TABLE analysis_by_liquidity_decile AS
        SELECT
            CAST(FLOOR(pre_entry_dollar_volume_percentile * 10) AS INT) AS liquidity_decile,
            COUNT(*) AS n,
            AVG(return_0935_to_1555) AS avg_return,
            MEDIAN(return_0935_to_1555) AS median_return,
            STDDEV(return_0935_to_1555) AS stddev_return,
            AVG(is_winner::INT) AS win_rate,
            AVG(is_top_10_percent::INT) AS top_10_pct_rate
        FROM halal_daily_candidates
        WHERE included_in_daily_study AND pre_entry_dollar_volume_percentile IS NOT NULL
        GROUP BY 1 ORDER BY 1
        """
    )

    con.execute(
        """
        CREATE OR REPLACE TABLE analysis_by_momentum_decile AS
        SELECT
            CAST(FLOOR(pre_entry_momentum_percentile * 10) AS INT) AS momentum_decile,
            COUNT(*) AS n,
            AVG(return_0935_to_1555) AS avg_return,
            MEDIAN(return_0935_to_1555) AS median_return,
            STDDEV(return_0935_to_1555) AS stddev_return,
            AVG(is_winner::INT) AS win_rate,
            AVG(is_top_10_percent::INT) AS top_10_pct_rate
        FROM halal_daily_candidates
        WHERE included_in_daily_study AND pre_entry_momentum_percentile IS NOT NULL
        GROUP BY 1 ORDER BY 1
        """
    )

    con.execute(
        f"""
        CREATE OR REPLACE TABLE analysis_by_price_bucket AS
        SELECT
            {_PRICE_BUCKET_SQL} AS price_bucket,
            COUNT(*) AS n,
            AVG(return_0935_to_1555) AS avg_return,
            MEDIAN(return_0935_to_1555) AS median_return,
            STDDEV(return_0935_to_1555) AS stddev_return,
            AVG(is_winner::INT) AS win_rate,
            AVG(is_top_10_percent::INT) AS top_10_pct_rate
        FROM halal_daily_candidates
        WHERE included_in_daily_study
        GROUP BY 1 ORDER BY 1
        """
    )
    _log.info("Built analysis_by_liquidity_decile / _momentum_decile / _price_bucket")


def build_news_tables(con: duckdb.DuckDBPyConnection, force: bool = False) -> None:
    if skip_if_exists(con, "analysis_by_news_count", force, "Stage 10c"):
        return

    con.execute(
        f"""
        CREATE OR REPLACE TABLE analysis_by_news_count AS
        SELECT
            {_NEWS_COUNT_BUCKET_SQL} AS news_count_bucket,
            COUNT(*) AS n,
            AVG(return_0935_to_1555) AS avg_return,
            AVG(is_winner::INT) AS win_rate,
            AVG(is_top_10_percent::INT) AS top_10_pct_rate
        FROM halal_daily_candidates
        WHERE included_in_daily_study
        GROUP BY 1 ORDER BY 1
        """
    )

    con.execute(
        """
        CREATE OR REPLACE TABLE analysis_by_sentiment AS
        SELECT
            COALESCE(latest_news_sentiment_before_entry, 'no_news') AS sentiment,
            COUNT(*) AS n,
            AVG(return_0935_to_1555) AS avg_return,
            AVG(is_winner::INT) AS win_rate,
            AVG(is_top_10_percent::INT) AS top_10_pct_rate,
            AVG((return_0935_to_1555 < 0)::INT) AS negative_return_rate
        FROM halal_daily_candidates
        WHERE included_in_daily_study
        GROUP BY 1 ORDER BY 1
        """
    )

    con.execute(
        """
        CREATE OR REPLACE TABLE analysis_by_source AS
        SELECT
            latest_news_source_before_entry AS source,
            COUNT(*) AS n,
            AVG(return_0935_to_1555) AS avg_return,
            AVG(is_winner::INT) AS win_rate,
            AVG(is_top_10_percent::INT) AS top_10_pct_rate,
            AVG((return_0935_to_1555 < 0)::INT) AS negative_return_rate,
            AVG(is_broad_news::INT) AS pct_broad_news
        FROM halal_daily_candidates
        WHERE included_in_daily_study AND has_news_before_entry
        GROUP BY 1
        HAVING COUNT(*) >= 5
        ORDER BY avg_return DESC
        """
    )
    _log.info("Built analysis_by_news_count / _sentiment / _source")


def run(con: duckdb.DuckDBPyConnection, force: bool = False) -> None:
    build_group_comparison(con, force)
    build_decile_and_bucket_tables(con, force)
    build_news_tables(con, force)
