"""Does the ML *pick better names*? A fair selection test that holds the traded
set/structure constant and varies ONLY the selection method.

Three rankings over the SAME candidate pool each day:
  - model   : rank by model score (pred_gain * up_prob)            [the system]
  - scanner : rank by the leak-free premarket gap-momentum scanner  (no ML)
  - random  : random pick from the same candidate pool (seeded)

Two complementary tests, both leak-free:

  Test A - selection only (no fills): score each method by the mean fixed forward
           return (`target_gain`, the post-open 30-min move from the 09:30 open) of
           its top-N names. N, day, and pool are identical across methods, so the
           ONLY difference is the ranking -> isolates selection quality with zero
           trade-count / fill-mechanics confound. Paired (per-day) t-test + bootstrap.

  Test B - equal-trade, same structure: replay each ranking through the real fill
           simulator under the FINAL rule -- at most MAX_POSITIONS entries/day,
           event-driven 2-bar confirmation, NO slot-refilling (simulate_day
           no_refill=True). All three then take a directly comparable <=2 names/day.

Both tests run on 2026 AND the 2025 bear window for out-of-sample replication.
"""
import json
import numpy as np
import pandas as pd
import duckdb
from scipy import stats

import backtest as bt

RESULTS = bt.RESULTS
DB = bt.DB
N_GRID = [1, 2, 5, bt.TOP_N_WATCH]      # picks-per-day grid for Test A (2 == final rule)
BOOT = 10_000


def paired_stats(diff: np.ndarray, rng) -> dict:
    """Paired t-test + bootstrap CI on a per-day difference series (e.g. model-random)."""
    diff = np.asarray(diff, float)
    diff = diff[~np.isnan(diff)]
    n = len(diff)
    if n < 2 or np.allclose(diff, diff[0]):
        return dict(n=n, mean=round(float(diff.mean()), 4) if n else None,
                    t=None, p=None, ci=None)
    t, p = stats.ttest_1samp(diff, 0.0)
    means = diff[rng.integers(0, n, size=(BOOT, n))].mean(axis=1)
    lo, hi = np.percentile(means, [2.5, 97.5])
    return dict(n=n, mean=round(float(diff.mean()), 4), t=round(float(t), 3),
                p=round(float(p), 4), ci=[round(float(lo), 4), round(float(hi), 4)])


def run_window(feature_file: str, label: str) -> dict:
    print(f"\n===== {label}  ({feature_file}) =====", flush=True)
    con = duckdb.connect(str(DB), read_only=True)
    halal = {e["ticker"] for e in json.load(open(RESULTS / "halal_stocks.json"))}
    present = {r[0] for r in con.execute("SELECT DISTINCT ticker FROM minute_bars").fetchall()}
    halal_sql = ",".join(f"'{t}'" for t in sorted(halal & present))

    feats = pd.read_csv(RESULTS / feature_file, dtype={"date": str})
    dates = sorted(feats["date"].unique())
    test_dates = dates[bt.MIN_TRAIN_DAYS:]

    rng = np.random.default_rng(42)
    methods = ("model", "scanner", "random")
    # Test A: per-day mean target_gain of top-N, per method per N
    A = {m: {n: [] for n in N_GRID} for m in methods}
    pool_mean = []                                   # sanity: candidate-pool mean target_gain
    # Test B: per-trade fills, and per-day total pnl (paired)
    B_trades = {m: [] for m in methods}
    B_daypnl = {m: {} for m in methods}

    models, asof = None, None
    for d in test_dates:
        hist = feats[feats["date"] < d]
        if models is None or d[:7] != asof:
            models = bt.train_models(hist)
            asof = d[:7]
        today = feats[feats["date"] == d]
        cands = bt.candidates_for_date(con, halal_sql, today, d)
        if cands.empty:
            continue
        scored = bt.score(cands, models, skew=False)     # real features (fixed mode)
        if "target_gain" not in scored or scored["target_gain"].isna().all():
            continue
        pool_mean.append(float(scored["target_gain"].mean()))

        # three rankings over the SAME candidate pool
        order = bt.premarket_scan_scores(con, halal_sql, d)
        sc = scored.copy()
        sc["scan"] = sc["ticker"].map(order).fillna(-1e9)
        ranked = {
            "model":   scored.sort_values("score", ascending=False),
            "scanner": sc.sort_values("scan", ascending=False),
            "random":  scored.sample(frac=1.0, random_state=int(rng.integers(1e9))),
        }

        # ---- Test A: selection-only forward return ----
        for m in methods:
            tg = ranked[m]["target_gain"].to_numpy()
            for n in N_GRID:
                top = tg[:n]
                top = top[~np.isnan(top)]
                if len(top):
                    A[m][n].append(float(top.mean()))

        # ---- Test B: equalized no-refill fills (top TOP_N_WATCH queue, max 2 seated) ----
        for m in methods:
            rk = ranked[m].head(bt.TOP_N_WATCH)
            res = bt.simulate_day(con, d, rk, "fixed", no_refill=True)
            B_trades[m].extend(res)
            B_daypnl[m][d] = sum(r["pnl_pct"] for r in res)

    con.close()

    # ---- assemble Test A summary ----
    testA = {"pool_mean_target_gain": round(float(np.mean(pool_mean)), 4)}
    for n in N_GRID:
        per = {m: round(float(np.mean(A[m][n])), 4) for m in methods}
        # pair days where all three have a value at this N
        idx = min(len(A[m][n]) for m in methods)
        mr = np.array(A["model"][n][:idx]) - np.array(A["random"][n][:idx])
        ms = np.array(A["model"][n][:idx]) - np.array(A["scanner"][n][:idx])
        testA[f"N={n}"] = dict(
            mean_pick_gain=per, days=idx,
            model_minus_random=paired_stats(mr, rng),
            model_minus_scanner=paired_stats(ms, rng))

    # ---- assemble Test B summary ----
    testB = {}
    for m in methods:
        df = pd.DataFrame(B_trades[m])
        df.to_csv(RESULTS / f"ablation_norefill_{label}_{m}.csv", index=False)
        p = df["pnl_pct"] if not df.empty else pd.Series(dtype=float)
        testB[m] = dict(
            trades=int(len(df)),
            trades_per_day=round(len(df) / max(1, len(B_daypnl[m])), 2),
            win_pct=round((p > 0).mean() * 100, 1) if len(p) else None,
            avg_trade_pct=round(float(p.mean()), 3) if len(p) else None,
            sum_pct=round(float(p.sum()), 1) if len(p) else None)
    days = sorted(set().union(*[set(B_daypnl[m]) for m in methods]))
    dp = {m: np.array([B_daypnl[m].get(d, 0.0) for d in days]) for m in methods}
    testB["model_minus_random_dayPnL"] = paired_stats(dp["model"] - dp["random"], rng)
    testB["model_minus_scanner_dayPnL"] = paired_stats(dp["model"] - dp["scanner"], rng)

    return {"label": label, "test_days": len(test_dates),
            "testA_selection_only": testA, "testB_norefill_fills": testB}


def run():
    windows = [("features_2026.csv", "2026"),
               ("features_bear_2025.csv", "bear2025")]
    out = {label: run_window(f, label) for f, label in windows}
    json.dump(out, open(RESULTS / "ablation_fair_summary.json", "w"), indent=2)
    print("\n" + "=" * 60)
    print(json.dumps(out, indent=2))
    print(f"\nWrote {RESULTS/'ablation_fair_summary.json'}")


if __name__ == "__main__":
    run()
