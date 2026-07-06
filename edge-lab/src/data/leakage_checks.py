"""Leakage detection.

Three independent lines of defense:
1. Structural: PointInTimeView slices strictly before decision_ts and a
   validation assert fires if any exposed row is >= cutoff.
2. Future-invariance test: recompute every feature after PERTURBING all
   post-decision bars. If any feature value changes, that feature leaks.
3. Statistical smell test: any feature whose |corr| with a same-day
   outcome exceeds a threshold is flagged for manual audit (near-perfect
   correlation with the future is how leaks usually announce themselves).
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from src.data.feature_builder import FEATURE_COLUMNS, PointInTimeView, compute_features
from src.utils.logging import get_logger

log = get_logger(__name__)


def _perturb_future(minute_df: pd.DataFrame, decision_ts: pd.Timestamp, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    df = minute_df.copy()
    fut = df["ts"] >= decision_ts
    n = int(fut.sum())
    if n:
        shock = 1.0 + rng.uniform(0.2, 0.8, size=n)
        for c in ("open", "high", "low", "close"):
            df.loc[fut, c] = df.loc[fut, c].to_numpy() * shock
        df.loc[fut, "volume"] = df.loc[fut, "volume"].to_numpy() * 7 + 12345
    return df


def future_invariance_check(minute_df: pd.DataFrame, daily_panel: pd.DataFrame,
                            date, ticker: str, decision_ts: pd.Timestamp) -> list[str]:
    """Returns list of feature names that CHANGED when the future changed
    (i.e., leaks). Empty list == clean."""
    v1 = PointInTimeView.build(date, minute_df, daily_panel)
    v2 = PointInTimeView.build(date, _perturb_future(minute_df, decision_ts), daily_panel)
    f1 = compute_features(v1, ticker, decision_ts)
    f2 = compute_features(v2, ticker, decision_ts)
    if f1 is None or f2 is None:
        return []
    leaks = []
    for k in f1:
        if k in ("ticker", "date", "decision_ts"):
            continue
        a, b = f1[k], f2[k]
        if isinstance(a, float) and isinstance(b, float):
            if (math.isnan(a) and math.isnan(b)):
                continue
            if not math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-12):
                leaks.append(k)
        elif a != b:
            leaks.append(k)
    return leaks


def suspicious_correlation_scan(features: pd.DataFrame, labels: pd.DataFrame,
                                outcome_col: str = "ret_eod", threshold: float = 0.90) -> pd.DataFrame:
    if outcome_col in features.columns:
        df = features
    else:
        df = features.join(labels[[outcome_col]])
    rows = []
    for c in FEATURE_COLUMNS:
        if c not in df.columns:
            continue
        s = pd.to_numeric(df[c], errors="coerce")
        if s.notna().sum() < 30 or s.std() == 0:
            continue
        corr = s.corr(df[outcome_col])
        if corr is not None and abs(corr) >= threshold:
            rows.append({"feature": c, "corr_with_future": float(corr)})
    out = pd.DataFrame(rows)
    if len(out):
        log.warning("SUSPICIOUS features (audit these): %s", out.to_dict("records"))
    return out


def audit_sample(minute_df, daily_panel, date, decision_times, max_tickers: int = 25) -> dict:
    """Runs the future-invariance check over a sample; returns summary."""
    v = PointInTimeView.build(date, minute_df, daily_panel)
    leaks: dict[str, list[str]] = {}
    for t in v.tickers()[:max_tickers]:
        for dts in decision_times:
            bad = future_invariance_check(minute_df, daily_panel, date, t, dts)
            if bad:
                leaks.setdefault(t, []).extend(bad)
    return {"checked_tickers": min(max_tickers, len(v.tickers())),
            "decision_times": [str(x) for x in decision_times],
            "leaks": leaks, "clean": not leaks}
