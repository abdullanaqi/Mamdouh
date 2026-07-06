"""SYNTHETIC smoke-test database for `python edited.py --compare`.

Builds data/market_data.duckdb with `features`, `minute_bars` and
`market_news` in the exact schema the live pipeline writes, from
randomly generated gappers. Labels/features are derived FROM the
generated bars (same definitions as the real pipeline), so the compare
engine is exercised end-to-end.

THIS IS TEST DATA. Any P&L it produces validates the code path only —
it is not evidence of a trading edge. Point it at your real DB for that.
"""
import time
import numpy as np
import pandas as pd
import duckdb
from pathlib import Path
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

t0 = time.time()
rng = np.random.default_rng(7)
ET = ZoneInfo("America/New_York")
BASE = Path(__file__).parent
DB = BASE / "data" / "market_data.duckdb"
DB.parent.mkdir(exist_ok=True)

N_DAYS, N_TKR, NB = 150, 12, 390
start = datetime(2025, 10, 1)
dates = []
d = start
while len(dates) < N_DAYS:
    if d.weekday() < 5:
        dates.append(d.strftime("%Y-%m-%d"))
    d += timedelta(days=1)

RISKY = ["announces public offering of common stock",
         "receives SEC subpoena over disclosures",
         "reverse split effective today"]
GOOD = ["reports Q3 earnings beat, raises guidance",
        "receives FDA approval for lead drug",
        "announces major partnership"]

feat_rows, news_rows = [], []
bar_cols = {k: [] for k in ("ticker", "window_start", "open", "high", "low", "close", "volume")}


def ns0(date_str):
    return int(datetime.strptime(date_str, "%Y-%m-%d")
               .replace(hour=9, minute=30, tzinfo=ET).timestamp() * 1e9)


def gen_path(px0, drift, sigma, n=NB):
    r = rng.normal(drift, 1.0, n) * sigma
    c = px0 * np.cumprod(1 + r)
    o = np.empty(n); o[0] = px0; o[1:] = c[:-1]
    wick = np.abs(rng.normal(0, sigma * 0.8, n))
    h = np.maximum(o, c) * (1 + wick)
    l = np.minimum(o, c) * (1 - wick)
    return o, h, l, c


def add_bars(tkr, ts, o, h, l, c, v):
    n = len(ts)
    bar_cols["ticker"].extend([tkr] * n)
    bar_cols["window_start"].extend(ts.tolist())
    bar_cols["open"].extend(o.tolist());  bar_cols["high"].extend(h.tolist())
    bar_cols["low"].extend(l.tolist());   bar_cols["close"].extend(c.tolist())
    bar_cols["volume"].extend(v.tolist())


