"""Out-of-regime test: run the fixed-mode strategy over the spring-2025 selloff.

Uses data/bear_2025.duckdb (Jan-mid-May 2025). Same walk-forward machinery as
backtest.py, but points at the bear DB and reports performance split into the
pre-crash, crash (the -6% 40-day window bottoming ~2025-04-08), and recovery.
"""
import json
from pathlib import Path

import duckdb
import pandas as pd

import backtest as bt

RESULTS = bt.RESULTS
BEAR_DB = Path(__file__).resolve().parent.parent / "data" / "bear_2025.duckdb"


def run():
    bt.DB = BEAR_DB                      # repoint feature SQL helpers at the bear DB
    con = duckdb.connect(str(BEAR_DB), read_only=True)
    halal = {e["ticker"] for e in json.load(open(RESULTS / "halal_stocks.json"))}
    present = {r[0] for r in con.execute("SELECT DISTINCT ticker FROM minute_bars").fetchall()}
    halal_sql = ",".join(f"'{t}'" for t in sorted(halal & present))
    print(f"bear universe with bars: {len(present & halal)}", flush=True)

    cache = RESULTS / "features_bear_2025.csv"
    if cache.exists():
        feats = pd.read_csv(cache, dtype={"date": str})
    else:
        feats = bt.build_all_features(con, halal_sql)
        feats.to_csv(cache, index=False)
    dates = sorted(feats["date"].unique())
    print(f"bear feature dates: {len(dates)} ({dates[0]}..{dates[-1]})", flush=True)

    res = []
    models, asof = None, None
    for d in dates[bt.MIN_TRAIN_DAYS:]:
        hist = feats[feats["date"] < d]
        if models is None or d[:7] != asof:
            models = bt.train_models(hist); asof = d[:7]
            print(f"  [retrain {d}] {hist['date'].nunique()} days", flush=True)
        today = feats[feats["date"] == d]
        cands = bt.candidates_for_date(con, halal_sql, today, d)
        if cands.empty:
            continue
        ranked = bt.score(cands, models, skew=False).sort_values("score", ascending=False).head(bt.TOP_N_WATCH)
        res.extend(bt.simulate_day(con, d, ranked, "fixed"))
        print(f"  {d}: {len([r for r in res if r['date']==d])} trades", flush=True)
    con.close()

    df = pd.DataFrame(res)
    df.to_csv(RESULTS / "backtest_bear_2025.csv", index=False)

    def block(label, lo, hi):
        sub = df[(df.date >= lo) & (df.date <= hi)]
        if sub.empty:
            return f"  {label}: no trades"
        p = sub.pnl_pct
        bd = sub.groupby("date").pnl_pct.sum()
        return (f"  {label} ({lo}..{hi}): trades={len(sub)} win%={(p>0).mean()*100:.1f} "
                f"avg%={p.mean():.3f} sum%={p.sum():.1f} pos_days%={(bd>0).mean()*100:.0f} "
                f"worst_day%={bd.min():.2f}")

    print("\n==== BEAR-WINDOW RESULTS ====")
    print(block("ALL      ", "2025-01-01", "2025-12-31"))
    print(block("pre-crash", "2025-02-10", "2025-03-20"))
    print(block("CRASH    ", "2025-03-21", "2025-04-08"))
    print(block("recovery ", "2025-04-09", "2025-05-15"))
    s = {"trades": int(len(df)), "win_pct": round((df.pnl_pct > 0).mean()*100, 1),
         "avg_trade_pct": round(df.pnl_pct.mean(), 3), "sum_pct": round(df.pnl_pct.sum(), 1)}
    json.dump(s, open(RESULTS / "backtest_bear_summary.json", "w"), indent=2)
    print("\n", json.dumps(s))


if __name__ == "__main__":
    run()
