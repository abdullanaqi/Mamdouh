"""Full research cycle:
1. grid walk-forward for the rule strategy (test untouched until frozen),
2. ML strategy walk-forward,
3. stress battery + edge gate on the aggregate out-of-sample trades,
4. dynamic-vs-fixed exit comparison on the final fold,
5. everything logged to the experiment ledger and reports/.

Prints an honest verdict. On random or edge-free data the expected output
is: NO VALID EDGE FOUND YET.
"""
import argparse
import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.backtest import metrics
from src.backtest.intraday_backtester import report_card, run_backtest
from src.config import CFG, WalkForwardConfig
from src.data.massive_loader import available_minute_dates, load_minute_day
from src.data.universe_builder import load_universe_panel
from src.reports.report_generator import (write_dynamic_exit_report,
                                          write_one_trade_per_day_report,
                                          write_walk_forward_report)
from src.research.experiment_runner import log_experiment
from src.research.overfit_tests import edge_gate, param_stability, run_stress_battery
from src.research.strategy_search import (ExitVariantWrapper, GapRvolStrategy,
                                          MLRankingStrategy, grid)
from src.research.walk_forward import walk_forward_grid, walk_forward_single


def run(param_grid=None, wf: WalkForwardConfig | None = None,
        provenance: str = "REAL Massive flat files", minute_loader=load_minute_day,
        dates=None, panel=None) -> dict:
    panel = panel if panel is not None else load_universe_panel()
    dates = dates if dates is not None else available_minute_dates()
    param_grid = param_grid or grid(GapRvolStrategy)

    print(f"== walk-forward grid: GapRvol ({len(param_grid)} configs) ==")
    res = walk_forward_grid(GapRvolStrategy, param_grid, dates, panel,
                            minute_loader, wf=wf, ckpt_tag="gaprvol_grid")
    if "error" in res:
        print("walk-forward impossible:", res["error"])
        return res
    write_walk_forward_report(res, provenance, "GapRvolStrategy (grid)")
    oos = res["oos_trades"]
    oos_card = res["oos_card"]
    print("aggregate OOS:", json.dumps({k: v for k, v in oos_card.items()
                                        if not isinstance(v, dict)}, default=str))

    chosen_folds = [fl for fl in res["fold_logs"] if fl.get("chosen")]
    verdict = {"edge_candidate": False, "failed_rules": ["no fold produced a chosen configuration"]}
    stress = {}
    stability = {}
    if chosen_folds:
        last = chosen_folds[-1]
        stability = param_stability(last["val_rows"])
        last_fold_idx = last["fold"]
        from src.research.walk_forward import month_folds
        folds = month_folds(dates, wf)
        fold = folds[last_fold_idx]

        def factory():
            s = GapRvolStrategy(**last["chosen"])
            s.n_trials_burned = res["n_trials"]
            return s.fit(fold.train + fold.val, panel, minute_loader)

        print("== stress battery on final-fold test window ==")
        stress = run_stress_battery(factory, oos, fold.test, panel,
                                    minute_loader, n_trials=res["n_trials"])
        verdict = edge_gate(oos_card, stress, stability)

        # forced one-trade-per-day on the same OOS windows, reported separately
        forced_trades = []
        for fl in chosen_folds:
            f = folds[fl["fold"]]
            s = GapRvolStrategy(**fl["chosen"])
            s.n_trials_burned = res["n_trials"]
            s.fit(f.train + f.val, panel, minute_loader)
            t = run_backtest(s, f.test, panel, forced=True, minute_loader=minute_loader)
            if len(t):
                forced_trades.append(t)
        forced = pd.concat(forced_trades, ignore_index=True) if forced_trades else pd.DataFrame()
        all_test_days = pd.DatetimeIndex([d for f in folds for d in f.test])
        forced_card = report_card(forced, all_test_days) if len(forced) else {"total_trades": 0}
        monthly = metrics.monthly_table(oos, all_test_days) if len(oos) else pd.DataFrame()
        write_one_trade_per_day_report(oos_card, forced_card, monthly, provenance)

        print("== dynamic vs fixed exits (final fold) ==")
        variants = [
            ("dynamic_quantile", lambda: factory()),
            ("atr_1.5_1.0", lambda: ExitVariantWrapper(factory(), tp_atr=1.5, sl_atr=1.0, name="atr")),
            ("atr_1.0_0.7", lambda: ExitVariantWrapper(factory(), tp_atr=1.0, sl_atr=0.7, name="atr_tight")),
            ("trail_1pct", lambda: ExitVariantWrapper(factory(), trail_pct=0.01, name="trail")),
            ("breakeven_after_1pct", lambda: ExitVariantWrapper(factory(), breakeven_trigger_pct=0.01, name="be")),
        ]
        rows = []
        for name, mk in variants:
            t = run_backtest(mk(), fold.test, panel, minute_loader=minute_loader)
            rows.append({"name": name,
                         "card": metrics.summarize(t, pd.DatetimeIndex(fold.test)) if len(t) else {"total_trades": 0}})
        write_dynamic_exit_report(rows, provenance)

    print("== ML strategy walk-forward ==")
    ml = walk_forward_single(MLRankingStrategy, dates, panel, minute_loader,
                             wf=wf, ckpt_tag="ml_ranking")
    ml_card = ml.get("oos_card", {})
    print("ML aggregate OOS:", json.dumps({k: v for k, v in ml_card.items()
                                           if not isinstance(v, dict)}, default=str))

    log_experiment(
        idea="GapRvol grid + ML ranking walk-forward cycle",
        reason="baseline continuation hypotheses per research plan",
        data_needed=provenance,
        test_method=f"walk-forward grid ({res['n_trials']} trials), stress battery, edge gate",
        result=json.dumps({"oos": oos_card, "stress_keys": list(stress.keys()),
                           "ml_oos": ml_card}, default=str)[:2000],
        verdict="rejected" if not verdict["edge_candidate"] else "candidate (needs paper-trade confirmation)",
        why="; ".join(verdict["failed_rules"]) or "passed mechanical gate",
        extra={"stability": stability},
    )

    print("\n==================== VERDICT ====================")
    if verdict["edge_candidate"]:
        print("EDGE CANDIDATE (mechanical gate passed). NOT yet a confirmed "
              "edge: requires longer OOS, real-cost review, and paper trading.")
    else:
        print("NO VALID EDGE FOUND YET.")
        for r in verdict["failed_rules"]:
            print("  failed:", r)
    return {"wf": res, "stress": stress, "verdict": verdict, "ml": ml}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--provenance", default="REAL Massive flat files")
    a = ap.parse_args()
    run(provenance=a.provenance)
