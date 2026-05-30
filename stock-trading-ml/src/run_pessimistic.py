"""Run ONLY the pessimistic-fill execution mode on 2026 (reuses cached features)."""
import json
import pandas as pd
import duckdb
import backtest as bt

RESULTS, DB = bt.RESULTS, bt.DB


def run():
    con = duckdb.connect(str(DB), read_only=True)
    halal = {e["ticker"] for e in json.load(open(RESULTS / "halal_stocks.json"))}
    present = {r[0] for r in con.execute("SELECT DISTINCT ticker FROM minute_bars").fetchall()}
    halal_sql = ",".join(f"'{t}'" for t in sorted(halal & present))
    feats = pd.read_csv(RESULTS / "features_2026.csv", dtype={"date": str})
    dates = sorted(feats["date"].unique())
    res = []
    models, asof = None, None
    for d in dates[bt.MIN_TRAIN_DAYS:]:
        hist = feats[feats["date"] < d]
        if models is None or d[:7] != asof:
            models = bt.train_models(hist); asof = d[:7]
        today = feats[feats["date"] == d]
        cands = bt.candidates_for_date(con, halal_sql, today, d)
        if cands.empty:
            continue
        ranked = bt.score(cands, models, skew=False).sort_values("score", ascending=False).head(bt.TOP_N_WATCH)
        res.extend(bt.simulate_day(con, d, ranked, "pessimistic"))
    con.close()
    df = pd.DataFrame(res)
    df.to_csv(RESULTS / "backtest_pessimistic.csv", index=False)
    p = df["pnl_pct"]
    s = dict(trades=int(len(df)), win_pct=round((p > 0).mean() * 100, 1),
             avg_trade_pct=round(p.mean(), 3), median_pct=round(p.median(), 3),
             sum_pct=round(p.sum(), 1), worst_day_pct=round(df.groupby("date").pnl_pct.sum().min(), 2),
             pos_days_pct=round((df.groupby("date").pnl_pct.sum() > 0).mean() * 100, 0))
    json.dump(s, open(RESULTS / "backtest_pessimistic_summary.json", "w"), indent=2)
    print(json.dumps(s, indent=2))


if __name__ == "__main__":
    run()
