"""Does the ML *pick better names*? A fair selection test that holds the traded
set/structure constant and varies ONLY the selection method.

Three rankings over the SAME candidate pool each day:
  - model   : rank by model score (predicted 30-min gain, single model)  [the system]
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


def csv_candidate_pool(day: pd.DataFrame) -> pd.DataFrame:
    """Leak-free candidate pool from the feature CSV (no DB needed): keep names
    priced >= MIN_PRICE with a known forward return, then take the top
    TOP_N_WATCH*2 by the premarket scanner proxy = pm_pct * log1p(pm_volume).

    Note: this uses pm_pct (premarket-session return) as the momentum signal, a
    leak-free non-ML baseline available for every window. It is a proxy for the
    live DB scanner (which ranks by prev-close gap x premarket volume); Test B
    uses the exact DB scanner for execution fidelity."""
    d = day[(day["open_price"] >= bt.MIN_PRICE)].dropna(subset=["target_gain"]).copy()
    if d.empty:
        return d
    d["pm_scan"] = d["pm_pct"].fillna(0.0) * np.log1p(d["pm_volume"].clip(lower=0).fillna(0.0))
    return d.sort_values("pm_scan", ascending=False).head(bt.TOP_N_WATCH * 2)


def run_window(feature_file: str, label: str, bars_available: bool) -> dict:
    print(f"\n===== {label}  ({feature_file})  bars={bars_available} =====", flush=True)
    con = duckdb.connect(str(DB), read_only=True)
    halal = {e["ticker"] for e in json.load(open(RESULTS / "halal_stocks.json"))}
    present = {r[0] for r in con.execute("SELECT DISTINCT ticker FROM minute_bars").fetchall()}
    halal_sql = ",".join(f"'{t}'" for t in sorted(halal & present))

    feats = pd.read_csv(RESULTS / feature_file, dtype={"date": str})
    dates = sorted(feats["date"].unique())
    test_dates = dates[bt.MIN_TRAIN_DAYS:]

    rng = np.random.default_rng(42)
    methods = ("model", "scanner", "random")
    # Test A (CSV, always): per-day mean target_gain of top-N, per method per N
    A = {m: {n: [] for n in N_GRID} for m in methods}
    pool_mean = []                                   # sanity: candidate-pool mean target_gain
    # Test B (DB fills, bars only): per-trade fills, and per-day total pnl (paired)
    B_trades = {m: [] for m in methods}
    B_daypnl = {m: {} for m in methods}

    models, asof = None, None
    for d in test_dates:
        hist = feats[feats["date"] < d]
        if models is None or d[:7] != asof:
            models = bt.train_models(hist)
            asof = d[:7]

        # ---- Test A: selection-only forward return (CSV pool, no DB) ----
        pool = csv_candidate_pool(feats[feats["date"] == d])
        if not pool.empty:
            scored = bt.score(pool, models, skew=False)
            pool_mean.append(float(scored["target_gain"].mean()))
            rankedA = {
                "model":   scored.sort_values("score", ascending=False),
                "scanner": scored.sort_values("pm_scan", ascending=False),
                "random":  scored.sample(frac=1.0, random_state=int(rng.integers(1e9))),
            }
            for m in methods:
                tg = rankedA[m]["target_gain"].to_numpy()
                for n in N_GRID:
                    top = tg[:n][~np.isnan(tg[:n])]
                    if len(top):
                        A[m][n].append(float(top.mean()))

        # ---- Test B: equalized no-refill real fills (DB; mirrors live selector) ----
        if bars_available:
            cands = bt.candidates_for_date(con, halal_sql, feats[feats["date"] == d], d)
            if cands.empty:
                continue
            sc = bt.score(cands, models, skew=False)
            order = bt.premarket_scan_scores(con, halal_sql, d)
            sc["scan"] = sc["ticker"].map(order).fillna(-1e9)
            rankedB = {
                "model":   sc.sort_values("score", ascending=False).head(bt.TOP_N_WATCH),
                "scanner": sc.sort_values("scan", ascending=False).head(bt.TOP_N_WATCH),
                "random":  sc.sample(min(bt.TOP_N_WATCH, len(sc)),
                                     random_state=int(rng.integers(1e9))),
            }
            for m in methods:
                res = bt.simulate_day(con, d, rankedB[m], "fixed", no_refill=True)
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
    if not bars_available:
        return {"label": label, "test_days": len(test_dates),
                "testA_selection_only": testA,
                "testB_norefill_fills": {
                    "note": "no intraday bars for this window in the DB; fills test not run"}}
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


def _db_date_range() -> tuple[str, str]:
    """Min/max calendar date present in minute_bars (window_start is UTC ns; for
    intraday US-equity bars the UTC date equals the ET trading date)."""
    con = duckdb.connect(str(DB), read_only=True)
    lo, hi = con.execute(
        "SELECT min(window_start), max(window_start) FROM minute_bars").fetchone()
    con.close()
    to_d = lambda ns: pd.Timestamp(int(ns), unit="ns", tz="UTC").strftime("%Y-%m-%d")
    return to_d(lo), to_d(hi)


def run():
    db_lo, db_hi = _db_date_range()
    print(f"minute_bars date coverage: {db_lo} .. {db_hi}", flush=True)
    windows = [("features_2026.csv", "2026"),
               ("features_bear_2025.csv", "bear2025")]
    out = {}
    for f, label in windows:
        wdates = sorted(pd.read_csv(RESULTS / f, usecols=["date"], dtype={"date": str})
                        ["date"].unique())
        bars = any(db_lo <= d <= db_hi for d in wdates)   # do bars exist for this window?
        out[label] = run_window(f, label, bars_available=bars)
    json.dump(out, open(RESULTS / "ablation_fair_summary.json", "w"), indent=2)
    print("\n" + "=" * 60)
    print(json.dumps(out, indent=2))
    print(f"\nWrote {RESULTS/'ablation_fair_summary.json'}")


if __name__ == "__main__":
    run()
