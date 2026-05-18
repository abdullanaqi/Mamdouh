"""Persistable XGBoost scoring model.

Wraps a fitted XGBClassifier with its canonical feature-column list so that
inference at trading time can never get the column order wrong.
"""
from __future__ import annotations

import pickle
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from xgboost import XGBClassifier

from halal_gap.features.builder import feature_columns
from halal_gap.utils.config import settings


@dataclass(slots=True)
class ScoringModel:
    """A fitted XGBoost classifier plus its locked feature schema."""

    model: XGBClassifier
    feature_cols: list[str]
    trained_on: int = 0    # number of rows used to fit
    schema_version: int = 1

    def predict_proba(self, X: pd.DataFrame | dict[str, Any]) -> np.ndarray:
        """Return P(win) for each row of `X`.

        Accepts a single dict (one row) or a DataFrame. Reorders columns to
        match the locked feature schema and fills missing columns with 0.
        """
        if isinstance(X, dict):
            X = pd.DataFrame([X])
        frame = X.copy()
        for c in self.feature_cols:
            if c not in frame.columns:
                frame[c] = 0
        frame = frame[self.feature_cols]
        proba = self.model.predict_proba(frame.values)[:, 1]
        return np.asarray(proba, dtype=float)

    def passes(self, X: pd.DataFrame | dict[str, Any], threshold: float | None = None) -> np.ndarray:
        """Boolean mask: True where P(win) >= threshold.

        Threshold defaults to `ml.win_probability_threshold` from settings.
        """
        thr = threshold if threshold is not None else settings()["ml"]["win_probability_threshold"]
        return self.predict_proba(X) >= thr

    def save(self, path: Path) -> Path:
        """Pickle-serialise the model + schema to `path`."""
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("wb") as f:
            pickle.dump(self, f)
        return path

    @classmethod
    def load(cls, path: Path) -> "ScoringModel":
        with path.open("rb") as f:
            obj = pickle.load(f)
        if not isinstance(obj, cls):
            raise TypeError(f"expected ScoringModel, got {type(obj)}")
        return obj


def default_xgb_params() -> dict[str, Any]:
    """XGBoost hyperparameters from settings.yaml."""
    cfg = settings()["ml"]["xgb"]
    return {
        "max_depth": int(cfg["max_depth"]),
        "learning_rate": float(cfg["learning_rate"]),
        "n_estimators": int(cfg["n_estimators"]),
        "objective": "binary:logistic",
        "eval_metric": "logloss",
        "tree_method": "hist",
        "n_jobs": 1,
        "random_state": 42,
    }


def fit_xgb(
    X_train: pd.DataFrame,
    y_train: np.ndarray,
    X_val: pd.DataFrame | None = None,
    y_val: np.ndarray | None = None,
) -> XGBClassifier:
    """Fit an XGBClassifier with optional early-stopping on a validation fold."""
    params = default_xgb_params()
    early = settings()["ml"]["xgb"].get("early_stopping_rounds")
    if X_val is not None and y_val is not None and len(X_val) > 0 and early:
        model = XGBClassifier(**params, early_stopping_rounds=int(early))
        model.fit(X_train.values, y_train, eval_set=[(X_val.values, y_val)], verbose=False)
    else:
        model = XGBClassifier(**params)
        model.fit(X_train.values, y_train)
    return model


def build_scoring_model(df: pd.DataFrame) -> ScoringModel:
    """Train a final production model on the full labelled dataset."""
    cols = feature_columns()
    X = df[cols]
    y = df["label"].astype(int).values
    model = fit_xgb(X, y)
    return ScoringModel(model=model, feature_cols=cols, trained_on=len(df))
