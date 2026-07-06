import numpy as np
import pandas as pd
import pytest

from src.models.dynamic_exit_model import DynamicExitModel, empirical_first_touch


def test_atr_fallback_levels_bounded():
    m = DynamicExitModel()  # unfit -> fallback
    row = pd.Series({"atr_pct_prev": 0.03})
    tp, sl, why = m.levels(row)
    assert tp == pytest.approx(0.045)
    assert sl == pytest.approx(0.03)
    assert "ATR fallback" in why
    assert 0.5 * 0.03 <= tp <= 3.0 * 0.03
    assert 0.4 * 0.03 <= sl <= 2.0 * 0.03


def test_quantile_model_levels_within_atr_bounds():
    rng = np.random.default_rng(0)
    n = 400
    df = pd.DataFrame({
        "atr_pct_prev": rng.uniform(0.01, 0.05, n),
        "rvol": rng.uniform(1, 5, n),
        "gap_pct": rng.normal(0.02, 0.02, n),
        "mfe_eod": rng.uniform(0, 0.08, n),
        "mae_eod": -rng.uniform(0, 0.05, n),
        "date": pd.Timestamp("2025-01-02"),
    })
    m = DynamicExitModel().fit(df)
    row = df.iloc[0]
    tp, sl, why = m.levels(row)
    atr = row["atr_pct_prev"]
    assert 0.5 * atr <= tp <= 3.0 * atr
    assert 0.4 * atr <= sl <= 2.0 * atr
    assert "predicted MFE" in why


def _labels(n_win=30, n_loss=20, n_flat=10):
    rows = []
    rows += [{"mfe_eod": 0.05, "mae_eod": -0.005, "ret_eod": 0.04}] * n_win
    rows += [{"mfe_eod": 0.01, "mae_eod": -0.05, "ret_eod": -0.04}] * n_loss
    rows += [{"mfe_eod": 0.01, "mae_eod": -0.005, "ret_eod": 0.002}] * n_flat
    return pd.DataFrame(rows)


def test_empirical_first_touch_probabilities_and_ev():
    est = empirical_first_touch(_labels(), tp_pct=0.03, sl_pct=0.02,
                                costs_ret_haircut=0.001)
    assert est["n"] == 60
    assert est["p_win"] == pytest.approx(30 / 60)
    assert est["p_loss"] == pytest.approx(20 / 60)
    expected_ev = 0.5 * 0.03 - (20 / 60) * 0.02 + 0.002 * (10 / 60) - 0.001
    assert est["ev"] == pytest.approx(expected_ev, abs=1e-9)


def test_ambiguous_paths_counted_as_losses():
    df = pd.DataFrame([{"mfe_eod": 0.05, "mae_eod": -0.05, "ret_eod": 0.0}] * 60)
    est = empirical_first_touch(df, tp_pct=0.03, sl_pct=0.02, costs_ret_haircut=0.0)
    assert est["p_win"] == 0.0
    assert est["p_loss"] == 1.0
    assert est["ev"] < 0
