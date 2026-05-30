"""Does the ML add value? Compare watchlist selection methods on identical days,
all executed through the SAME fixed-mode execution simulator.

  - model   : rank by model score (up_prob * pred_gain + intraday terms)  [the system]
  - scanner : rank by the gap-momentum scanner only (pct * log volume), no ML
  - random  : random pick from the same candidate pool (seeded, 1 draw)

If "model" doesn't beat "scanner", the ML isn't earning its keep.
"""
import json
import numpy as np
import pandas as pd
import duckdb
from pathlib import Path

import backtest as bt

RESULTS = bt.RESULTS
DB = bt.DB


def run():
    con = duckdb.connect(str(DB), read_only=True)
    halal = {e["ticker"] for e in json.load(open(RESULTS / "halal_stocks.json"))}
    present = {r[0] for r in con.execute("SELECT DISTINCT ticker FROM minute_bars").fetchall()}
    halal_sql = ",".join(f"'{t}'" for t in sorted(halal & present))

    feats = pd.read_csv(RESULTS / "features_2026.csv", dtype={"date": str})
    dates = sorted(feats["date"].unique())
    test_dates = dates[bt.MIN_TRAIN_DAYS:]

    out = {"model": [], "scanner": [], "random": []}
    rng = np.random.default_rng(42)
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
        scored = bt.score(cands, models, skew=False)        # real features (fixed)

        # three rankings over the SAME candidate pool
        model_rank = scored.sort_values("score", ascending=False).head(bt.TOP_N_WATCH)
        # scanner: need the day pct*logvol per ticker
        db = con.execute(f"""SELECT ticker, open, close, volume FROM day_bars
            WHERE date='{d}' AND ticker IN ({halal_sql})""").fetchdf()
        db["scan"] = (db["close"] - db["open"]) / db["open"].replace(0, np.nan) * 100 * np.log1p(db["volume"])
        order = db.set_index("ticker")["scan"]
        sc = scored.copy()
        sc["scan"] = sc["ticker"].map(order).fillna(-1e9)
        scanner_rank = sc.sort_values("scan", ascending=False).head(bt.TOP_N_WATCH)
        random_rank = scored.sample(min(bt.TOP_N_WATCH, len(scored)), random_state=int(rng.integers(1e9)))

        for name, rk in (("model", model_rank), ("scanner", scanner_rank), ("random", random_rank)):
            out[name].extend(bt.simulate_day(con, d, rk, "fixed"))

    con.close()
    summary = {}
    for name, res in out.items():
        df = pd.DataFrame(res)
        df.to_csv(RESULTS / f"ablation_{name}.csv", index=False)
        p = df["pnl_pct"]
        summary[name] = dict(trades=int(len(df)), win_pct=round((p > 0).mean() * 100, 1),
                             avg_trade_pct=round(p.mean(), 3), median_pct=round(p.median(), 3),
                             sum_pct=round(p.sum(), 1))
    json.dump(summary, open(RESULTS / "ablation_summary.json", "w"), indent=2)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    run()
