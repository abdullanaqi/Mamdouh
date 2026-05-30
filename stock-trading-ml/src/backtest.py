"""Walk-forward backtest of the premarket/open ML strategy on 2026 flat-file data.

Two execution modes replayed on identical signals:
  - "asis"  : reproduces the deployed bugs — entry booked at the 09:30 opening
              tick, exits booked at the exact TP/SL level the instant a bar
              crosses it (TP checked first = optimistic), re-entries reuse the
              stale opening price, no slippage. Also mimics the live feature
              skew (premarket features zeroed at scoring time).
  - "fixed" : entry at the confirmed fill (~09:32 open) with slippage, exits at
              the first real bar touch (SL-first within a bar = conservative)
              with slippage, fresh re-entry pricing, EOD close at 15:55.

Data source: data/market_data.duckdb (minute_bars, day_bars) from flatfiles.py.
News features are 0 (no REST key) — documented; the model is near-noise either way.
"""
import json
import warnings
from pathlib import Path
from zoneinfo import ZoneInfo
from datetime import datetime, timedelta

import duckdb
import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingRegressor, RandomForestClassifier

warnings.filterwarnings("ignore")

BASE = Path(__file__).resolve().parent.parent
DB = BASE / "data" / "market_data.duckdb"
RESULTS = BASE / "results"
_ET = ZoneInfo("America/New_York")

# ---- strategy constants (from system_asis.py) ----
TOP_N_WATCH = 20
MAX_POSITIONS = 2
MIN_PRICE = 5.0
MIN_TRAIN_DAYS = 25          # warm-up before first test day
POST_OPEN_MINS = 30
OPEN_H, OPEN_M = 9, 30
CLOSE_H, CLOSE_M = 15, 55
SLIPPAGE = 0.001            # 10 bps per side, fixed mode only

FEATURES = [
    "pm_pct", "pm_momentum", "pm_vol_surge",
    "news_sentiment", "news_count", "has_earnings", "has_fda",
    "open5_range_pct", "open5_vwap", "open5_volume", "open5_pct",
]
# premarket features zeroed in the deployed live path (train/serve skew)
SKEWED = ["pm_pct", "pm_momentum", "pm_vol_surge"]


def utc_ns(date_str: str, h: int, m: int = 0) -> int:
    """DST-correct ET wall-clock -> epoch nanoseconds (fixes the ET_OFFSET_H=4 bug).

    Minutes may exceed 59 (e.g. 9:60 == 10:00); handled via timedelta from midnight ET.
    """
    midnight = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=_ET)
    dt = midnight + timedelta(hours=h, minutes=m)
    return int(dt.timestamp() * 1_000_000_000)


# ════════════════════════════════ FEATURES ════════════════════════════════

def trading_dates(con) -> list[str]:
    rows = con.execute("SELECT DISTINCT date FROM day_bars ORDER BY date").fetchall()
    return [r[0] for r in rows]


def build_features_for_date(con, halal_list_sql: str, date_str: str) -> pd.DataFrame:
    s = utc_ns(date_str, 4, 0)               # premarket start 04:00 ET
    e = utc_ns(date_str, OPEN_H, OPEN_M)     # market open
    mid = s + 15 * 60 * 1_000_000_000
    df = con.execute(f"""
        SELECT ticker, window_start AS ts, open, close, high, low, volume
        FROM minute_bars
        WHERE window_start >= {s} AND window_start < {e} AND ticker IN ({halal_list_sql})
        ORDER BY window_start""").fetchdf()
    if df.empty:
        return pd.DataFrame()
    rows = []
    for ticker, g in df.groupby("ticker"):
        g = g.sort_values("ts")
        pm_open = g["open"].iloc[0]
        if pm_open == 0:
            continue
        pm_close = g["close"].iloc[-1]
        pm_vol = g["volume"].sum()
        first, last = g[g["ts"] < mid], g[g["ts"] >= mid]
        f_ret = ((first["close"].iloc[-1] - first["open"].iloc[0]) / first["open"].iloc[0] * 100
                 if not first.empty and first["open"].iloc[0] else 0.0)
        l_ret = ((last["close"].iloc[-1] - last["open"].iloc[0]) / last["open"].iloc[0] * 100
                 if not last.empty and last["open"].iloc[0] else 0.0)
        rows.append({"ticker": ticker, "pm_volume": pm_vol,
                     "pm_pct": (pm_close - pm_open) / pm_open * 100,
                     "pm_momentum": l_ret - f_ret})
    pm = pd.DataFrame(rows)
    if pm.empty:
        return pm

    # volume surge vs prior up-to-5 premarket sessions
    pm = _vol_surge(con, pm, date_str)
    # first-5-min features + post-open label
    pm = _open5_and_label(con, pm, date_str)
    # news features zeroed (no REST key)
    pm["news_sentiment"] = 0.0
    pm["news_count"] = 0
    pm["has_earnings"] = 0
    pm["has_fda"] = 0
    pm.insert(0, "date", date_str)
    return pm


