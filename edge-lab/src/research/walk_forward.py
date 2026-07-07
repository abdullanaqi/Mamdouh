"""Walk-forward validation.

Folds are built on calendar months of AVAILABLE trading dates:
    [train (T months)] --embargo--> [val (V)] --embargo--> [test (K)]
stepping forward by S months. Within each fold:
  1. every parameter set is fit on TRAIN and scored on VAL,
  2. the winner (by expectancy with a minimum-trades gate) is frozen,
  3. only the winner is evaluated on TEST.
Aggregate out-of-sample performance = concatenated TEST trades across
folds. n_trials (grid size x folds) is recorded for the overfit penalty.
"""
from __future__ import annotations

import pickle
from dataclasses import dataclass

import pandas as pd

from src.backtest.intraday_backtester import report_card, run_backtest
from src.config import CFG, WalkForwardConfig
from src.utils.logging import get_logger

log = get_logger(__name__)

MIN_VAL_TRADES = 8


def _fold_sig(fold: "Fold") -> tuple:
    return (len(fold.train), str(fold.train[0]), str(fold.train[-1]),
            len(fold.val), str(fold.val[-1]), len(fold.test), str(fold.test[-1]))


def _ckpt_load(tag: str | None, fi: int, sig: tuple, extra_sig) -> dict | None:
    """Per-fold checkpoints let a multi-hour walk-forward survive machine
    restarts. A checkpoint is reused only when the fold's date windows AND
    the caller's extra signature (strategy, grid) match exactly; results
    are identical to an uninterrupted run because per-fold inputs -- grid,
    windows, and the deterministic n_trials counter -- are all replayed."""
    if tag is None:
        return None
    p = CFG.paths.walk_forward / f"ckpt_{tag}_fold{fi}.pkl"
    if not p.exists():
        return None
    try:
        with open(p, "rb") as f:
            blob = pickle.load(f)
    except Exception:
        return None
    if blob.get("sig") != sig or blob.get("extra_sig") != extra_sig:
        log.warning("checkpoint %s ignored: signature mismatch", p.name)
        return None
    log.info("fold %d restored from checkpoint %s", fi, p.name)
    return blob


def _ckpt_save(tag: str | None, fi: int, sig: tuple, extra_sig, payload: dict) -> None:
    if tag is None:
        return
    p = CFG.paths.walk_forward / f"ckpt_{tag}_fold{fi}.pkl"
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    with open(tmp, "wb") as f:
        pickle.dump({"sig": sig, "extra_sig": extra_sig, **payload}, f)
    tmp.rename(p)


@dataclass
class Fold:
    train: list
    val: list
    test: list


def month_folds(dates: list[pd.Timestamp], wf: WalkForwardConfig | None = None) -> list[Fold]:
    wf = wf or CFG.wf
    dates = sorted(pd.Timestamp(d) for d in dates)
    months = sorted({d.to_period("M") for d in dates})
    by_m = {m: [d for d in dates if d.to_period("M") == m] for m in months}
    folds = []
    i = 0
    while True:
        t0, t1 = i, i + wf.train_months
        v1 = t1 + wf.val_months
        k1 = v1 + wf.test_months
        if k1 > len(months):
            break
        train = [d for m in months[t0:t1] for d in by_m[m]]
        val = [d for m in months[t1:v1] for d in by_m[m]][wf.embargo_days:]
        test = [d for m in months[v1:k1] for d in by_m[m]][wf.embargo_days:]
        if train and val and test:
            folds.append(Fold(train, val, test))
        i += wf.step_months
    return folds


def _fit_and_eval(strategy, train, eval_dates, daily_panel, minute_loader,
                  forced: bool) -> tuple[pd.DataFrame, dict]:
    strategy.fit(train, daily_panel, minute_loader)
    trades = run_backtest(strategy, eval_dates, daily_panel, forced=forced,
                          minute_loader=minute_loader)
    return trades, report_card(trades, pd.DatetimeIndex(eval_dates))


