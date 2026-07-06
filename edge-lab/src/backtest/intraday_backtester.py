"""Intraday backtester.

Given a Strategy (see research/strategy_search.py protocol) and a list of
dates, this: builds a PointInTimeView per date, asks the strategy for
scored candidates using only pre-decision data, simulates every accepted
trade through the pessimistic execution simulator, and returns a trades
DataFrame plus the metric report card.
"""
from __future__ import annotations

import dataclasses

import pandas as pd

from src.backtest import metrics
from src.backtest.execution_simulator import ExitSpec, simulate_trade
from src.data.feature_builder import PointInTimeView
from src.data.massive_loader import load_minute_day
from src.utils.logging import get_logger

log = get_logger(__name__)


def run_backtest(strategy, dates, daily_panel: pd.DataFrame,
                 one_per_day: bool = True, forced: bool = False,
                 minute_loader=load_minute_day, costs=None) -> pd.DataFrame:
    """Returns trades DataFrame. `forced=True` takes the top candidate even
    when its expected value is <= 0 (kept separate per the spec)."""
    rows = []
    for d in dates:
        mdf = minute_loader(d)
        if mdf is None or len(mdf) == 0:
            continue
        view = PointInTimeView.build(d, mdf, daily_panel)
        cands = strategy.candidates_for_day(view)  # list[dict] with scores
        if not cands:
            continue
        cands = sorted(cands, key=lambda c: c["score"], reverse=True)
        chosen = []
        for c in cands:
            if one_per_day and chosen:
                break
            if not forced and c.get("ev", 0.0) <= 0.0:
                continue
            chosen.append(c)
        if forced and not chosen and cands:
            chosen = [cands[0]]
        for c in chosen:
            spec = ExitSpec(tp_pct=c["tp_pct"], sl_pct=c["sl_pct"],
                            breakeven_trigger_pct=c.get("breakeven_trigger_pct"),
                            trail_pct=c.get("trail_pct"))
            res = simulate_trade(view._full_day_unsafe(c["ticker"]),  # noqa: SLF001
                                 c["ticker"], d, c["decision_ts"], spec, costs=costs)
            if res is None:
                continue
            row = dataclasses.asdict(res)
            for k in ("score", "ev", "p_win", "gap_pct", "rvol", "last_price"):
                if k in c:
                    row[k] = c[k]
            row["forced"] = forced and c.get("ev", 0.0) <= 0.0
            rows.append(row)
    trades = pd.DataFrame(rows)
    if len(trades):
        trades["date"] = pd.to_datetime(trades["date"])
    return trades


def report_card(trades: pd.DataFrame, all_days=None) -> dict:
    if len(trades) == 0:
        return {"total_trades": 0, "note": "no trades generated"}
    out = metrics.summarize(trades, all_days)
    out["stability"] = metrics.stability_checks(trades, all_days)
    out["by_exit_reason"] = trades.groupby("exit_reason")["net_ret"].agg(["count", "mean"]).round(4).to_dict()
    return out
