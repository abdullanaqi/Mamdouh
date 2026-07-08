"""Shared walk-forward training/scoring used by ranking_model.py and
risk_model.py. Both models must be evaluated identically: expanding-window,
month-by-month, never scoring a month with a model that saw that month or
any later month during training. See ranking_model.py's module docstring
for why this is stricter than k-fold.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.inspection import permutation_importance
from sklearn.metrics import roc_auc_score

import config

                                                                          
                                                                            
                                                                         
                                                                           
                                                              
_PERMUTATION_REPEATS = 5


def walk_forward_predict(
    df: pd.DataFrame,
    feature_columns: list[str],
    score_column_name: str,
    log,
    log_prefix: str,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """df must have columns: ticker, trade_date, year_month, target (0/1 float).
    Returns (scores_df[ticker, trade_date, score_column_name], eval_df,
    importance_df[test_month, feature, importance_mean, importance_std]).
    """
    months = sorted(df["year_month"].unique())
    if len(months) <= config.MIN_TRAIN_MONTHS:
        log.warning(
            f"[{log_prefix}] Only {len(months)} distinct months available; need > "
            f"{config.MIN_TRAIN_MONTHS} for any walk-forward scoring - output will be empty"
        )
        empty_cols = ["ticker", "trade_date", score_column_name]
        return pd.DataFrame(columns=empty_cols), pd.DataFrame(), pd.DataFrame()

    out_frames = []
    eval_rows = []
    importance_frames = []
    for i in range(config.MIN_TRAIN_MONTHS, len(months)):
        test_month = months[i]
        train_months = months[:i]
        train_mask = df["year_month"].isin(train_months)
        test_mask = df["year_month"] == test_month

        X_train = df.loc[train_mask, feature_columns].to_numpy(dtype=float)
        y_train = df.loc[train_mask, "target"].to_numpy(dtype=float)
        X_test = df.loc[test_mask, feature_columns].to_numpy(dtype=float)
        y_test = df.loc[test_mask, "target"].to_numpy(dtype=float)

        if y_train.sum() == 0 or y_train.sum() == len(y_train):
            log.warning(f"[{log_prefix}] Skipping test month {test_month}: training target has no class variation")
            continue

        model = HistGradientBoostingClassifier(max_depth=4, max_iter=150, random_state=42)
        model.fit(X_train, y_train)
        proba = model.predict_proba(X_test)[:, 1]

        out = df.loc[test_mask, ["ticker", "trade_date"]].copy()
        out[score_column_name] = proba
        out_frames.append(out)

        auc = np.nan
        if len(np.unique(y_test)) > 1:
            auc = roc_auc_score(y_test, proba)
            result = permutation_importance(
                model, X_test, y_test, n_repeats=_PERMUTATION_REPEATS,
                random_state=42, scoring="roc_auc", n_jobs=-1,
            )
            importance_frames.append(pd.DataFrame({
                "test_month": str(test_month),
                "feature": feature_columns,
                "importance_mean": result.importances_mean,
                "importance_std": result.importances_std,
            }))
        eval_rows.append({
            "test_month": str(test_month), "n_train": int(len(y_train)), "n_test": int(len(y_test)),
            "train_positive_rate": float(y_train.mean()), "test_positive_rate": float(y_test.mean()),
            "auc": float(auc) if not np.isnan(auc) else None,
        })
        auc_str = f"{auc:.4f}" if not np.isnan(auc) else "undefined (single class in test)"
        log.info(f"[{log_prefix}] trained on {len(train_months)} months ({len(y_train):,} rows), scored {test_month} ({len(y_test):,} rows), AUC={auc_str}")

    scores = pd.concat(out_frames, ignore_index=True) if out_frames else pd.DataFrame(columns=["ticker", "trade_date", score_column_name])
    importance_by_month = pd.concat(importance_frames, ignore_index=True) if importance_frames else pd.DataFrame(columns=["test_month", "feature", "importance_mean", "importance_std"])
    return scores, pd.DataFrame(eval_rows), importance_by_month


def summarize_importance(importance_by_month: pd.DataFrame) -> pd.DataFrame:
    """Average permutation importance across all walk-forward folds, ranked
    descending. A feature with a high average AND a small std is a stable,
    trustworthy driver; a high mean with a huge std is fold-dependent and
    should be treated with more suspicion."""
    if importance_by_month.empty:
        return pd.DataFrame(columns=["feature", "mean_importance", "std_across_folds", "n_folds"])
    summary = importance_by_month.groupby("feature")["importance_mean"].agg(
        mean_importance="mean", std_across_folds="std", n_folds="count"
    ).reset_index()
    return summary.sort_values("mean_importance", ascending=False).reset_index(drop=True)
