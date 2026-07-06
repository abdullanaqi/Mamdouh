"""Candidate ranker.

score = w_ev*EV + w_p*(P(win)-0.5) + w_liq*liquidity + w_stab*stability
        + w_tail*tail_risk + w_slip*slippage_risk + w_fit*overfit_penalty

All component scales are normalized to roughly comparable magnitudes.
The ranker ORDERS candidates; the EV>0 gate (in the selector) decides
whether the top candidate is a recommendation or a FORCED trade.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from src.config import CFG


def _liquidity_score(row) -> float:
    mdv = row.get("med_dollar_vol_20d_prev", np.nan)
    if not mdv or np.isnan(mdv):
        return 0.0
    return float(np.clip(np.log10(max(mdv, 1.0)) - 6.0, 0.0, 3.0) / 3.0)  # $1M->0, $1B->1


def _tail_risk(row) -> float:
    atr = row.get("atr_pct_prev", np.nan)
    v = row.get("vol_20d_prev", np.nan)
    parts = [x for x in (atr, v) if x is not None and not np.isnan(x)]
    return float(np.clip(np.mean(parts) / 0.10, 0.0, 1.5)) if parts else 0.5


def _slippage_risk(row) -> float:
    bps = row.get("slippage_bps_est", 10.0)
    return float(np.clip(bps / 50.0, 0.0, 1.5))


def rank_score(row: dict | pd.Series, ev: float, p_win: float,
               stability: float = 0.5, overfit_penalty: float = 0.0) -> float:
    """stability in [0,1] comes from walk-forward month-consistency of the
    strategy that produced the candidate; overfit_penalty in [0,1] from the
    number of parameters / trials burned (see overfit_tests)."""
    w = CFG.selector_weights
    ev_n = float(np.clip(ev / 0.02, -2.0, 2.0))          # 2% EV -> 1.0
    p_n = float(np.clip((p_win - 0.5) / 0.2, -1.5, 1.5)) if not np.isnan(p_win) else 0.0
    return (w.ev * ev_n + w.p_win * p_n
            + w.liquidity * _liquidity_score(row)
            + w.stability * stability
            + w.tail_risk * _tail_risk(row)
            + w.slippage_risk * _slippage_risk(row)
            + w.overfit_penalty * overfit_penalty)
