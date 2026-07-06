"""Label construction. This module is the ONLY place (besides the
execution simulator it delegates to) allowed to touch post-decision data,
because labels describe the realized future for training/evaluation.

Labels produced per (ticker, date, decision_ts):
- mfe_eod / mae_eod : max favorable/adverse excursion to the time stop
- ret_eod           : raw open->timestop close return
- hit_{tp}b4_{sl}   : first-touch outcome under a reference ATR exit
- net_ret_ref       : net return under the reference exit incl. costs
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from src.backtest.execution_simulator import ExitSpec, simulate_trade
from src.config import CFG
from src.utils.time_utils import ts_et


def label_candidate(view, ticker: str, decision_ts: pd.Timestamp,
                    ref_tp_pct: float, ref_sl_pct: float) -> dict | None:
    day_bars = view._full_day_unsafe(ticker)  # noqa: SLF001 -- sanctioned use
    if day_bars is None or len(day_bars) == 0:
        return None
    spec = ExitSpec(tp_pct=ref_tp_pct, sl_pct=ref_sl_pct)
    res = simulate_trade(day_bars, ticker, view.date, decision_ts, spec)
    if res is None or res.exit_reason == "no_fill":
        return None

    # MFE/MAE measured on raw prices from decision to the time stop,
    # independent of the reference exit outcome:
    stop_ts = ts_et(pd.Timestamp(view.date).date(), CFG.session.default_time_stop)
    window = day_bars[(day_bars["ts"] >= decision_ts) & (day_bars["ts"] < stop_ts)]
    if len(window) == 0:
        return None
    entry_raw = float(window.iloc[0]["open"])
    mfe_eod = float(window["high"].max() / entry_raw - 1.0)
    mae_eod = float(window["low"].min() / entry_raw - 1.0)
    ret_eod = float(window.iloc[-1]["close"] / entry_raw - 1.0)

    return {
        "hit_tp_first": 1.0 if res.exit_reason == "tp" else 0.0,
        "exit_reason": res.exit_reason,
        "net_ret_ref": res.net_ret,
        "gross_ret_ref": res.gross_ret,
        "mfe_eod": mfe_eod,
        "mae_eod": mae_eod,
        "ret_eod": ret_eod,
        "ref_tp_pct": ref_tp_pct,
        "ref_sl_pct": ref_sl_pct,
    }
