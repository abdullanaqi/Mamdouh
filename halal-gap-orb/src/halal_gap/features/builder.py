"""Feature engineering for the Stage 3 ML scoring layer.

Hard rule: features are computed at the 09:35 ET decision time. They MUST
NOT include any information about how the trade actually played out
(entry_price, exit_price, pnl, high_water_r, exit_reason, etc.).
"""
from __future__ import annotations

from typing import Any

import pandas as pd

from halal_gap.catalyst.models import CatalystFeature, ClassifierResult
from halal_gap.scanner.gap_scanner import GapCandidate
from halal_gap.strategy.orb import OpeningRange, TradeSetup


# One-hot column names for the categorical catalyst_type field, in fixed order
# so that downstream feature vectors are always aligned.
CATALYST_TYPES: tuple[str, ...] = (
    "earnings", "guidance", "analyst", "fda", "mna",
    "contract", "product", "legal", "macro", "other", "none",
)


def feature_columns() -> list[str]:
    """The canonical, ordered list of feature column names.

    Anything inside this list is safe to feed the model; anything outside is
    not a feature (e.g. labels, timestamps, IDs).
    """
    base = [
        "gap_pct",
        "premarket_rvol",
        "premarket_dv_log",
        "rvol_5min",
        "atr_pct",
        "or_body_pct",
        "or_range_pct",
        "risk_pct",
        "gap_to_atr",
        "day_of_week",
        # Catalyst features (zero-filled when classifier was not run).
        "cat_has",
        "cat_dir",
        "cat_strength",
        "cat_confidence",
        "cat_item_count",
    ]
    base += [f"cat_type_{t}" for t in CATALYST_TYPES]
    return base


def _one_hot_catalyst_type(t: str | None) -> dict[str, int]:
    """One-hot encode `catalyst_type` against `CATALYST_TYPES`."""
    return {f"cat_type_{c}": 1 if t == c else 0 for c in CATALYST_TYPES}


def build_feature_row(
    setup: TradeSetup,
    opening: OpeningRange,
    *,
    catalyst: CatalystFeature | ClassifierResult | None = None,
) -> dict[str, Any]:
    """Build one feature row from a setup + opening-range + (optional) catalyst.

    Example:
        >>> # row = build_feature_row(setup, opening_range, catalyst=feature)
        >>> # assert set(row.keys()) == set(feature_columns()) | {"symbol", "as_of"}
    """
    cand: GapCandidate = setup.candidate

    or_range = opening.high - opening.low
    or_body = abs(opening.close - opening.open)
    open_px = opening.open if opening.open > 0 else opening.close
    or_body_pct = or_body / open_px if open_px > 0 else 0.0
    or_range_pct = or_range / open_px if open_px > 0 else 0.0
    risk_pct = setup.risk_per_share / setup.entry_stop if setup.entry_stop > 0 else 0.0
    atr_pct = setup.atr_value / setup.entry_stop if setup.entry_stop > 0 else 0.0
    gap_to_atr = cand.gap_pct / atr_pct if atr_pct > 0 else 0.0

    row: dict[str, Any] = {
        "symbol": cand.symbol,
        "as_of": pd.Timestamp(cand.as_of),
        "gap_pct": float(cand.gap_pct),
        "premarket_rvol": float(cand.premarket_rvol),
        "premarket_dv_log": _safe_log1p(cand.premarket_dollar_volume),
        "rvol_5min": float(opening.rvol_5min) if opening.rvol_5min == opening.rvol_5min else 0.0,
        "atr_pct": float(atr_pct),
        "or_body_pct": float(or_body_pct),
        "or_range_pct": float(or_range_pct),
        "risk_pct": float(risk_pct),
        "gap_to_atr": float(gap_to_atr),
        "day_of_week": int(pd.Timestamp(cand.as_of).dayofweek),
        "cat_has": 0,
        "cat_dir": 0.0,
        "cat_strength": 0,
        "cat_confidence": 0.0,
        "cat_item_count": 0,
    }
    row.update(_one_hot_catalyst_type(None))
    row["cat_type_none"] = 1   # default when no catalyst was supplied
    if catalyst is not None:
        _apply_catalyst(row, catalyst)
    return row


def _apply_catalyst(
    row: dict[str, Any], catalyst: CatalystFeature | ClassifierResult
) -> None:
    if isinstance(catalyst, ClassifierResult):
        cat_type = catalyst.output.catalyst_type
        direction = catalyst.output.direction
        sign = {"bullish": 1.0, "neutral": 0.0, "bearish": -1.0}[direction]
        row["cat_has"] = int(cat_type != "none" and catalyst.item_count > 0)
        row["cat_dir"] = sign
        row["cat_strength"] = int(catalyst.output.strength)
        row["cat_confidence"] = float(catalyst.output.confidence)
        row["cat_item_count"] = int(catalyst.item_count)
    else:  # CatalystFeature
        cat_type = catalyst.catalyst_type
        row["cat_has"] = int(catalyst.has_catalyst)
        row["cat_dir"] = float(catalyst.direction_score)
        row["cat_strength"] = int(catalyst.strength)
        row["cat_confidence"] = float(catalyst.confidence)
        row["cat_item_count"] = int(catalyst.item_count)
    row.update(_one_hot_catalyst_type(cat_type))


def _safe_log1p(x: float) -> float:
    """log(1+x) clipped to non-negative input."""
    import math
    return math.log1p(max(0.0, float(x)))