def _vol_surge(con, df, date_str):
    tickers_sql = ",".join(f"'{t}'" for t in df["ticker"])
    d0 = datetime.strptime(date_str, "%Y-%m-%d")
    hist = {t: [] for t in df["ticker"]}
    found = 0
    from datetime import timedelta
    for back in range(1, 15):
        past = (d0 - timedelta(days=back)).strftime("%Y-%m-%d")
        s, e = utc_ns(past, 4, 0), utc_ns(past, OPEN_H, OPEN_M)
        rows = con.execute(f"""SELECT ticker, SUM(volume) FROM minute_bars
            WHERE window_start>={s} AND window_start<{e} AND ticker IN ({tickers_sql})
            GROUP BY ticker""").fetchall()
        if rows:
            found += 1
            for t, v in rows:
                if t in hist:
                    hist[t].append(v)
            if found >= 5:
                break
    avg = {t: sum(v) / len(v) for t, v in hist.items() if v}
    df["pm_vol_surge"] = df.apply(
        lambda r: r["pm_volume"] / avg[r["ticker"]] if avg.get(r["ticker"], 0) > 0 else 1.0, axis=1)
    return df


def _open5_and_label(con, df, date_str):
    tickers_sql = ",".join(f"'{t}'" for t in df["ticker"])
    s5, e5 = utc_ns(date_str, OPEN_H, OPEN_M), utc_ns(date_str, OPEN_H, OPEN_M) + 5 * 60_000_000_000
    o5 = con.execute(f"""
        SELECT ticker, FIRST(open ORDER BY window_start) o, LAST(close ORDER BY window_start) c,
               MAX(high) h, MIN(low) l, SUM(volume) v,
               SUM(close*volume)/NULLIF(SUM(volume),0) vw
        FROM minute_bars WHERE window_start>={s5} AND window_start<{e5} AND ticker IN ({tickers_sql})
        GROUP BY ticker""").fetchdf()
    if not o5.empty:
        ref = o5["o"].replace(0, np.nan)
        o5["open5_pct"] = (o5["c"] - o5["o"]) / ref * 100
        o5["open5_range_pct"] = (o5["h"] - o5["l"]) / ref * 100
        o5["open5_vwap"] = (o5["vw"] - o5["o"]) / ref * 100
        o5["open5_volume"] = o5["v"]
        df = df.merge(o5[["ticker", "open5_pct", "open5_range_pct", "open5_vwap", "open5_volume"]],
                      on="ticker", how="left")
    for c in ("open5_pct", "open5_range_pct", "open5_vwap", "open5_volume"):
        if c not in df:
            df[c] = 0.0
        df[c] = df[c].fillna(0.0)

    # label: price 30min after open > 09:30 open  (post-open 30-min direction)
    o_s, o_e = utc_ns(date_str, OPEN_H, OPEN_M), utc_ns(date_str, OPEN_H, OPEN_M) + 60_000_000_000
    p_e = utc_ns(date_str, OPEN_H, OPEN_M + POST_OPEN_MINS)
    opens = con.execute(f"""SELECT ticker, open op FROM minute_bars
        WHERE window_start>={o_s} AND window_start<{o_e} AND ticker IN ({tickers_sql})""").fetchdf()
    closes = con.execute(f"""SELECT ticker, LAST(close ORDER BY window_start) pc FROM minute_bars
        WHERE window_start>={o_e} AND window_start<{p_e} AND ticker IN ({tickers_sql})
        GROUP BY ticker""").fetchdf()
    lab = opens.merge(closes, on="ticker", how="inner")
    lab["label"] = (lab["pc"] > lab["op"]).astype(int)
    lab["target_gain"] = (lab["pc"] - lab["op"]) / lab["op"] * 100
    df = df.merge(lab[["ticker", "op", "pc", "label", "target_gain"]], on="ticker", how="left")
    df = df.rename(columns={"op": "open_price", "pc": "post_open_close"})

    # drawdown target: intraday low vs open
    i_e = utc_ns(date_str, CLOSE_H, CLOSE_M + 1)
    dd = con.execute(f"""SELECT ticker, MIN(low) lo, FIRST(open ORDER BY window_start) io
        FROM minute_bars WHERE window_start>={o_s} AND window_start<{i_e} AND ticker IN ({tickers_sql})
        GROUP BY ticker""").fetchdf()
    dd["target_dd"] = (dd["lo"] - dd["io"]) / dd["io"].replace(0, np.nan) * 100
    df = df.merge(dd[["ticker", "target_dd"]], on="ticker", how="left")
    return df


