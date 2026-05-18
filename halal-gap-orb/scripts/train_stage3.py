"""Train the Stage 3 XGBoost scoring model.

Pipeline:
  1. Load `reports/stage1_artifacts.parquet` and (optional)
     `reports/stage2_catalysts.parquet`.
  2. Build the feature matrix via `features.dataset.build_dataset`.
  3. Run walk-forward CV (parameters from `settings.ml.cv`).
  4. Print aggregate OOS metrics + per-fold breakdown.
  5. Fit the final production model on the full dataset and save it to
     `models/scoring_model.pkl`.

Usage:
    uv run python scripts/train_stage3.py
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from halal_gap.features.dataset import load_from_disk
from halal_gap.model.predictor import ScoringModel
from halal_gap.model.trainer import aggregate, fit_final_model, run_walk_forward
from halal_gap.utils.config import reports_dir, settings
from halal_gap.utils.logging import log


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--trades", type=Path, default=reports_dir() / "stage1_artifacts.parquet")
    p.add_argument("--catalysts", type=Path, default=reports_dir() / "stage2_catalysts.parquet")
    p.add_argument(
        "--out", type=Path, default=Path(settings()["paths"]["models_dir"]) / "scoring_model.pkl"
    )
    p.add_argument("--report", type=Path, default=reports_dir() / "stage3_cv.json")
    args = p.parse_args()

    if not args.trades.exists():
        raise SystemExit(f"{args.trades} not found; run Stage 1 first")
    cats = args.catalysts if args.catalysts.exists() else None
    df = load_from_disk(args.trades, cats)
    log.info(f"dataset: rows={len(df)} positive_rate={df['label'].mean():.3f}")

    folds = run_walk_forward(df)
    agg = aggregate(folds)
    fold_rows = [
        {
            "test_start": f.spec.test_start.isoformat(),
            "test_end": f.spec.test_end.isoformat(),
            "n_train": f.n_train,
            "n_test": f.n_test,
            "auc": f.auc,
            "pr_auc": f.pr_auc,
            "logloss": f.logloss,
            "brier": f.brier,
        }
        for f in folds
    ]
    args.report.write_text(json.dumps({"folds": fold_rows, "oos": agg}, indent=2))
    log.info(f"OOS metrics: {agg}")
    log.info(f"CV report -> {args.report}")

    final = fit_final_model(df)
    out_path = final.save(args.out)
    log.info(f"saved final model -> {out_path}")


if __name__ == "__main__":
    main()