for date_str in dates:
    mkt_pm = float(rng.normal(0, 0.35))
    ts = ns0(date_str) + np.arange(NB, dtype=np.int64) * 60_000_000_000
    o, h, l, c = gen_path(500.0, mkt_pm / 39000, np.full(NB, 0.0004))
    add_bars("SPY", ts, o, h, l, c, np.full(NB, 1e6))

    for i in range(N_TKR):
        tkr = "SYN" + chr(65 + i) + "A"
        pm_pct = float(np.clip(rng.gamma(2.2, 3.0) + 2.0, 2.0, 65.0))
        pm_range = float(np.clip(pm_pct * rng.uniform(0.3, 0.9), 1.0, 40.0))
        surge = float(np.clip(rng.gamma(2.0, 4.0), 0.5, 60.0))
        prev_close = float(rng.uniform(4, 60))
        pm_close = prev_close * (1 + pm_pct / 100)
        pm_open = prev_close * (1 + rng.normal(pm_pct * 0.3, 1.0) / 100)
        pm_high = pm_close * (1 + pm_range / 200)
        pm_low = min(pm_open, pm_close) * (1 - pm_range / 200)
        pm_vol = float(rng.uniform(5e4, 3e6))
        has_news = int(rng.random() < 0.5)
        risky = int(has_news and rng.random() < 0.18)
        title = ""
        sent = 0.0
        if has_news:
            sent = float(np.clip(rng.normal(-0.6 if risky else 0.3, 0.4), -1, 1))
            title = f"{tkr} " + str(rng.choice(RISKY if risky else GOOD))
            news_rows.append((tkr, title,
                              pd.Timestamp(f"{date_str} 08:00", tz=ET)
                              .tz_convert("UTC").tz_localize(None),
                              "positive" if sent > 0 else ("negative" if sent < 0 else "neutral")))
        # planted weak edge: surge + positive sentiment help; risk news hurts
        edge = 0.004 * np.log1p(surge) + 0.006 * sent - 0.02 * risky - 0.00008 * pm_pct
        sigma = 0.0016 * (1 + pm_range / 10) * np.where(np.arange(NB) < 30, 1.6, 1.0)
        px0 = pm_close * float(1 + rng.normal(0, 0.004))
        do, dh, dl, dc = gen_path(px0, edge / NB + 0.25 * mkt_pm / 39000, sigma)
        dv = np.maximum(pm_vol / 40 * np.exp(-np.arange(NB) / 60)
                        * rng.uniform(0.4, 1.8, NB), 500.0)
        add_bars(tkr, ts, do, dh, dl, dc, dv)

        # ---- features/labels FROM the bars (pipeline definitions) ----
        o5_open, o5_close = float(do[0]), float(dc[4])
        o5_high, o5_low = float(dh[:5].max()), float(dl[:5].min())
        o5_vol = float(dv[:5].sum())
        o5_vwap = float((dc[:5] * dv[:5]).sum() / o5_vol)
        open_price = float(do[5])                     # 9:35 anchor open
        tp_lvl, sl_lvl = open_price * 1.01, open_price * 0.99
        tp_hits = np.nonzero(dh[6:] >= tp_lvl)[0]
        sl_hits = np.nonzero(dl[6:] <= sl_lvl)[0]
        tp_k = int(tp_hits[0]) if tp_hits.size else None
        sl_k = int(sl_hits[0]) if sl_hits.size else None
        post_close = float(dc[-1])
        if tp_k is not None and (sl_k is None or tp_k < sl_k):
            label = 1
        elif sl_k is not None:
            label = 0
        else:
            label = int(post_close > open_price)
        feat_rows.append(dict(
            date=date_str, ticker=tkr,
            pm_open=pm_open, pm_close=pm_close, pm_high=pm_high, pm_low=pm_low,
            pm_volume=pm_vol, pm_pct=pm_pct,
            pm_momentum=float((pm_close - pm_open) / pm_open * 100),
            pm_range_pct=pm_range, pm_vol_surge=surge,
            gap_vol_quality=float(pm_pct * np.log1p(surge)),
            price_bucket=int(min(pm_close // 10, 9)),
            news_sentiment=sent, news_count=has_news,
            has_earnings=int(has_news and sent > 0 and "earnings" in title),
            has_fda=int("FDA" in title),
            open5_pct=(o5_close - o5_open) / o5_open * 100,
            open5_range_pct=(o5_high - o5_low) / o5_open * 100,
            open5_vwap=(o5_close - o5_vwap) / o5_vwap * 100,
            open5_volume=o5_vol,
            open_price=open_price, post_open_close=post_close, label=label,
            intraday_open=float(do[0]), intraday_close=float(dc[-1]),
            intraday_high=float(dh.max()), intraday_low=float(dl.min()),
            intraday_volume=float(dv.sum()),
            intraday_pct=float((dc[-1] - do[0]) / do[0] * 100),
            intraday_range_pct=float((dh.max() - dl.min()) / do[0] * 100),
            intraday_vwap=float(np.average(dc, weights=dv)),
            eod_label=int(dc[-1] > do[0]),
            mkt_pm_pct=mkt_pm, mkt_open5_pct=float(rng.normal(mkt_pm / 3, 0.05)),
            mkt_rel_pm_pct=pm_pct - mkt_pm,
        ))

bars_df = pd.DataFrame(bar_cols)
fdf = pd.DataFrame(feat_rows)
ndf = pd.DataFrame(news_rows, columns=["ticker", "title", "published_utc", "sentiment"])
con = duckdb.connect(str(DB))
con.execute("DROP TABLE IF EXISTS features")
con.execute("DROP TABLE IF EXISTS minute_bars")
con.execute("DROP TABLE IF EXISTS market_news")
con.execute("CREATE TABLE minute_bars AS SELECT * FROM bars_df")
con.execute("CREATE TABLE features AS SELECT * FROM fdf")
con.execute("CREATE TABLE market_news AS SELECT * FROM ndf")
con.close()
print(f"SYNTHETIC DB written in {time.time()-t0:.1f}s: {DB}")
print(f"  {len(fdf):,} feature rows | {len(bars_df):,} bars | {len(ndf)} headlines — TEST DATA ONLY.")