def walk_forward_grid(strategy_cls, param_grid: list[dict], dates, daily_panel,
                      minute_loader, wf: WalkForwardConfig | None = None,
                      forced: bool = False, ckpt_tag: str | None = None) -> dict:
    folds = month_folds(dates, wf)
    if not folds:
        return {"error": "not enough months of data for a single fold",
                "n_dates": len(dates)}
    extra_sig = (strategy_cls.__name__, forced,
                 tuple(tuple(sorted(p.items())) for p in param_grid))
    all_test_trades, fold_logs = [], []
    n_trials = 0
    for fi, fold in enumerate(folds):
        sig = _fold_sig(fold)
        ckpt = _ckpt_load(ckpt_tag, fi, sig, extra_sig)
        if ckpt is not None:
            n_trials += len(param_grid)
            fold_logs.append(ckpt["fold_log"])
            if ckpt["test_trades"] is not None and len(ckpt["test_trades"]):
                all_test_trades.append(ckpt["test_trades"])
            continue
        best, best_exp, val_rows = None, None, []
        for params in param_grid:
            n_trials += 1
            strat = strategy_cls(**params)
            strat.n_trials_burned = n_trials
            val_trades, val_card = _fit_and_eval(strat, fold.train, fold.val,
                                                 daily_panel, minute_loader, forced)
            exp = val_card.get("expectancy")
            ok = (val_card.get("total_trades", 0) >= MIN_VAL_TRADES
                  and exp is not None)
            val_rows.append({"params": params, "val": val_card, "eligible": ok})
            if ok and (best_exp is None or exp > best_exp):
                best, best_exp = params, exp
        if best is None:
            fold_log = {"fold": fi, "chosen": None, "val_rows": val_rows,
                        "note": "no param set produced enough validation trades"}
            fold_logs.append(fold_log)
            _ckpt_save(ckpt_tag, fi, sig, extra_sig,
                       {"fold_log": fold_log, "test_trades": None})
            continue
        strat = strategy_cls(**best)
        strat.n_trials_burned = n_trials
        # refit on train+val so the frozen model uses all pre-test data
        test_trades, test_card = _fit_and_eval(strat, fold.train + fold.val,
                                               fold.test, daily_panel,
                                               minute_loader, forced)
        if len(test_trades):
            test_trades["fold"] = fi
        fold_log = {"fold": fi, "chosen": best, "val_expectancy": best_exp,
                    "test": test_card, "val_rows": val_rows}
        fold_logs.append(fold_log)
        _ckpt_save(ckpt_tag, fi, sig, extra_sig,
                   {"fold_log": fold_log,
                    "test_trades": test_trades if len(test_trades) else None})
        if len(test_trades):
            all_test_trades.append(test_trades)
        log.info("fold %d: chose %s | val_exp=%.5f | test_exp=%s", fi, best,
                 best_exp, test_card.get("expectancy"))
    oos = pd.concat(all_test_trades, ignore_index=True) if all_test_trades else pd.DataFrame()
    all_test_days = pd.DatetimeIndex([d for f in folds for d in f.test])
    return {
        "n_folds": len(folds),
        "n_trials": n_trials,
        "fold_logs": fold_logs,
        "oos_trades": oos,
        "oos_card": report_card(oos, all_test_days) if len(oos) else {"total_trades": 0},
    }


def walk_forward_single(strategy_factory, dates, daily_panel, minute_loader,
                        wf: WalkForwardConfig | None = None, forced: bool = False,
                        ckpt_tag: str | None = None) -> dict:
    """For strategies that self-tune internally (e.g., ML): fit on
    train+val, evaluate on test, per fold. No outer grid."""
    folds = month_folds(dates, wf)
    if not folds:
        return {"error": "not enough months of data for a single fold"}
    extra_sig = (strategy_factory().describe(), forced)
    all_test, fold_logs = [], []
    for fi, fold in enumerate(folds):
        sig = _fold_sig(fold)
        ckpt = _ckpt_load(ckpt_tag, fi, sig, extra_sig)
        if ckpt is not None:
            fold_logs.append(ckpt["fold_log"])
            if ckpt["test_trades"] is not None and len(ckpt["test_trades"]):
                all_test.append(ckpt["test_trades"])
            continue
        strat = strategy_factory()
        trades, card = _fit_and_eval(strat, fold.train + fold.val, fold.test,
                                     daily_panel, minute_loader, forced)
        if len(trades):
            trades["fold"] = fi
            all_test.append(trades)
        fold_logs.append({"fold": fi, "test": card})
        _ckpt_save(ckpt_tag, fi, sig, extra_sig,
                   {"fold_log": {"fold": fi, "test": card},
                    "test_trades": trades if len(trades) else None})
    oos = pd.concat(all_test, ignore_index=True) if all_test else pd.DataFrame()
    all_test_days = pd.DatetimeIndex([d for f in folds for d in f.test])
    return {"n_folds": len(folds), "fold_logs": fold_logs, "oos_trades": oos,
            "oos_card": report_card(oos, all_test_days) if len(oos) else {"total_trades": 0}}
