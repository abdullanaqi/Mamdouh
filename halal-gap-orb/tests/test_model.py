"""ScoringModel + walk-forward CV + explainer tests on synthetic data."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from halal_gap.features.builder import feature_columns
from halal_gap.model.predictor import ScoringModel, build_scoring_model
from halal_gap.model.trainer import (
    aggregate,
    fit_final_model,
    run_walk_forward,
    walk_forward_specs,
)


def _synthetic_dataset(n: int = 600, seed: int = 7) -> pd.DataFrame:
    """A synthetic but learnable dataset: high gap + strong catalyst => win."""
    rng = np.random.default_rng(seed)
    cols = feature_columns()
    rows: list[dict[str, object]] = []
    start = pd.Timestamp("2022-01-03")
    for i in range(n):
        gap = float(rng.uniform(0.02, 0.08))
        rvol = float(rng.uniform(1.5, 6.0))
        rvol5 = float(rng.uniform(1.0, 5.0))
        atrpct = float(rng.uniform(0.01, 0.04))
        strength = int(rng.integers(1, 6))
        confidence = float(rng.uniform(0.2, 0.95))
        cat_dir = float(rng.choice([-1.0, 0.0, 1.0]))
        cat_has = int(cat_dir != 0 and strength >= 2)
        cat_type = int(rng.integers(0, 11))
        # Latent score: bullish strong catalyst + large gap + high rvol => high p(win)
        score = (
            1.5 * (gap - 0.04)
            + 0.4 * (rvol - 3.0)
            + 0.5 * (cat_dir * (strength / 5.0))
            + 0.3 * (confidence - 0.5)
            + rng.normal(0, 0.4)
        )
        label = int(score > 0)
        row = {c: 0 for c in cols}
        row.update(
            {
                "gap_pct": gap, "premarket_rvol": rvol,
                "premarket_dv_log": float(rng.uniform(14, 22)),
                "rvol_5min": rvol5, "atr_pct": atrpct,
                "or_body_pct": 0.005, "or_range_pct": 0.01,
                "risk_pct": atrpct * 0.5, "gap_to_atr": gap / max(atrpct, 1e-6),
                "day_of_week": int(rng.integers(0, 5)),
                "cat_has": cat_has, "cat_dir": cat_dir,
                "cat_strength": strength, "cat_confidence": confidence,
                "cat_item_count": int(rng.integers(0, 5)),
            }
        )
        from halal_gap.features.builder import CATALYST_TYPES
        t = CATALYST_TYPES[cat_type % len(CATALYST_TYPES)]
        for tk in CATALYST_TYPES:
            row[f"cat_type_{tk}"] = int(tk == t)
        row["symbol"] = f"S{i % 30:02d}"
        row["as_of"] = start + pd.Timedelta(days=i)
        row["label"] = label
        rows.append(row)
    return pd.DataFrame(rows).sort_values("as_of").reset_index(drop=True)


def test_fit_final_model_round_trip(tmp_path: Path) -> None:
    df = _synthetic_dataset(n=200)
    model = build_scoring_model(df)
    assert model.feature_cols == feature_columns()
    proba = model.predict_proba(df[feature_columns()])
    assert proba.shape == (len(df),)
    assert ((proba >= 0) & (proba <= 1)).all()

    path = tmp_path / "m.pkl"
    model.save(path)
    loaded = ScoringModel.load(path)
    p2 = loaded.predict_proba(df[feature_columns()])
    np.testing.assert_allclose(proba, p2, rtol=1e-6, atol=1e-6)


def test_predict_proba_dict_input() -> None:
    df = _synthetic_dataset(n=120)
    model = build_scoring_model(df)
    one = df[feature_columns()].iloc[0].to_dict()
    p = model.predict_proba(one)
    assert p.shape == (1,)
    assert 0.0 <= p[0] <= 1.0


def test_predict_proba_fills_missing_columns() -> None:
    df = _synthetic_dataset(n=120)
    model = build_scoring_model(df)
    truncated = df[feature_columns()[:5]].iloc[:3].copy()  # drop most features
    p = model.predict_proba(truncated)
    assert p.shape == (3,)


def test_passes_uses_threshold() -> None:
    df = _synthetic_dataset(n=120)
    model = build_scoring_model(df)
    proba = model.predict_proba(df[feature_columns()])
    mask = model.passes(df[feature_columns()], threshold=0.5)
    assert (mask == (proba >= 0.5)).all()


def test_walk_forward_yields_specs() -> None:
    df = _synthetic_dataset(n=600)
    specs = walk_forward_specs(df)
    assert len(specs) >= 1
    for s in specs:
        assert s.train_end == s.test_start
        assert s.train_start < s.train_end
        assert s.test_start < s.test_end


def test_walk_forward_runs_and_metrics_finite() -> None:
    df = _synthetic_dataset(n=600)
    results = run_walk_forward(df)
    assert len(results) >= 1
    for r in results:
        assert r.n_train > 0 and r.n_test > 0
        assert np.isfinite(r.logloss)
        assert np.isfinite(r.brier)
    agg = aggregate(results)
    assert agg["n"] > 0
    assert np.isfinite(agg["logloss"])
    # Latent score is genuinely learnable -> AUC should beat naive
    assert agg["auc"] > 0.6


def test_fit_final_model_rejects_homogeneous_labels() -> None:
    df = _synthetic_dataset(n=100)
    df["label"] = 1  # break it
    with pytest.raises(ValueError):
        fit_final_model(df)


def test_walk_forward_skips_too_small_folds() -> None:
    df = _synthetic_dataset(n=20)  # below the 30-row floor
    results = run_walk_forward(df)
    # Should not throw, just skip with no usable folds.
    assert isinstance(results, list)


def test_explainer_runs() -> None:
    df = _synthetic_dataset(n=200)
    model = build_scoring_model(df)
    from halal_gap.model.explainer import explain
    bundle = explain(model, df[feature_columns()].head(10))
    assert bundle.values.shape == (10, len(feature_columns()))
    top = bundle.top_k(0, k=3)
    assert len(top) == 3
    importance = bundle.mean_abs_importance()
    assert len(importance) == len(feature_columns())
