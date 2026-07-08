"""Stage 7: pre-9:35 news features.

Leakage rule: only news with published_utc <= entry_ts_utc (the exact UTC
instant of that ticker/day's 09:35 bar) is ever used.

Lower bound convention: news is windowed to "since this ticker's previous
trading day's 09:35 entry" rather than all of history. Both news_date and
published_utc in market_news are the same UTC calendar date (verified: no
day is ever rolled forward for after-hours news), so news_date cannot be
used as a NY-trading-day boundary. Bounding the lookback to the previous
entry timestamp instead gives a stable "since we last opened this name"
window that correctly spans weekends/holidays and keeps news_count_before_entry
comparable across tickers and days (unbounded lifetime counts would just
measure how long a ticker has existed in the dataset). This is a disclosed
design choice, not a spec-literal requirement - see report caveats.

Sentiment normalization: market_news.sentiment is free text with 11 distinct
values (positive, neutral, negative, mixed, bullish, cautiously positive,
neutral/positive, neutral/negative, neutral/slightly positive, cautious,
volatile). Mapped via CASE: contains 'negative' (and not 'positive') ->
negative; contains 'positive' or equals 'bullish' -> positive; else neutral.

Direct vs broad: duplicate_news_id_count_across_tickers is the number of
distinct tickers (market-wide, halal + non-halal) that share the same
news_id as the latest pre-entry article. >1 implies a syndicated/broad
story (e.g. sector- or market-wide wire content) rather than a ticker-
specific one. This is a heuristic estimate, not a guaranteed classification.
"""
from __future__ import annotations

import duckdb

import config
from db import skip_if_exists

_log = config.setup_logging(__name__)

_SENTIMENT_CASE = """
    CASE
        WHEN sentiment ILIKE '%negative%' THEN 'negative'
        WHEN sentiment ILIKE '%positive%' OR sentiment = 'bullish' THEN 'positive'
        ELSE 'neutral'
    END
"""


def build(con: duckdb.DuckDBPyConnection, force: bool = False) -> None:
    if skip_if_exists(con, "pre_entry_news_features", force, "Stage 7"):
        return

    _log.info("Building pre-entry news features (published_utc <= entry_ts_utc)")
    con.execute(
        f"""
        CREATE OR REPLACE TABLE pre_entry_news_features AS
        WITH news_breadth AS (
            SELECT news_id, COUNT(DISTINCT ticker) AS ticker_breadth
            FROM src.market_news
            GROUP BY news_id
        ),
        news_norm AS (
            SELECT
                n.news_id, n.ticker, n.published_utc, n.title, n.source,
                {_SENTIMENT_CASE} AS sentiment_norm,
                b.ticker_breadth
            FROM src.market_news n
            JOIN news_breadth b ON b.news_id = n.news_id
        ),
        entry_windows AS (
            SELECT
                ticker, trade_date, entry_ts_utc,
                LAG(entry_ts_utc) OVER (PARTITION BY ticker ORDER BY trade_date) AS window_lower_bound_utc
            FROM entry_exit_prices
            WHERE has_entry_price
        ),
        news_in_window AS (
            SELECT
                w.ticker, w.trade_date, w.entry_ts_utc,
                nn.news_id, nn.published_utc, nn.title, nn.source, nn.sentiment_norm, nn.ticker_breadth,
                ROW_NUMBER() OVER (PARTITION BY w.ticker, w.trade_date ORDER BY nn.published_utc DESC) AS rn
            FROM entry_windows w
            JOIN news_norm nn
                ON nn.ticker = w.ticker
               AND nn.published_utc <= w.entry_ts_utc
               AND (w.window_lower_bound_utc IS NULL OR nn.published_utc > w.window_lower_bound_utc)
        ),
        agg AS (
            SELECT
                ticker, trade_date,
                COUNT(*) AS news_count_before_entry,
                SUM((sentiment_norm = 'positive')::INT) AS positive_news_count_before_entry,
                SUM((sentiment_norm = 'negative')::INT) AS negative_news_count_before_entry,
                SUM((sentiment_norm = 'neutral')::INT)  AS neutral_news_count_before_entry
            FROM news_in_window
            GROUP BY ticker, trade_date
        ),
        latest AS (
            SELECT
                ticker, trade_date, entry_ts_utc,
                published_utc AS latest_news_time_before_entry,
                title   AS latest_news_title_before_entry,
                source  AS latest_news_source_before_entry,
                sentiment_norm AS latest_news_sentiment_before_entry,
                ticker_breadth AS duplicate_news_id_count_across_tickers,
                date_diff('minute', published_utc, entry_ts_utc) AS minutes_between_latest_news_and_entry
            FROM news_in_window
            WHERE rn = 1
        )
        SELECT
            u.ticker,
            u.trade_date,
            COALESCE(a.news_count_before_entry, 0) AS news_count_before_entry,
            COALESCE(a.positive_news_count_before_entry, 0) AS positive_news_count_before_entry,
            COALESCE(a.negative_news_count_before_entry, 0) AS negative_news_count_before_entry,
            COALESCE(a.neutral_news_count_before_entry, 0) AS neutral_news_count_before_entry,
            l.latest_news_time_before_entry,
            l.latest_news_title_before_entry,
            l.latest_news_source_before_entry,
            l.latest_news_sentiment_before_entry,
            l.minutes_between_latest_news_and_entry,
            (COALESCE(a.news_count_before_entry, 0) > 0) AS has_news_before_entry,
            (COALESCE(a.positive_news_count_before_entry, 0) > 0) AS has_positive_news_before_entry,
            (COALESCE(a.negative_news_count_before_entry, 0) > 0) AS has_negative_news_before_entry,
            l.duplicate_news_id_count_across_tickers,
            CASE WHEN l.duplicate_news_id_count_across_tickers IS NULL THEN NULL
                 ELSE l.duplicate_news_id_count_across_tickers > 1 END AS is_broad_news,
            CASE WHEN l.duplicate_news_id_count_across_tickers IS NULL THEN NULL
                 ELSE l.duplicate_news_id_count_across_tickers = 1 END AS is_direct_news
        FROM trading_days_universe u
        LEFT JOIN agg a ON a.ticker = u.ticker AND a.trade_date = u.trade_date
        LEFT JOIN latest l ON l.ticker = u.ticker AND l.trade_date = u.trade_date
        """
    )
    row = con.execute(
        "SELECT COUNT(*), SUM(has_news_before_entry::INT) FROM pre_entry_news_features"
    ).fetchone()
    _log.info(f"pre_entry_news_features: {row[0]:,} rows | has_news_before_entry={row[1]:,}")
