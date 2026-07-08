"""Standalone leakage / correctness validation suite.

Every check returns (passed: bool, detail: str). run_all() executes them,
logs each result, writes them to `leakage_validation_report`, and returns
the pandas DataFrame. Intended to be run after Stage 8 (candidates
assembled) and again after Stage 12/13 (models trained) - see main.py.
"""
from __future__ import annotations

import random

import duckdb
import pandas as pd

import config
from db import table_exists

_log = config.setup_logging(__name__)


def check_forbidden_columns_not_in_features(con: duckdb.DuckDBPyConnection) -> tuple[bool, str]:
    overlap = set(config.PRE_ENTRY_FEATURE_COLUMNS) & config.FORBIDDEN_COLUMNS
    if overlap:
        return False, f"FORBIDDEN columns leaked into PRE_ENTRY_FEATURE_COLUMNS: {sorted(overlap)}"
    return True, f"{len(config.PRE_ENTRY_FEATURE_COLUMNS)} pre-entry feature columns, 0 overlap with {len(config.FORBIDDEN_COLUMNS)} forbidden columns"


def check_model_feature_columns(con: duckdb.DuckDBPyConnection) -> tuple[bool, str]:
    """If ranking_model.py / risk_model.py have run, verify the feature
    columns they actually used are a subset of the pre-entry allowlist and
    disjoint from the forbidden set."""
    problems = []
    checked = []
    for meta_table in ("ranking_model_metadata", "risk_model_metadata"):
        if not table_exists(con, meta_table):
            continue
        used = {r[0] for r in con.execute(f"SELECT feature_name FROM {meta_table}").fetchall()}
        checked.append(meta_table)
        not_allowed = used - set(config.PRE_ENTRY_FEATURE_COLUMNS)
        forbidden_used = used & config.FORBIDDEN_COLUMNS
        if forbidden_used:
            problems.append(f"{meta_table} used FORBIDDEN columns: {sorted(forbidden_used)}")
        if not_allowed:
            problems.append(f"{meta_table} used columns outside PRE_ENTRY_FEATURE_COLUMNS: {sorted(not_allowed)}")
    if not checked:
        return True, "no model metadata tables found yet (models not trained) - skipped"
    if problems:
        return False, "; ".join(problems)
    return True, f"checked {checked}: all features are pre-entry-safe"


def check_news_no_future(con: duckdb.DuckDBPyConnection) -> tuple[bool, str]:
    if not table_exists(con, "pre_entry_news_features"):
        return True, "pre_entry_news_features not built yet - skipped"
    row = con.execute(
        """
        SELECT COUNT(*) FROM pre_entry_news_features nf
        JOIN entry_exit_prices e ON e.ticker = nf.ticker AND e.trade_date = nf.trade_date
        WHERE nf.latest_news_time_before_entry IS NOT NULL
          AND nf.latest_news_time_before_entry > e.entry_ts_utc
        """
    ).fetchone()
    violations = row[0]
    if violations:
        return False, f"{violations} rows have news published AFTER entry_ts_utc"
    return True, "no pre_entry_news_features row references news after entry_ts_utc"


def check_price_features_no_future(con: duckdb.DuckDBPyConnection, n_samples: int = 25) -> tuple[bool, str]:
    """Recompute open_0930 / high / low directly from halal_minute_bars for a
    random sample and diff against pre_entry_price_features."""
    if not table_exists(con, "pre_entry_price_features"):
        return True, "pre_entry_price_features not built yet - skipped"

    sample = con.execute(
        f"""
        SELECT ticker, trade_date FROM pre_entry_price_features
        WHERE open_0930 IS NOT NULL
        USING SAMPLE {n_samples} ROWS
        """
    ).fetchdf()
    if sample.empty:
        return True, "no rows with open_0930 to sample"

    mismatches = []
    for _, r in sample.iterrows():
        recomputed = con.execute(
            f"""
            SELECT arg_min(open, ts_ny_time), MAX(high), MIN(low)
            FROM halal_minute_bars
            WHERE ticker = ? AND trade_date = ?
              AND ts_ny_time BETWEEN TIME '{config.PRE_ENTRY_START}' AND TIME '{config.PRE_ENTRY_END}'
            """,
            [r["ticker"], r["trade_date"]],
        ).fetchone()
        stored = con.execute(
            """
            SELECT open_0930, high_0930_to_before_entry, low_0930_to_before_entry
            FROM pre_entry_price_features WHERE ticker = ? AND trade_date = ?
            """,
            [r["ticker"], r["trade_date"]],
        ).fetchone()
        if recomputed != stored:
            mismatches.append((r["ticker"], str(r["trade_date"]), recomputed, stored))

    if mismatches:
        return False, f"{len(mismatches)}/{len(sample)} sampled rows mismatch: {mismatches[:3]}"
    return True, f"{len(sample)} sampled rows: open_0930/high/low recomputed from halal_minute_bars match exactly"


