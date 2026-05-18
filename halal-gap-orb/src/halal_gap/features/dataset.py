"""Assemble the training dataset for Stage 3 from persisted backtest artifacts.

Inputs:
  - reports/stage1_artifacts.parquet  (one row per executed setup with pnl_r)
  - reports/stage2_catalysts.parquet  (one row per (symbol, as_of); optional)

Output: a DataFrame whose columns are exactly `feature_columns()` plus
`symbol`, `as_of`, and `label` (1 if pnl_r > 0 else 0).
"""
from __future__ import annotations

import math
from pathlib import Path

import pandas as pd

from halal_gap.features.builder import (
    CATALYST_TYPES,
    _one_hot_catalyst_type,
    feature_columns,
)
from halal_gap.utils.logging import log


WIN_LABEL_THRESHOLD_R = 0.0  # any positive R is a "win"


def _safe_log1p(x: float) -> float:
    return math.log1p(max(0.0, float(x)))


def build_dataset(
    trades: pd.DataFrame,
    catalysts: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Project the artifacts dataframes into a model-ready training set.

    `trades` columns required: symbol, as_of, filled, gap_pct, premarket_rvol,
    rvol_5min, atr_value, entry_stop, risk_per_share, pnl_r.

    Rows where filled == False are dropped (no realised outcome to learn from).
    """
    df = trades[trades["filled"]].copy() if "filled" in trades.columns else trades.copy()
    if df.empty:
        return pd.DataFrame(columns=feature_columns() + ["symbol", "as_of", "label"])
    df["as_of"] = pd.to_datetime(df["as_of"])

    # Derived features mirror exactly what features/builder.build_feature_row produces.
    df["atr_pct"] = (df["atr_value"] / df["entry_stop"]).where(df["entry_stop"] > 0, 0.0)
    df["risk_pct"] = (df["risk_per_share"] / df["entry_stop"]).where(df["entry_stop"] > 0, 0.0)
    df["gap_to_atr"] = (df["gap_pct"] / df["atr_pct"]).where(df["atr_pct"] > 0, 0.0)
    df["or_body_pct"] = 0.0       # absent from the persisted trade row; safe zero fallback
    df["or_range_pct"] = 0.0
    df["premarket_dv_log"] = df.get(
        "premarket_dollar_volume", pd.Series([0.0] * len(df))
    ).apply(_safe_log1p)
    df["day_of_week"] = df["as_of"].dt.dayofweek

    # Catalyst defaults.
    for col, default in [
        ("cat_has", 0),
        ("cat_dir", 0.0),
        ("cat_strength", 0),
        ("cat_confidence", 0.0),
        ("cat_item_count", 0),
    ]:
        df[col] = default
    for t in CATALYST_TYPES:
        df[f"cat_type_{t}"] = 0
    df["cat_type_none"] = 1  # default category

    if catalysts is not None and not catalysts.empty:
        df = _merge_catalysts(df, catalysts)

    df["label"] = (df["pnl_r"] > WIN_LABEL_THRESHOLD_R).astype(int)

    cols = feature_columns() + ["symbol", "as_of", "label"]
    missing = [c for c in cols if c not in df.columns]
    for c in missing:
        df[c] = 0
    return df[cols].sort_values("as_of").reset_index(drop=True)


def _merge_catalysts(df: pd.DataFrame, catalysts: pd.DataFrame) -> pd.DataFrame:
    """Left-join the (symbol, as_of) catalyst rows onto trades."""
    cats = catalysts.copy()
    cats["as_of"] = pd.to_datetime(cats["as_of"])
    cats["symbol"] = cats["symbol"].astype(str)
    # Project to the canonical column names.
    sign_map = {"bullish": 1.0, "neutral": 0.0, "bearish": -1.0}
    cats["cat_has"] = (
        (cats["catalyst_type"] != "none") & (cats["item_count"] > 0)
    ).astype(int)
    cats["cat_dir"] = cats["direction"].map(sign_map).fillna(0.0).astype(float)
    cats["cat_strength"] = cats["strength"].astype(int)
    cats["cat_confidence"] = cats["confidence"].astype(float)
    cats["cat_item_count"] = cats["item_count"].astype(int)
    one_hots = pd.get_dummies(cats["catalyst_type"], prefix="cat_type")
    for t in CATALYST_TYPES:
        col = f"cat_type_{t}"
        if col not in one_hots.columns:
            one_hots[col] = 0
    one_hots = one_hots[[f"cat_type_{t}" for t in CATALYST_TYPES]]
    cats_proj = pd.concat(
        [cats[["symbol", "as_of", "cat_has", "cat_dir", "cat_strength",
               "cat_confidence", "cat_item_count"]], one_hots],
        axis=1,
    )
    cat_cols = [c for c in cats_proj.columns if c not in {"symbol", "as_of"}]
    merged = df.drop(columns=cat_cols, errors="ignore").merge(
        cats_proj, on=["symbol", "as_of"], how="left"
    )
    # Reapply defaults for trades that had no matching catalyst row.
    for c in cat_cols:
        if c == "cat_type_none":
            merged[c] = merged[c].fillna(1).astype(int)
        elif c.startswith("cat_type_"):
            merged[c] = merged[c].fillna(0).astype(int)
        elif c == "cat_dir" or c == "cat_confidence":
            merged[c] = merged[c].fillna(0.0).astype(float)
        else:
            merged[c] = merged[c].fillna(0).astype(int)
    return merged


def load_from_disk(
    trades_path: Path, catalysts_path: Path | None = None
) -> pd.DataFrame:
    """Convenience: read parquets and call `build_dataset`."""
    trades = pd.read_parquet(trades_path)
    cats = pd.read_parquet(catalysts_path) if (catalysts_path and catalysts_path.exists()) else None
    log.info(f"loaded {len(trades)} trades; catalysts={'yes' if cats is not None else 'no'}")
    return build_dataset(trades, cats)
