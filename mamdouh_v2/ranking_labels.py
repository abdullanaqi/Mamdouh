"""Stage 9: daily return ranking labels - RESEARCH/ANALYSIS LABELS ONLY.

return_0935_to_1555 and everything derived from it here are outcome data
(only knowable after 15:55). They are used for: (a) evaluating how good the
pre-entry ranking/risk models are, and (b) defining winner/trap analysis
groups. They must NEVER be fed back into ranking_model.py or risk_model.py
as an input feature - see config.FORBIDDEN_COLUMNS and leakage_checks.py.

Ranking/percentile are computed PARTITION BY trade_date, restricted to rows
flagged included_in_daily_study in `eligibility` (the actual basic-eligible
tradeable universe for that day) so the cross-section is fair. is_winner /
is_loser are simple sign-of-return descriptors computed for any row with a
valid return, independent of eligibility, since they're descriptive, not a
ranking.
"""
from __future__ import annotations

import duckdb

import config
from db import skip_if_exists

_log = config.setup_logging(__name__)


def build(con: duckdb.DuckDBPyConnection, force: bool = False) -> None:
    if skip_if_exists(con, "ranking_labels", force, "Stage 9"):
        return

    _log.info("Building daily return ranking labels (research/evaluation only)")
    con.execute(
        f"""
        CREATE OR REPLACE TABLE ranking_labels AS
        WITH base AS (
            SELECT
                ticker, trade_date,
                CASE WHEN has_entry_price AND has_exit_price AND entry_price_0935 != 0
                     THEN (exit_price_1555 - entry_price_0935) / entry_price_0935 END AS return_0935_to_1555
            FROM entry_exit_prices
        ),
        elig_ranked AS (
            SELECT
                b.ticker, b.trade_date,
                RANK() OVER (PARTITION BY b.trade_date ORDER BY b.return_0935_to_1555 DESC) AS daily_rank_by_return,
                PERCENT_RANK() OVER (PARTITION BY b.trade_date ORDER BY b.return_0935_to_1555) AS daily_percentile_by_return
            FROM base b
            JOIN eligibility e ON e.ticker = b.ticker AND e.trade_date = b.trade_date
            WHERE e.included_in_daily_study
        )
        SELECT
            b.ticker,
            b.trade_date,
            b.return_0935_to_1555,
            r.daily_rank_by_return,
            r.daily_percentile_by_return,
            (r.daily_percentile_by_return >= {config.TOP_5_PCT_CUTOFF})  AS is_top_5_percent,
            (r.daily_percentile_by_return >= {config.TOP_10_PCT_CUTOFF}) AS is_top_10_percent,
            (b.return_0935_to_1555 > 0) AS is_winner,
            (b.return_0935_to_1555 < 0) AS is_loser
        FROM base b
        LEFT JOIN elig_ranked r ON r.ticker = b.ticker AND r.trade_date = b.trade_date
        """
    )
    row = con.execute(
        "SELECT COUNT(*), SUM(is_top_5_percent::INT), SUM(is_top_10_percent::INT) FROM ranking_labels"
    ).fetchone()
    _log.info(f"ranking_labels: {row[0]:,} rows | top5%={row[1]:,} | top10%={row[2]:,}")
