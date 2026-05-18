"""SHAP wrapper around a fitted ScoringModel."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from halal_gap.model.predictor import ScoringModel


@dataclass(slots=True)
class ExplanationBundle:
    """Convenience container for SHAP values + base value + feature ordering."""

    values: np.ndarray         # (n_rows, n_features)
    base_value: float
    feature_names: list[str]
    expected_proba: np.ndarray  # raw model output prob, for reference

    def top_k(self, row_idx: int, k: int = 5) -> list[tuple[str, float]]:
        """Return the top-k highest-|shap| features for one row."""
        v = self.values[row_idx]
        order = np.argsort(-np.abs(v))[:k]
        return [(self.feature_names[i], float(v[i])) for i in order]

    def mean_abs_importance(self) -> pd.Series:
        """Mean |shap| across rows, ranked descending."""
        s = pd.Series(np.abs(self.values).mean(axis=0), index=self.feature_names)
        return s.sort_values(ascending=False)


def explain(model: ScoringModel, X: pd.DataFrame) -> ExplanationBundle:
    """Compute SHAP values for `X` using TreeExplainer.

    SHAP's output for `XGBClassifier` is the raw-margin shap; we keep that
    (interpretation is "log-odds shift" per feature, not probability).
    """
    import shap  # heavy import; lazy

    cols = model.feature_cols
    frame = X.copy()
    for c in cols:
        if c not in frame.columns:
            frame[c] = 0
    frame = frame[cols]

    explainer = shap.TreeExplainer(model.model)
    raw = explainer.shap_values(frame.values)
    base = explainer.expected_value
    if isinstance(raw, list):  # binary classifier sometimes returns [neg, pos]
        raw = raw[1]
        base = base[1] if hasattr(base, "__len__") else base
    return ExplanationBundle(
        values=np.asarray(raw),
        base_value=float(base),
        feature_names=cols,
        expected_proba=model.predict_proba(frame),
    )
