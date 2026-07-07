"""The vectorized _gate_mask must agree with the per-row _gate exactly,
including NaN / None / missing-column edge cases."""
import itertools
import math

import numpy as np
import pandas as pd

from src.research.strategy_search import GapRvolStrategy, grid


def _edge_rows() -> list[dict]:
    vals_gap = [np.nan, None, -0.02, 0.0, 0.01, 0.02, 0.15, 0.30, 0.31, 0.5]
    vals_rvol = [np.nan, None, 0.0, 1.0, 1.5, 2.5, 4.0, 9.0]
    rows = []
    rng = np.random.default_rng(7)
    for g, rv in itertools.product(vals_gap, vals_rvol):
        rows.append({
            "gap_pct": g, "rvol": rv,
            "ret_since_open": rng.choice([np.nan, -0.01, 0.0, 0.005]),
            "orb_breakout": rng.choice([np.nan, 0.0, 1.0]),
            "dist_vwap": rng.choice([np.nan, -0.002, 0.0, 0.003]),
        })
    # rows missing keys entirely
    rows.append({"gap_pct": 0.05, "rvol": 2.0})
    rows.append({"rvol": 2.0, "ret_since_open": 0.01})
    return rows


def test_gate_mask_matches_gate_for_every_grid_config():
    rows = _edge_rows()
    df = pd.DataFrame(rows)
    for params in grid(GapRvolStrategy):
        s = GapRvolStrategy(**params)
        expected = [bool(s._gate(r)) for r in rows]
        got = s._gate_mask(df).tolist()
        assert got == expected, f"mismatch for {params}"


def test_gate_mask_missing_columns():
    s = GapRvolStrategy()
    df = pd.DataFrame([{"ticker": "A"}, {"ticker": "B"}])
    assert s._gate_mask(df).tolist() == [False, False]
