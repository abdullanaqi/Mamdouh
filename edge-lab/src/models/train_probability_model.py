"""Calibrated P(win) classifier: did price hit TP before SL (under the
reference exit) after entry? Trained with time-series CV; probabilities
are isotonic-calibrated because raw GBM scores are not probabilities."""
from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.calibration import CalibratedClassifierCV
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.model_selection import TimeSeriesSplit

from src.data.feature_builder import FEATURE_COLUMNS


class WinProbabilityModel:
    def __init__(self):
        self.model = None
        self.feature_cols = list(FEATURE_COLUMNS)

    def _X(self, df: pd.DataFrame) -> np.ndarray:
        return df.reindex(columns=self.feature_cols).astype(float).to_numpy()

    def fit(self, train: pd.DataFrame) -> "WinProbabilityModel":
        df = train.dropna(subset=["hit_tp_first"]).sort_values("date")
        if len(df) < 300 or df["hit_tp_first"].nunique() < 2:
            self.model = None  # not enough signal; caller must handle
            return self
        base = HistGradientBoostingClassifier(max_depth=3, max_iter=200,
                                              learning_rate=0.06, random_state=0)
        self.model = CalibratedClassifierCV(base, method="isotonic",
                                            cv=TimeSeriesSplit(n_splits=3))
        X = self._X(df)
        # sklearn >= 1.9 crashes on entirely-NaN training columns; a constant
        # column carries no signal and produces no splits, so fill is safe.
        all_nan = np.isnan(X).all(axis=0)
        X[:, all_nan] = 0.0
        self.model.fit(X, df["hit_tp_first"].astype(int).to_numpy())
        return self

    def predict_proba(self, feats: pd.DataFrame) -> np.ndarray:
        if self.model is None:
            return np.full(len(feats), np.nan)
        return self.model.predict_proba(self._X(feats))[:, 1]
