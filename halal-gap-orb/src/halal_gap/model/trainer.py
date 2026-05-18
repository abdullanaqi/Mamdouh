"""Walk-forward cross-validation trainer for Stage 3.

Standard time-series CV with strictly non-overlapping train and test windows:
  train: [t - train_months, t)
  test:  [t, t + test_months)
  step:  step_months

For every fold we fit on the train slice and score the test slice; aggregate
metrics + per-fold predictions are returned so the caller can plot
out-of-sample equity curves or threshold sweeps.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterator

import numpy as np
import pandas as pd
from sklearn.metrics import (
    average_precision_score,
    brier_score_loss,
    log_loss,
    roc_auc_score,
)

from halal_gap.features.builder import feature_columns
from halal_gap.model.predictor import ScoringModel, fit_xgb
from halal_gap.utils.config import settings
from halal_gap.utils.logging import log


@dataclass(slots=True, frozen=True)
class FoldSpec:
    """One walk-forward fold's window boundaries (NY-tz timestamps)."""

    train_start: pd.Timestamp
    train_end: pd.Timestamp
    test_start: pd.Timestamp
    test_end: pd.Timestamp


@dataclass(slots=True)
class FoldResult:
    """Output of one fold: trained model, test predictions, and metrics."""

    spec: FoldSpec
    n_train: int
    n_test: int
    auc: float
    pr_auc: float
    logloss: float
    brier: float
    predictions: pd.DataFrame = field(default_factory=pd.DataFrame)


def _months(ts: pd.Timestamp, n: int) -> pd.Timestamp:
    return ts + pd.DateOffset(months=n)


def walk_forward_specs(df: pd.DataFrame) -> list[FoldSpec]:
    """Generate fold specs over the labelled dataset using settings.ml.cv."""
    cfg = settings()["ml"]["cv"]
    train_m = int(cfg["train_months"])
    test_m = int(cfg["test_months"])
    step_m = int(cfg["step_months"])

    if df.empty:
        return []
    start = pd.to_datetime(df["as_of"].min()).normalize()
    end = pd.to_datetime(df["as_of"].max()).normalize()
    specs: list[FoldSpec] = []
    test_start = _months(start, train_m)
    while test_start < end:
        train_start = _months(test_start, -train_m)
        train_end = test_start
        test_end = min(_months(test_start, test_m), end + pd.Timedelta(days=1))
        specs.append(
            FoldSpec(
                train_start=train_start, train_end=train_end,
                test_start=test_start, test_end=test_end,
            )
        )
        test_start = _months(test_start, step_m)
    return specs


def _slice(df: pd.DataFrame, lo: pd.Timestamp, hi: pd.Timestamp) -> pd.DataFrame:
    return df[(df["as_of"] >= lo) & (df["as_of"] < hi)]


def _safe_auc(y: np.ndarray, p: np.ndarray) -> float:
    if len(np.unique(y)) < 2:
        return float("nan")
    return float(roc_auc_score(y, p))


def _safe_pr_auc(y: np.ndarray, p: np.ndarray) -> float:
    if len(np.unique(y)) < 2:
        return float("nan")
    return float(average_precision_score(y, p))


def run_walk_forward(df: pd.DataFrame) -> list[FoldResult]:
    """Train+score every walk-forward fold and return per-fold results.

    Folds with too few training rows (<30) or no class diversity are skipped
    with a warning rather than failing the whole sweep.
    """
    cols = feature_columns()
    results: list[FoldResult] = []
    for spec in walk_forward_specs(df):
        train = _slice(df, spec.train_start, spec.train_end)
        test = _slice(df, spec.test_start, spec.test_end)
        if len(train) < 30 or len(test) == 0:
            log.info(f"skip fold {spec.test_start.date()}..{spec.test_end.date()} (n_train={len(train)})")
            continue
        if train["label"].nunique() < 2:
            log.warning(f"fold {spec.test_start.date()}: train labels homogeneous, skipping")
            continue
        X_train, y_train = train[cols], train["label"].astype(int).values
        X_test, y_test = test[cols], test["label"].astype(int).values
        model = fit_xgb(X_train, y_train, X_val=X_test, y_val=y_test)
        proba = model.predict_proba(X_test.values)[:, 1]
        preds = test[["symbol", "as_of", "label"]].copy()
        preds["proba"] = proba

        results.append(
            FoldResult(
                spec=spec,
                n_train=len(train),
                n_test=len(test),
                auc=_safe_auc(y_test, proba),
                pr_auc=_safe_pr_auc(y_test, proba),
                logloss=float(log_loss(y_test, np.clip(proba, 1e-6, 1 - 1e-6), labels=[0, 1])),
                brier=float(brier_score_loss(y_test, proba)),
                predictions=preds,
            )
        )
        log.info(
            f"fold {spec.test_start.date()}..{spec.test_end.date()} "
            f"n_train={len(train)} n_test={len(test)} "
            f"AUC={results[-1].auc:.3f} logloss={results[-1].logloss:.3f}"
        )
    return results


def aggregate(results: list[FoldResult]) -> dict[str, float]:
    """Concatenate every fold's predictions and compute a single OOS metric set."""
    if not results:
        return {"auc": float("nan"), "pr_auc": float("nan"),
                "logloss": float("nan"), "brier": float("nan"), "n": 0}
    preds = pd.concat([r.predictions for r in results], ignore_index=True)
    y = preds["label"].astype(int).values
    p = preds["proba"].astype(float).values
    return {
        "auc": _safe_auc(y, p),
        "pr_auc": _safe_pr_auc(y, p),
        "logloss": float(log_loss(y, np.clip(p, 1e-6, 1 - 1e-6), labels=[0, 1])),
        "brier": float(brier_score_loss(y, p)),
        "n": int(len(preds)),
    }


def fit_final_model(df: pd.DataFrame) -> ScoringModel:
    """Train the production model on the full dataset (no CV)."""
    cols = feature_columns()
    X, y = df[cols], df["label"].astype(int).values
    if df["label"].nunique() < 2:
        raise ValueError("cannot fit model: labels are homogeneous")
    model = fit_xgb(X, y)
    return ScoringModel(model=model, feature_cols=cols, trained_on=len(df))