def check_entry_exit_prices_against_source(con: duckdb.DuckDBPyConnection, n_samples: int = 25) -> tuple[bool, str]:
    """Spot-check entry/exit prices directly against src.minute_bars
    (bypassing halal_minute_bars entirely) for independent verification."""
    if not table_exists(con, "entry_exit_prices"):
        return True, "entry_exit_prices not built yet - skipped"

    sample = con.execute(
        f"""
        SELECT ticker, trade_date, entry_price_0935, exit_price_1555
        FROM entry_exit_prices
        WHERE has_entry_price AND has_exit_price
        USING SAMPLE {n_samples} ROWS
        """
    ).fetchdf()
    if sample.empty:
        return True, "no fully-priced rows to sample"

    mismatches = []
    for _, r in sample.iterrows():
        entry = con.execute(
            f"""
            SELECT open FROM src.minute_bars
            WHERE ticker = ? AND CAST(ts_ny AS DATE) = ? AND CAST(ts_ny AS TIME) = TIME '{config.ENTRY_TIME}'
            """,
            [r["ticker"], r["trade_date"]],
        ).fetchone()
        exit_ = con.execute(
            f"""
            SELECT open FROM src.minute_bars
            WHERE ticker = ? AND CAST(ts_ny AS DATE) = ? AND CAST(ts_ny AS TIME) = TIME '{config.EXIT_TIME}'
            """,
            [r["ticker"], r["trade_date"]],
        ).fetchone()
        if entry is None or exit_ is None or abs(entry[0] - r["entry_price_0935"]) > 1e-9 or abs(exit_[0] - r["exit_price_1555"]) > 1e-9:
            mismatches.append((r["ticker"], str(r["trade_date"]), entry, exit_, r["entry_price_0935"], r["exit_price_1555"]))

    if mismatches:
        return False, f"{len(mismatches)}/{len(sample)} sampled rows mismatch source: {mismatches[:3]}"
    return True, f"{len(sample)} sampled rows: entry/exit prices match src.minute_bars exactly"


def check_eligibility_correctness(con: duckdb.DuckDBPyConnection) -> tuple[bool, str]:
    if not table_exists(con, "eligibility"):
        return True, "eligibility not built yet - skipped"
    row = con.execute(
        f"""
        SELECT COUNT(*) FROM eligibility
        WHERE NOT is_halal
           OR (included_in_daily_study AND NOT (has_entry_price AND has_exit_price AND price_above_5_at_entry))
           OR (price_above_5_at_entry AND NOT has_entry_price)
        """
    ).fetchone()
    if row[0]:
        return False, f"{row[0]} eligibility rows are internally inconsistent"
    return True, "all eligibility rows are_halal=true and included_in_daily_study implies has_entry+has_exit+price>=5"


