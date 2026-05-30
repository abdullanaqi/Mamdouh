"""Full ~2-year walk-forward (2025-01 .. 2026-05) under the FINAL rule:
at most MAX_POSITIONS entries/day, event-driven confirmation, NO slot-refilling.

Disk-aware: only ~22GB free but ~24GB of minute bars for the span, so this
streams month-by-month. For each month it (1) loads that month's minute+day bars
into a small work DB -- reusing data already on disk where possible, downloading
the gap months (2025-05..2025-12) from the Massive S3 flat files -- (2) builds
features, (3) runs the no-refill fills sim for all three selection methods
(model / scanner / random) plus the model under pessimistic fills, then (4) drops
old minute bars so at most ~2 months are resident at a time.

Outputs (results/):
  features_2yr.csv
  backtest_2yr_{fixed,pessimistic}.csv         (model, no-refill)
  ablation_2yr_norefill_{model,scanner,random}.csv
  backtest_2yr_summary.json                    (Test A selection-only + Test B fills,
                                                monthly P&L, paired stats)
"""
import gzip
import io
import json
import os
from datetime import date, timedelta
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
from dotenv import load_dotenv
from scipy import stats

import backtest as bt
import ablation as ab          # csv_candidate_pool, paired_stats, N_GRID
import flatfiles as ff         # _s3, _fetch_csv, MIN_PREFIX, DAY_PREFIX

BASE = bt.BASE
RESULTS = bt.RESULTS
load_dotenv(BASE / ".env")

WORK_DB = BASE / "data" / "work_2yr.duckdb"
BEAR_DB = BASE / "data" / "bear_2025.duckdb"
MKT_DB = BASE / "data" / "market_data.duckdb"

# month -> source: reuse existing DBs for full-coverage months, download the rest
BEAR_MONTHS = {(2025, m) for m in (1, 2, 3, 4)}        # bear DB: 2025-01..05-15 (May partial)
MKT_MONTHS = {(2026, m) for m in (1, 2, 3, 4, 5)}      # market DB: 2026-01..05-29

FEATS_CSV = RESULTS / "features_2yr.csv"


def span_months() -> list[tuple[int, int]]:
    out = []
    y, m = 2025, 1
    while (y, m) <= (2026, 5):
        out.append((y, m))
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return out


def _first(y, m):                       # first calendar day of month m
    return date(y, m, 1)


def _next_first(y, m):
    return date(y + 1, 1, 1) if m == 12 else date(y, m + 1, 1)


def month_ns(y, m):
    s = bt.utc_ns(_first(y, m).isoformat(), 0, 0)
    e = bt.utc_ns(_next_first(y, m).isoformat(), 0, 0)
    return s, e


def ensure_tables(con):
    con.execute("""CREATE TABLE IF NOT EXISTS minute_bars(
        ticker VARCHAR, window_start BIGINT, open DOUBLE, close DOUBLE,
        high DOUBLE, low DOUBLE, volume DOUBLE)""")
    con.execute("""CREATE TABLE IF NOT EXISTS day_bars(
        date VARCHAR, ticker VARCHAR, open DOUBLE, close DOUBLE,
        high DOUBLE, low DOUBLE, volume DOUBLE)""")


def reuse_month(con, src_db, y, m):
    s, e = month_ns(y, m)
    lo, hi = _first(y, m).isoformat(), _next_first(y, m).isoformat()
    con.execute(f"ATTACH '{src_db}' AS s (READ_ONLY)")
    con.execute(f"""INSERT INTO minute_bars
        SELECT ticker,window_start,open,close,high,low,volume FROM s.minute_bars
        WHERE window_start>={s} AND window_start<{e}""")
    con.execute(f"""INSERT INTO day_bars
        SELECT date,ticker,open,close,high,low,volume FROM s.day_bars
        WHERE date>='{lo}' AND date<'{hi}'""")
    con.execute("DETACH s")
    n = con.execute(f"SELECT count(*) FROM minute_bars WHERE window_start>={s} AND window_start<{e}").fetchone()[0]
    print(f"    reused {src_db.name}: {n:,} min rows", flush=True)


