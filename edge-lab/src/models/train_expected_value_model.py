"""Expected-value model: regresses NET return (after modeled slippage and
fees, under the reference exit) on decision-time features. Predictions are
clipped to a sane band so one crazy extrapolation cannot dominate ranking."""
from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor

from src.data.feature_builder import FEATURE_COLUMNS


class ExpectedValueModel:
    def __init__(self, clip: float = 0.06):
        self.model = None
        self.clip = clip
        self.feature_cols = list(FEATURE_COLUMNS)

    def _X(self, df: pd.DataFrame) -> np.ndarray:
        return df.reindex(columns=self.feature_cols).astype(float).to_numpy()

    def fit(self, train: pd.DataFrame) -> "ExpectedValueModel":
        df = train.dropna(subset=["net_ret_ref"]).sort_values("date")
        if len(df) < 300:
            self.model = None
            return self
        y = df["net_ret_ref"].clip(-self.clip, self.clip).to_numpy()
        self.model = HistGradientBoostingRegressor(max_depth=3, max_iter=250,
                                                   learning_rate=0.05, random_state=0)
        X = self._X(df)
        # sklearn >= 1.9 crashes on entirely-NaN training columns; a constant
        # column carries no signal and produces no splits, so fill is safe.
        all_nan = np.isnan(X).all(axis=0)
        X[:, all_nan] = 0.0
        self.model.fit(X, y)
        return self

    def predict(self, feats: pd.DataFrame) -> np.ndarray:
        if self.model is None:
            return np.full(len(feats), np.nan)
        return np.clip(self.model.predict(self._X(feats)), -self.clip, self.clip)

    def feature_importance(self, val: pd.DataFrame) -> pd.Series:
        """Permutation importance on validation data (stability audit)."""
        from sklearn.inspection import permutation_importance
        df = val.dropna(subset=["net_ret_ref"])
        if self.model is None or len(df) < 100:
            return pd.Series(dtype=float)
        r = permutation_importance(self.model, self._X(df),
                                   df["net_ret_ref"].to_numpy(),
                                   n_repeats=5, random_state=0)
        return pd.Series(r.importances_mean, index=self.feature_cols).sort_values(ascending=False)