def build_all_features(con, halal_list_sql) -> pd.DataFrame:
    dates = trading_dates(con)
    out = []
    for i, d in enumerate(dates, 1):
        f = build_features_for_date(con, halal_list_sql, d)
        if not f.empty:
            out.append(f)
        print(f"  features {i}/{len(dates)} {d}: {len(f)} rows", flush=True)
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame()


# ════════════════════════════════ MODEL ════════════════════════════════

def train_models(train_df: pd.DataFrame) -> dict:
    df = train_df.copy()
    for c in FEATURES:
        if c not in df:
            df[c] = 0.0
    # clip targets
    for col in ("target_gain", "target_dd"):
        v = df[col].dropna()
        if len(v) > 100:
            df[col] = df[col].clip(v.quantile(0.02), v.quantile(0.98))
    clf = RandomForestClassifier(n_estimators=300, max_depth=7, min_samples_leaf=8,
                                 max_features="sqrt", class_weight="balanced",
                                 random_state=42, n_jobs=-1)
    m = df[df["label"].notna()]
    clf.fit(m[FEATURES].fillna(0), m["label"])
    models = {"clf": clf}
    for tgt, name in (("target_gain", "reg_gain"), ("target_dd", "reg_dd")):
        sub = df[df[tgt].notna()]
        if len(sub) >= 200:
            reg = GradientBoostingRegressor(n_estimators=200, max_depth=4, learning_rate=0.05,
                                            subsample=0.8, min_samples_leaf=15, max_features=0.8,
                                            random_state=42)
            reg.fit(sub[FEATURES].fillna(0), sub[tgt])
            models[name] = reg
        else:
            models[name] = None
    return models


def score(df: pd.DataFrame, models: dict, skew: bool) -> pd.DataFrame:
    df = df.copy()
    for c in FEATURES:
        if c not in df:
            df[c] = 0.0
    X = df[FEATURES].fillna(0).copy()
    if skew:                                   # mimic live: premarket feats zeroed
        for c in SKEWED:
            X[c] = 0.0
    df["up_prob"] = models["clf"].predict_proba(X)[:, 1]
    df["pred_gain_pct"] = models["reg_gain"].predict(X) if models.get("reg_gain") else np.nan
    df["pred_dd_pct"] = models["reg_dd"].predict(X) if models.get("reg_dd") else np.nan
    df["score"] = df["pred_gain_pct"].fillna(0) * df["up_prob"]
    return df


# ════════════════════════════════ TP/SL ════════════════════════════════

def calc_tp_sl(entry, pred_gain, pred_dd, pm_pct, up_prob):
    raw_tp = (pred_gain if not np.isnan(pred_gain) else pm_pct * 1.5) * (0.7 + 0.3 * up_prob)
    tp_pct = float(raw_tp)
    raw_sl = pred_dd * 1.1 if (not np.isnan(pred_dd) and pred_dd < 0) else (
        -abs(pm_pct) * 0.5 if pm_pct != 0 else -1.0)
    sl_pct = float(raw_sl)
    min_rr = 1.5 if up_prob < 0.6 else 1.0
    tp_pct = float(max(tp_pct, abs(sl_pct) * min_rr))
    return tp_pct, sl_pct


# ════════════════════════════════ EXECUTION REPLAY ════════════════════════════════

def day_bars(con, ticker, date_str):
    s, e = utc_ns(date_str, OPEN_H, OPEN_M), utc_ns(date_str, CLOSE_H, CLOSE_M + 1)
    return con.execute(f"""SELECT window_start ts, open, high, low, close FROM minute_bars
        WHERE ticker='{ticker}' AND window_start>={s} AND window_start<{e}
        ORDER BY window_start""").fetchdf()