def download_month(con, halal, y, m):
    s3 = ff._s3()
    d, days = _first(y, m), []
    nxt = _next_first(y, m)
    while d < nxt:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    nmin = 0
    for d in days:
        mdf = ff._fetch_csv(s3, ff.MIN_PREFIX, d)
        if mdf is None:
            continue
        mdf = mdf[mdf["ticker"].isin(halal)]
        mb = mdf[["ticker", "window_start", "open", "close", "high", "low", "volume"]]
        con.execute("INSERT INTO minute_bars SELECT * FROM mb")
        nmin += len(mb)
        ddf = ff._fetch_csv(s3, ff.DAY_PREFIX, d)
        if ddf is not None and not ddf.empty:
            ddf = ddf[ddf["ticker"].isin(halal)].copy()
            ddf["date"] = d.isoformat()
            db = ddf[["date", "ticker", "open", "close", "high", "low", "volume"]]
            con.execute("INSERT INTO day_bars SELECT * FROM db")
    print(f"    downloaded S3 {y}-{m:02d}: {nmin:,} min rows ({len(days)} days)", flush=True)


def run():
    if WORK_DB.exists():
        WORK_DB.unlink()
    if FEATS_CSV.exists():
        FEATS_CSV.unlink()                       # rebuilt fresh; we append per month
    con = duckdb.connect(str(WORK_DB))
    ensure_tables(con)

    halal = sorted({e["ticker"] for e in json.load(open(RESULTS / "halal_stocks.json"))})
    halal_set = set(halal)
    halal_sql = ",".join(f"'{t}'" for t in halal)
    methods = ("model", "scanner", "random")
    rng = np.random.default_rng(42)

    feats_all = pd.DataFrame()
    B_trades = {m: [] for m in methods}          # no-refill fixed fills, per method
    B_daypnl = {m: {} for m in methods}
    pess_trades = []                             # model under pessimistic fills
    models, asof = None, None
    global_day = 0                               # for warmup gate

    for (y, m) in span_months():
        ym = f"{y}-{m:02d}"
        print(f"\n=== month {ym} ===", flush=True)
        if (y, m) in BEAR_MONTHS:
            reuse_month(con, BEAR_DB, y, m)
        elif (y, m) in MKT_MONTHS:
            reuse_month(con, MKT_DB, y, m)
        else:
            download_month(con, halal_set, y, m)

        # ---- build features for this month's trading dates ----
        s, e = month_ns(y, m)
        mdates = [r[0] for r in con.execute(
            f"""SELECT DISTINCT date FROM day_bars WHERE date>='{_first(y,m).isoformat()}'
                AND date<'{_next_first(y,m).isoformat()}' ORDER BY date""").fetchall()]
        month_feats = []
        for d in mdates:
            f = bt.build_features_for_date(con, halal_sql, d)
            if not f.empty:
                month_feats.append(f)
        if month_feats:
            mf = pd.concat(month_feats, ignore_index=True)
            feats_all = pd.concat([feats_all, mf], ignore_index=True)
            hdr = not FEATS_CSV.exists()
            mf.to_csv(FEATS_CSV, mode="a", header=hdr, index=False)
            print(f"    features: +{len(mf)} rows ({len(mdates)} days)", flush=True)

        # ---- fills for this month's test dates (after global warmup) ----
        for d in mdates:
            global_day += 1
            if global_day <= bt.MIN_TRAIN_DAYS:
                continue
            hist = feats_all[feats_all["date"] < d]
            if models is None or d[:7] != asof:
                models = bt.train_models(hist)
                asof = d[:7]
            today = feats_all[feats_all["date"] == d]
            cands = bt.candidates_for_date(con, halal_sql, today, d)
            if cands.empty:
                continue
            sc = bt.score(cands, models, skew=False)
            order = bt.premarket_scan_scores(con, halal_sql, d)
            sc["scan"] = sc["ticker"].map(order).fillna(-1e9)
            ranked = {
                "model":   sc.sort_values("score", ascending=False).head(bt.TOP_N_WATCH),
                "scanner": sc.sort_values("scan", ascending=False).head(bt.TOP_N_WATCH),
                "random":  sc.sample(min(bt.TOP_N_WATCH, len(sc)),
                                     random_state=int(rng.integers(1e9))),
            }
            for mth in methods:
                res = bt.simulate_day(con, d, ranked[mth], "fixed", no_refill=True)
                B_trades[mth].extend(res)
                B_daypnl[mth][d] = sum(r["pnl_pct"] for r in res)
            pess_trades.extend(
                bt.simulate_day(con, d, ranked["model"], "pessimistic", no_refill=True))

        # ---- drop minute bars older than this month (keep ~2 months resident) ----
        con.execute(f"DELETE FROM minute_bars WHERE window_start < {s}")
        con.execute("CHECKPOINT")

    # ---------- Test A: selection-only over the FULL 2-year features ----------
    A = {mth: {n: [] for n in ab.N_GRID} for mth in methods}
    pool_mean = []
    mdl2, asofA = None, None
    fdates = sorted(feats_all["date"].unique())
    for i, d in enumerate(fdates):
        if i < bt.MIN_TRAIN_DAYS:
            continue
        hist = feats_all[feats_all["date"] < d]
        if mdl2 is None or d[:7] != asofA:
            mdl2 = bt.train_models(hist)
            asofA = d[:7]
        pool = ab.csv_candidate_pool(feats_all[feats_all["date"] == d])
        if pool.empty:
            continue
        scored = bt.score(pool, mdl2, skew=False)
        pool_mean.append(float(scored["target_gain"].mean()))
        rk = {"model": scored.sort_values("score", ascending=False),
              "scanner": scored.sort_values("pm_scan", ascending=False),
              "random": scored.sample(frac=1.0, random_state=int(rng.integers(1e9)))}
        for mth in methods:
            tg = rk[mth]["target_gain"].to_numpy()
            for n in ab.N_GRID:
                t = tg[:n][~np.isnan(tg[:n])]
                if len(t):
                    A[mth][n].append(float(t.mean()))
    con.close()

    # ---------- assemble summary ----------
    def fills_summary(trades):
        df = pd.DataFrame(trades)
        if df.empty:
            return dict(trades=0), df
        p = df["pnl_pct"]
        return dict(trades=int(len(df)), days=int(df["date"].nunique()),
                    trades_per_day=round(len(df) / df["date"].nunique(), 2),
                    win_pct=round((p > 0).mean() * 100, 1),
                    avg_trade_pct=round(float(p.mean()), 3),
                    total_pnl_pct=round(float(p.sum()), 1),
                    tp=int((df["reason"] == "TP").sum()), sl=int((df["reason"] == "SL").sum()),
                    eod=int((df["reason"] == "EOD").sum())), df

    testB = {}
    dfs = {}
    for mth in methods:
        testB[mth], dfs[mth] = fills_summary(B_trades[mth])
        dfs[mth].to_csv(RESULTS / f"ablation_2yr_norefill_{mth}.csv", index=False)
    dfs["model"].to_csv(RESULTS / "backtest_2yr_fixed.csv", index=False)
    pess_sum, pess_df = fills_summary(pess_trades)
    pess_df.to_csv(RESULTS / "backtest_2yr_pessimistic.csv", index=False)
    testB["model_pessimistic"] = pess_sum

    days = sorted(set().union(*[set(B_daypnl[mth]) for mth in methods]))
    dp = {mth: np.array([B_daypnl[mth].get(d, 0.0) for d in days]) for mth in methods}
    testB["model_minus_random_dayPnL"] = ab.paired_stats(dp["model"] - dp["random"], rng)
    testB["model_minus_scanner_dayPnL"] = ab.paired_stats(dp["model"] - dp["scanner"], rng)

    # monthly model P&L (fixed)
    mdf = dfs["model"].copy()
    monthly = {}
    if not mdf.empty:
        mdf["month"] = mdf["date"].str.slice(0, 7)
        for mo, g in mdf.groupby("month"):
            monthly[mo] = dict(trades=int(len(g)), win_pct=round((g["pnl_pct"] > 0).mean() * 100, 1),
                               total_pnl_pct=round(float(g["pnl_pct"].sum()), 1),
                               avg_trade_pct=round(float(g["pnl_pct"].mean()), 3))

    testA = {"pool_mean_target_gain": round(float(np.mean(pool_mean)), 4) if pool_mean else None}
    for n in ab.N_GRID:
        idx = min(len(A[mth][n]) for mth in methods)
        mr = np.array(A["model"][n][:idx]) - np.array(A["random"][n][:idx])
        ms = np.array(A["model"][n][:idx]) - np.array(A["scanner"][n][:idx])
        testA[f"N={n}"] = dict(
            mean_pick_gain={mth: round(float(np.mean(A[mth][n])), 4) for mth in methods},
            days=idx, model_minus_random=ab.paired_stats(mr, rng),
            model_minus_scanner=ab.paired_stats(ms, rng))

    summary = {"span": f"{fdates[0]}..{fdates[-1]}", "feature_days": len(fdates),
               "testA_selection_only": testA, "testB_norefill_fills": testB,
               "model_monthly_fixed": monthly}
    json.dump(summary, open(RESULTS / "backtest_2yr_summary.json", "w"), indent=2)
    print("\n" + "=" * 60)
    print(json.dumps(summary, indent=2))
    print(f"\nWrote {RESULTS/'backtest_2yr_summary.json'}")


if __name__ == "__main__":
    run()
