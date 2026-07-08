"""Stage 12: pre-entry ranking model - predicts P(top 10% by return) using
ONLY config.PRE_ENTRY_FEATURE_COLUMNS.

See model_utils.walk_forward_predict for the validation scheme (expanding
window, month-by-month, never scoring a month with a model trained on that
month or later). Model is HistGradientBoostingClassifier - chosen because
it handles missing values (NaN) natively, which matters since several
pre-entry features (premarket_*, news_*) are legitimately NULL for
tickers/days lacking that data.

Target: is_top_10_percent (a Stage-9 OUTCOME label) is used ONLY as the
training target here - never as a model input feature. ranking_score is
the out-of-fold predicted probability, written to ranking_model_scores and
used downstream by backtest.py for daily selection.
"""
from __future__ import annotations

import duckdb
import pandas as pd

import config
from db import skip_if_exists
from model_utils import summarize_importance, walk_forward_predict

_log = config.setup_logging(__name__)

FEATURE_COLUMNS = config.PRE_ENTRY_FEATURE_COLUMNS


def _load_training_frame(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    cast_cols = ",\n            ".join(f"CAST({c} AS DOUBLE) AS {c}" for c in FEATURE_COLUMNS)
    df = con.execute(
        f"""
        SELECT
            ticker,
            trade_date,
            CAST(is_top_10_percent AS DOUBLE) AS target,
            {cast_cols}
        FROM halal_daily_candidates
        WHERE included_in_daily_study AND is_top_10_percent IS NOT NULL
        """
    ).fetchdf()
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df["year_month"] = df["trade_date"].dt.to_period("M")
    return df


def build(con: duckdb.DuckDBPyConnection, force: bool = False) -> None:
    if skip_if_exists(con, "ranking_model_scores", force, "Stage 12"):
        return

    _log.info("Training walk-forward ranking model (pre-entry features only, target=is_top_10_percent)")
    df = _load_training_frame(con)
    scores, eval_df, importance_by_month = walk_forward_predict(df, FEATURE_COLUMNS, "ranking_score", _log, "ranking_model")
    importance_summary = summarize_importance(importance_by_month)

    con.execute("CREATE OR REPLACE TABLE ranking_model_scores AS SELECT * FROM scores")
    con.execute("CREATE OR REPLACE TABLE ranking_model_eval AS SELECT * FROM eval_df")
    con.execute("CREATE OR REPLACE TABLE ranking_model_feature_importance AS SELECT * FROM importance_summary")
    con.execute("CREATE OR REPLACE TABLE ranking_model_metadata (feature_name VARCHAR)")
    con.executemany("INSERT INTO ranking_model_metadata VALUES (?)", [(c,) for c in FEATURE_COLUMNS])

    _log.info(f"ranking_model_scores: {len(scores):,} out-of-fold predictions across {len(eval_df)} test months")
    if not importance_summary.empty:
        top5 = ", ".join(importance_summary.head(5)["feature"])
        _log.info(f"ranking_model top 5 features by permutation importance: {top5}")
