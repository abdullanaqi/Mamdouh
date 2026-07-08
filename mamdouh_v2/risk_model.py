"""Stage 13: pre-entry trap/risk warning model - predicts P(this setup is a
buyer-trap / pump-and-fade pattern) using ONLY config.PRE_ENTRY_FEATURE_COLUMNS.

Same walk-forward scheme as ranking_model.py (see model_utils.py). Target is
the composite outcome label (possible_buyer_trap_pattern OR
possible_pump_and_fade_pattern) from trap_analysis.py - an OUTCOME label,
used only as the training target, never as an input feature.

risk_score is the out-of-fold predicted trap probability. backtest.py uses
it to build the "ranking score + trap/risk filter" variant.
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
            CAST(possible_buyer_trap_pattern OR possible_pump_and_fade_pattern AS DOUBLE) AS target,
            {cast_cols}
        FROM halal_daily_candidates
        WHERE included_in_daily_study
          AND possible_buyer_trap_pattern IS NOT NULL
          AND possible_pump_and_fade_pattern IS NOT NULL
        """
    ).fetchdf()
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df["year_month"] = df["trade_date"].dt.to_period("M")
    return df


def build(con: duckdb.DuckDBPyConnection, force: bool = False) -> None:
    if skip_if_exists(con, "risk_model_scores", force, "Stage 13"):
        return

    _log.info("Training walk-forward trap/risk model (pre-entry features only, target=possible_buyer_trap_pattern OR possible_pump_and_fade_pattern)")
    df = _load_training_frame(con)
    scores, eval_df, importance_by_month = walk_forward_predict(df, FEATURE_COLUMNS, "risk_score", _log, "risk_model")
    importance_summary = summarize_importance(importance_by_month)

    con.execute("CREATE OR REPLACE TABLE risk_model_scores AS SELECT * FROM scores")
    con.execute("CREATE OR REPLACE TABLE risk_model_eval AS SELECT * FROM eval_df")
    con.execute("CREATE OR REPLACE TABLE risk_model_feature_importance AS SELECT * FROM importance_summary")
    con.execute("CREATE OR REPLACE TABLE risk_model_metadata (feature_name VARCHAR)")
    con.executemany("INSERT INTO risk_model_metadata VALUES (?)", [(c,) for c in FEATURE_COLUMNS])

    _log.info(f"risk_model_scores: {len(scores):,} out-of-fold predictions across {len(eval_df)} test months")
    if not importance_summary.empty:
        top5 = ", ".join(importance_summary.head(5)["feature"])
        _log.info(f"risk_model top 5 features by permutation importance: {top5}")
