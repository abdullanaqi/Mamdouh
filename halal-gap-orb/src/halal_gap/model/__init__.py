"""Stage 3: XGBoost scoring layer."""
from halal_gap.model.explainer import ExplanationBundle, explain
from halal_gap.model.predictor import ScoringModel, build_scoring_model, fit_xgb
from halal_gap.model.trainer import (
    FoldResult,
    FoldSpec,
    aggregate,
    fit_final_model,
    run_walk_forward,
    walk_forward_specs,
)

__all__ = [
    "ExplanationBundle",
    "FoldResult",
    "FoldSpec",
    "ScoringModel",
    "aggregate",
    "build_scoring_model",
    "explain",
    "fit_final_model",
    "fit_xgb",
    "run_walk_forward",
    "walk_forward_specs",
]