def simulate_day(con, date_str, ranked: pd.DataFrame, mode: str) -> list[dict]:
    """Replay one day; ranked is sorted best-first with model preds + pm_pct."""
    open_ns = utc_ns(date_str, OPEN_H, OPEN_M)
    bar1_ns = open_ns + 60_000_000_000          # 09:31
    bar2_ns = open_ns + 120_000_000_000         # 09:32 (entry after 2-bar confirm)
    asis = (mode == "asis")
    pessimistic = (mode == "pessimistic")       # worst-case: bar touching both TP&SL -> SL
    bars_cache = {}

    def bars(t):
        if t not in bars_cache:
            bars_cache[t] = day_bars(con, t, date_str)
        return bars_cache[t]

    def confirm_and_entry(t):
        b = bars(t)
        if len(b) < 3:
            return None
        o930 = b.iloc[0]
        c931 = b[b["ts"] == bar1_ns]
        if c931.empty or c931["close"].iloc[0] <= o930["open"]:
            return None                          # not confirmed (close931 <= open930)
        entry_bar = b[b["ts"] == bar2_ns]
        if entry_bar.empty:
            return None
        if asis:
            entry = float(o930["open"])          # BUG: stale opening tick
        elif pessimistic:
            # chase: enter at the 09:32 bar HIGH (worst intrabar buy) + slippage
            entry = float(entry_bar["high"].iloc[0]) * (1 + SLIPPAGE)
        else:
            entry = float(entry_bar["open"].iloc[0]) * (1 + SLIPPAGE)
        return entry, bar2_ns

    def reentry_price(t, at_ns):
        b = bars(t)
        seg = b[b["ts"] >= at_ns]
        if seg.empty:
            return None
        if asis:                                 # BUG: reuse stale opening tick
            return float(b.iloc[0]["open"]), at_ns
        return float(seg.iloc[0]["open"]) * (1 + SLIPPAGE), at_ns

    def exit_scan(t, entry, tp_pct, sl_pct, from_ns):
        b = bars(t)
        tp = entry * (1 + tp_pct / 100)
        sl = entry * (1 + sl_pct / 100)
        seg = b[b["ts"] >= from_ns]
        for _, r in seg.iterrows():
            hi, lo = r["high"], r["low"]
            if asis:
                # optimistic: TP checked first, booked at level, no slippage
                if hi >= tp:
                    return tp, "TP", r["ts"]
                if lo <= sl:
                    return sl, "SL", r["ts"]
            elif pessimistic:
                # worst case: any bar touching SL fills at the bar LOW (slip past stop);
                # SL always checked first even if the same bar also tags TP.
                if lo <= sl:
                    return float(lo) * (1 - SLIPPAGE), "SL", r["ts"]
                if hi >= tp:
                    return tp * (1 - SLIPPAGE), "TP", r["ts"]
            else:
                # conservative: SL-first within a bar, slippage against us
                if lo <= sl:
                    return sl * (1 - SLIPPAGE), "SL", r["ts"]
                if hi >= tp:
                    return tp * (1 - SLIPPAGE), "TP", r["ts"]
        # EOD close
        if not seg.empty:
            return float(seg.iloc[-1]["close"]) * (1 - (SLIPPAGE if not asis else 0)), "EOD", seg.iloc[-1]["ts"]
        return None

    results, open_pos, traded = [], {}, set()
    queue = ranked.to_dict("records")
    qi = 0

    # fill initial slots
    def try_fill(at_ns=None):
        nonlocal qi
        while len(open_pos) < MAX_POSITIONS and qi < len(queue):
            row = queue[qi]; qi += 1
            t = row["ticker"]
            if t in traded:
                continue
            if at_ns is None:
                ce = confirm_and_entry(t)
            else:
                # re-entry: must still confirm at open; price fresh/stale by mode
                ce0 = confirm_and_entry(t)
                if ce0 is None:
                    traded.add(t); continue
                rp = reentry_price(t, at_ns)
                ce = (rp[0], at_ns) if rp else None
            if ce is None:
                traded.add(t); continue
            entry, ent_ns = ce
            tp_pct, sl_pct = calc_tp_sl(
                entry, float(row.get("pred_gain_pct", np.nan)),
                float(row.get("pred_dd_pct", np.nan)), float(row.get("pm_pct", 0.0)),
                float(row["up_prob"]))
            open_pos[t] = dict(ticker=t, entry=entry, ent_ns=ent_ns,
                               tp_pct=tp_pct, sl_pct=sl_pct)
            traded.add(t)

    try_fill(None)
    # resolve exits chronologically, refilling freed slots at the exit time
    guard = 0
    while open_pos and guard < 200:
        guard += 1
        # find earliest exit among open positions
        exits = {}
        for t, p in open_pos.items():
            ex = exit_scan(t, p["entry"], p["tp_pct"], p["sl_pct"], p["ent_ns"])
            if ex:
                exits[t] = ex
        if not exits:
            break
        t = min(exits, key=lambda k: exits[k][2])
        price, reason, ex_ns = exits[t]
        p = open_pos.pop(t)
        pnl = (price - p["entry"]) / p["entry"] * 100
        results.append(dict(date=date_str, ticker=t, entry=round(p["entry"], 4),
                            exit=round(price, 4), reason=reason, pnl_pct=round(pnl, 3),
                            ent_ns=p["ent_ns"], ex_ns=ex_ns))
        try_fill(ex_ns)        # refill freed slot at exit time
    return results