def check_no_fixed_liquidity_filter(con: duckdb.DuckDBPyConnection) -> tuple[bool, str]:
    """included_in_daily_study must depend only on halal+entry+exit+price>=5,
    never on volume/transactions/dollar-volume."""
    if not table_exists(con, "eligibility") or not table_exists(con, "entry_exit_prices"):
        return True, "eligibility/entry_exit_prices not built yet - skipped"
    row = con.execute(
        f"""
        SELECT COUNT(*) FROM eligibility e
        JOIN entry_exit_prices x ON x.ticker = e.ticker AND x.trade_date = e.trade_date
        WHERE e.included_in_daily_study !=
              (x.has_entry_price AND x.has_exit_price AND x.entry_price_0935 >= {config.MIN_ENTRY_PRICE})
        """
    ).fetchone()
    if row[0]:
        return False, f"{row[0]} rows where included_in_daily_study does not match the pure halal+price>=5+entry+exit rule (a liquidity filter may have leaked in)"
    return True, "included_in_daily_study exactly matches halal+has_entry+has_exit+price>=5, no liquidity filter applied"


def check_ranking_partitioned_by_day(con: duckdb.DuckDBPyConnection) -> tuple[bool, str]:
    if not table_exists(con, "ranking_labels"):
        return True, "ranking_labels not built yet - skipped"
    row = con.execute(
        """
        SELECT
            SUM((daily_percentile_by_return < 0 OR daily_percentile_by_return > 1)::INT) AS out_of_range,
            SUM((daily_rank_by_return < 1)::INT) AS bad_rank
        FROM ranking_labels
        WHERE daily_percentile_by_return IS NOT NULL
        """
    ).fetchone()
    if row[0] or row[1]:
        return False, f"out_of_range_percentile={row[0]}, bad_rank={row[1]}"
                                                                                             
    bad_days = con.execute(
        """
        SELECT COUNT(*) FROM (
            SELECT trade_date, MIN(daily_rank_by_return) AS min_rank
            FROM ranking_labels WHERE daily_rank_by_return IS NOT NULL
            GROUP BY trade_date
        ) t WHERE min_rank != 1
        """
    ).fetchone()[0]
    if bad_days:
        return False, f"{bad_days} trade_dates where rank #1 is missing (ranking may not be partitioned per day)"
    return True, "daily_rank_by_return/daily_percentile_by_return are correctly partitioned per trade_date"


def check_source_db_untouched(con: duckdb.DuckDBPyConnection) -> tuple[bool, str]:
    """The source DB must never be written to - confirm it is attached read-only."""
    row = con.execute("SELECT database_name, readonly FROM duckdb_databases() WHERE database_name = 'src'").fetchone()
    if row is None:
        return False, "src database is not attached"
    if not row[1]:
        return False, "src database is NOT attached read-only"
    return True, "src (market_data.duckdb) is attached READ_ONLY"


CHECKS = [
    ("forbidden_columns_not_in_features", check_forbidden_columns_not_in_features),
    ("model_feature_columns_are_pre_entry_safe", check_model_feature_columns),
    ("news_no_future_leakage", check_news_no_future),
    ("price_features_no_future_leakage", check_price_features_no_future),
    ("entry_exit_prices_match_source", check_entry_exit_prices_against_source),
    ("eligibility_internally_consistent", check_eligibility_correctness),
    ("no_fixed_liquidity_filter_pre_analysis", check_no_fixed_liquidity_filter),
    ("ranking_partitioned_by_trading_day", check_ranking_partitioned_by_day),
    ("source_db_readonly", check_source_db_untouched),
]


def run_all(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    _log.info("Running leakage/correctness validation suite")
    results = []
    for name, fn in CHECKS:
        try:
            passed, detail = fn(con)
        except Exception as exc:                                                                       
            passed, detail = False, f"check raised an exception: {exc!r}"
        level = _log.info if passed else _log.error
        level(f"[{'PASS' if passed else 'FAIL'}] {name}: {detail}")
        results.append({"check_name": name, "passed": passed, "detail": detail})

    df = pd.DataFrame(results)
    con.execute("CREATE OR REPLACE TABLE leakage_validation_report AS SELECT * FROM df")
    n_failed = (~df["passed"]).sum()
    if n_failed:
        _log.error(f"{n_failed}/{len(df)} leakage checks FAILED - see leakage_validation_report")
    else:
        _log.info(f"All {len(df)} leakage checks passed")
    return df
