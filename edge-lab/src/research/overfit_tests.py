"""Anti-overfitting battery.

Every candidate edge must pass this battery on OUT-OF-SAMPLE trades before
it may be called an edge. Each test maps to a rejection rule from the
project spec (section 11). What these tests can and cannot prove is spelled
out in reports/LEAKAGE_AUDIT.md -- passing here is necessary, not
sufficient.
"""
from __future__ import annotations

import dataclasses

import numpy as np
import pandas as pd

from src.backtest import metrics
from src.backtest.intraday_backtester import report_card, run_backtest
from src.config import CFG, Costs


class DelayedStrategy:
    """Entry-delay wrapper: decisions are made at T (same features), fills
    happen at T+delay. A real edge should degrade gracefully, not vanish."""

    def __init__(self, inner, delay_minutes: int):
        self.inner = inner
        self.delay = pd.Timedelta(minutes=delay_minutes)

    def fit(self, *a, **k):
        self.inner.fit(*a, **k)
        return self

    def candidates_for_day(self, view):
        cands = self.inner.candidates_for_day(view)
        for c in cands:
            c["decision_ts"] = c["decision_ts"] + self.delay
        return cands


def stressed_costs(slippage_mult: float = 1.0, fee_mult: float = 1.0) -> Costs:
    base = CFG.costs
    table = tuple((cut, bps * slippage_mult) for cut, bps in base.slippage_bps_by_price)
    return dataclasses.replace(
        base,
        fee_round_trip_usd=base.fee_round_trip_usd * fee_mult,
        slippage_bps_by_price=table,
        impact_k_bps=base.impact_k_bps * slippage_mult,
        stop_extra_slippage_bps=base.stop_extra_slippage_bps * slippage_mult,
    )


def bootstrap_pvalue_mean_gt0(net_rets: pd.Series, n_boot: int = 5000, seed: int = 0) -> float:
    """P(mean <= 0 | data) via centered bootstrap. Small = good, but with a
    grid of size G the honest bar is roughly p < 0.05/G (Bonferroni-ish);
    we report both raw p and the trial count and let the gate decide."""
    x = net_rets.dropna().to_numpy()
    if len(x) < 20:
        return float("nan")
    rng = np.random.default_rng(seed)
    obs = x.mean()
    centered = x - obs
    sims = rng.choice(centered, size=(n_boot, len(x)), replace=True).mean(axis=1)
    return float((sims >= obs).mean())


def drop_top_ticker(trades: pd.DataFrame) -> dict:
    t = trades[trades["exit_reason"] != "no_fill"]
    if len(t) == 0:
        return {}
    top = t.groupby("ticker")["net_ret"].sum().idxmax()
    return {"dropped": top, "card": metrics.summarize(t[t["ticker"] != top])}


def drop_best_month(trades: pd.DataFrame) -> dict:
    t = trades[trades["exit_reason"] != "no_fill"]
    if len(t) == 0:
        return {}
    top = t.groupby(t["date"].dt.to_period("M"))["net_ret"].sum().idxmax()
    keep = t["date"].dt.to_period("M") != top
    return {"dropped": str(top), "card": metrics.summarize(t[keep])}


def param_stability(val_rows: list[dict]) -> dict:
    """Winner should not be a lonely spike: compare its validation
    expectancy to the median of all eligible grid points."""
    elig = [r for r in val_rows if r.get("eligible")]
    if len(elig) < 3:
        return {"ok": None, "note": "grid too small to judge stability"}
    exps = [r["val"]["expectancy"] for r in elig]
    best = max(exps)
    med = float(np.median(exps))
    return {"best_val_expectancy": best, "median_val_expectancy": med,
            "spike_ratio": best / med if med > 0 else float("inf"),
            "n_eligible": len(elig)}


def run_stress_battery(strategy_factory, oos_trades: pd.DataFrame,
                       test_dates, daily_panel, minute_loader,
                       n_trials: int) -> dict:
    """Reruns the frozen strategy on the SAME test dates under stress.
    strategy_factory() must return the already-chosen configuration
    refit on pre-test data by the caller."""
    out: dict = {}
    base = metrics.summarize(oos_trades, pd.DatetimeIndex(test_dates)) if len(oos_trades) else {}
    out["base"] = base
    for name, sm, fm in (("slippage_x2", 2.0, 1.0), ("slippage_x3", 3.0, 1.0),
                         ("fees_x2", 1.0, 2.0)):
        tr = run_backtest(strategy_factory(), test_dates, daily_panel,
                          minute_loader=minute_loader, costs=stressed_costs(sm, fm))
        out[name] = metrics.summarize(tr, pd.DatetimeIndex(test_dates)) if len(tr) else {"total_trades": 0}
    for delay in (1, 3):
        tr = run_backtest(DelayedStrategy(strategy_factory(), delay), test_dates,
                          daily_panel, minute_loader=minute_loader)
        out[f"entry_delay_{delay}m"] = metrics.summarize(tr, pd.DatetimeIndex(test_dates)) if len(tr) else {"total_trades": 0}
    if len(oos_trades):
        out["drop_top_ticker"] = drop_top_ticker(oos_trades)
        out["drop_best_month"] = drop_best_month(oos_trades)
        out["bootstrap_p_mean_gt0"] = bootstrap_pvalue_mean_gt0(
            oos_trades.loc[oos_trades["exit_reason"] != "no_fill", "net_ret"])
        out["n_trials"] = n_trials
    return out


def edge_gate(oos_card: dict, stress: dict, stability: dict | None = None) -> dict:
    """Mechanical rejection rules (spec sections 11 & 15). Returns verdict +
    every failed rule. Passing this gate is NECESSARY for 'edge found',
    never sufficient on its own."""
    fails = []
    def _exp(card):
        e = card.get("expectancy")
        return e if isinstance(e, (int, float)) else None

    if not oos_card or oos_card.get("total_trades", 0) < 40:
        fails.append("fewer than 40 out-of-sample trades")
    e = _exp(oos_card)
    if e is None or e <= 0:
        fails.append("out-of-sample expectancy not positive")
    if oos_card.get("pct_months_positive", 0) < 0.55:
        fails.append("fewer than 55% of OOS months positive")
    if isinstance(oos_card.get("max_drawdown"), (int, float)) and oos_card["max_drawdown"] < -0.25:
        fails.append("max drawdown worse than -25%")
    st = oos_card.get("stability") or {}
    if isinstance(st.get("top_ticker_pnl_share"), (int, float)) and st["top_ticker_pnl_share"] > 0.5:
        fails.append("more than half of PnL from one ticker")
    if isinstance(st.get("top_month_pnl_share"), (int, float)) and st["top_month_pnl_share"] > 0.6:
        fails.append("more than 60% of PnL from one month")
    for k in ("slippage_x2", "fees_x2", "entry_delay_1m"):
        card = stress.get(k) or {}
        se = _exp(card)
        if se is None or se <= 0:
            fails.append(f"expectancy not positive under stress: {k}")
    p = stress.get("bootstrap_p_mean_gt0")
    n_trials = stress.get("n_trials", 1) or 1
    if p is None or (isinstance(p, float) and (np.isnan(p) or p > 0.05 / max(1, min(n_trials, 100)))):
        fails.append(f"bootstrap p={p} fails trial-adjusted threshold (n_trials={n_trials})")
    if stability and isinstance(stability.get("spike_ratio"), (int, float)) and stability["spike_ratio"] > 4:
        fails.append("winning params are a lonely spike vs. neighbors")
    return {"edge_candidate": len(fails) == 0, "failed_rules": fails}