# ════════════════════════════════ DRIVER ════════════════════════════════

def candidates_for_date(con, halal_sql, feats_today: pd.DataFrame, date_str: str) -> pd.DataFrame:
    """Scanner: rank halal day_bars by pct*log(volume), price>=MIN_PRICE, top 2*TOP_N."""
    db = con.execute(f"""SELECT ticker, open, close, volume FROM day_bars
        WHERE date='{date_str}' AND ticker IN ({halal_sql}) AND close>={MIN_PRICE} AND open>0
        """).fetchdf()
    if db.empty or feats_today.empty:
        return pd.DataFrame()
    db["pct"] = (db["close"] - db["open"]) / db["open"] * 100
    db["scan"] = db["pct"] * np.log1p(db["volume"])
    keep = set(db.sort_values("scan", ascending=False).head(TOP_N_WATCH * 2)["ticker"])
    return feats_today[feats_today["ticker"].isin(keep)].copy()


def run(test_start: str | None = None):
    con = duckdb.connect(str(DB), read_only=True)
    halal = {e["ticker"] for e in json.load(open(RESULTS / "halal_stocks.json"))}
    present = {r[0] for r in con.execute("SELECT DISTINCT ticker FROM minute_bars").fetchall()}
    halal = sorted(halal & present)          # only names that actually trade
    halal_sql = ",".join(f"'{t}'" for t in halal)
    print(f"Universe with bars: {len(halal)}", flush=True)

    cache = RESULTS / "features_2026.csv"
    if cache.exists():
        print(f"Loading cached features <- {cache.name}", flush=True)
        feats = pd.read_csv(cache, dtype={"date": str})
    else:
        print("Building features for all loaded dates ...", flush=True)
        feats = build_all_features(con, halal_sql)
        feats.to_csv(cache, index=False)
    dates = sorted(feats["date"].unique())
    print(f"Feature dates: {len(dates)} ({dates[0]}..{dates[-1]})", flush=True)

    # train once on first MIN_TRAIN_DAYS, test on the rest (expanding retrain monthly)
    test_dates = dates[MIN_TRAIN_DAYS:]
    if test_start:
        test_dates = [d for d in test_dates if d >= test_start]

    all_res = {"asis": [], "fixed": []}
    models, model_asof = None, None
    for d in test_dates:
        hist = feats[feats["date"] < d]
        # retrain at the start of each month (cheap + faithful enough)
        if models is None or d[:7] != model_asof:
            models = train_models(hist)
            model_asof = d[:7]
            print(f"  [retrain @ {d}] on {hist['date'].nunique()} days, {len(hist)} rows", flush=True)
        today = feats[feats["date"] == d]
        cands = candidates_for_date(con, halal_sql, today, d)
        if cands.empty:
            continue
        for mode in ("asis", "fixed"):
            sc = score(cands, models, skew=(mode == "asis"))
            ranked = sc.sort_values("score", ascending=False).head(TOP_N_WATCH)
            res = simulate_day(con, d, ranked, mode)
            all_res[mode].extend(res)
        print(f"  {d}: asis {len([r for r in all_res['asis'] if r['date']==d])} trades | "
              f"fixed {len([r for r in all_res['fixed'] if r['date']==d])} trades", flush=True)

    con.close()
    summary = {}
    for mode in ("asis", "fixed"):
        rdf = pd.DataFrame(all_res[mode])
        rdf.to_csv(RESULTS / f"backtest_{mode}.csv", index=False)
        if rdf.empty:
            summary[mode] = {"trades": 0}
            continue
        wins = (rdf["pnl_pct"] > 0).sum()
        summary[mode] = dict(
            trades=len(rdf), win_rate=round(wins / len(rdf) * 100, 1),
            total_pnl_pct=round(rdf["pnl_pct"].sum(), 2),
            avg_pnl_pct=round(rdf["pnl_pct"].mean(), 3),
            tp=int((rdf["reason"] == "TP").sum()), sl=int((rdf["reason"] == "SL").sum()),
            eod=int((rdf["reason"] == "EOD").sum()),
            days=rdf["date"].nunique())
    json.dump(summary, open(RESULTS / "backtest_summary.json", "w"), indent=2)
    print("\n==== SUMMARY ====")
    print(json.dumps(summary, indent=2))
    return summary


if __name__ == "__main__":
    import sys
    run(test_start=sys.argv[1] if len(sys.argv) > 1 else None)
