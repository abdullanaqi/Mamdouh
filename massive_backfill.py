"""Massive (Polygon) FLAT FILES -> DuckDB backfill for `python edited.py --compare`.

This is the gold-standard data path: minute aggregates for ALL tickers
including extended hours, so it builds the TRUE premarket-gainer universe
point-in-time (delisted names included) with REAL premarket features —
none of the FMP proxies.

    export MASSIVE_S3_KEY=...        # flat-files Access Key ID
    export MASSIVE_S3_SECRET=...     # flat-files Secret Access Key
    python massive_backfill.py --from 2026-01-02 --to 2026-07-02

Then:  python edited.py --compare

Source objects (bucket `flatfiles`, endpoint https://files.massive.com):
    us_stocks_sip/day_aggs_v1/YYYY/MM/YYYY-MM-DD.csv.gz   (~MBs)
    us_stocks_sip/minute_aggs_v1/YYYY/MM/YYYY-MM-DD.csv.gz (~tens of MB)

Per day: previous-day closes from day_aggs; premarket [04:00, 09:30) ET
aggregated per ticker straight off the minute file (universe + pm_*
features, all knowable at 09:30 — leak-safe); regular-session bars stored
only for the selected candidates + SPY. pm_vol_surge uses each ticker's
own trailing premarket-volume baseline, mirroring the live pipeline.

Notes:
  * Flat files carry no news. market_news stays empty unless your live
    pipeline merges it — news features are honest zeros here, stated in
    reports rather than faked.
  * halal_stocks.json is applied when present; otherwise the universe is
    unfiltered and the run says so.
"""
from __future__ import annotations
import argparse
import gzip
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import boto3
import botocore
import duckdb
import numpy as np
import pandas as pd
from dotenv import load_dotenv

# Load flat-files credentials from the project .env (same file edited.py uses)
# so `python massive_backfill.py` works without a separate `export` step.
load_dotenv(Path(__file__).parent / ".env")

ET = ZoneInfo("America/New_York")
TICKER_RE = re.compile(r"^[A-Z]{1,5}$")
PFX_DAY = "us_stocks_sip/day_aggs_v1"
PFX_MIN = "us_stocks_sip/minute_aggs_v1"


def ns_utc(d: str, hh: int, mm: int) -> int:
    return int(datetime.strptime(d, "%Y-%m-%d")
               .replace(hour=hh, minute=mm, tzinfo=ET).timestamp() * 1e9)


def s3_client():
    key = os.environ.get("MASSIVE_S3_KEY", "")
    sec = os.environ.get("MASSIVE_S3_SECRET", "")
    if not key or not sec:
        sys.exit("Set MASSIVE_S3_KEY and MASSIVE_S3_SECRET (flat-files credentials).")
    return boto3.client(
        "s3",
        endpoint_url=os.environ.get("MASSIVE_S3_ENDPOINT", "https://files.massive.com"),
        aws_access_key_id=key, aws_secret_access_key=sec,
        config=botocore.config.Config(connect_timeout=20, read_timeout=120,
                                      retries={"max_attempts": 3}))


def list_dates(s3, bucket: str, d0: str, d1: str) -> list[str]:
    """Available trading dates from the day_aggs listing (calendar truth)."""
    y0, m0 = int(d0[:4]), int(d0[5:7])
    y1, m1 = int(d1[:4]), int(d1[5:7])
    dates = []
    y, m = y0, m0
    while (y, m) <= (y1, m1):
        pfx = f"{PFX_DAY}/{y:04d}/{m:02d}/"
        tok = None
        while True:
            kw = dict(Bucket=bucket, Prefix=pfx, MaxKeys=1000)
            if tok:
                kw["ContinuationToken"] = tok
            r = s3.list_objects_v2(**kw)
            for o in r.get("Contents", []):
                d = Path(o["Key"]).name.replace(".csv.gz", "")
                if d0 <= d <= d1:
                    dates.append(d)
            if not r.get("IsTruncated"):
                break
            tok = r.get("NextContinuationToken")
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return sorted(set(dates))


def fetch(s3, bucket: str, key: str, dst: Path) -> float:
    if dst.exists() and dst.stat().st_size > 0:
        return dst.stat().st_size / 1e6
    s3.download_file(bucket, key, str(dst))
    return dst.stat().st_size / 1e6


# ------------------------------------------------------------- per-day core
def prev_close_map(con, day_file: Path) -> dict[str, tuple[float, float]]:
    rows = con.execute(f"""
        SELECT ticker, close, volume FROM read_csv_auto('{day_file}')
    """).fetchall()
    return {t: (float(c or 0), float(v or 0)) for t, c, v in rows if t}


def premarket_agg(con, min_file: Path, d: str) -> pd.DataFrame:
    lo, hi = ns_utc(d, 4, 0), ns_utc(d, 9, 30)
    return con.execute(f"""
        SELECT ticker,
               FIRST(open  ORDER BY window_start) AS pm_open,
               LAST (close ORDER BY window_start) AS pm_close,
               MAX(high)   AS pm_high,
               MIN(low)    AS pm_low,
               SUM(volume) AS pm_volume
        FROM read_csv_auto('{min_file}')
        WHERE window_start >= {lo} AND window_start < {hi}
        GROUP BY ticker
    """).fetchdf()


def rth_bars(con, min_file: Path, d: str, tickers: list[str]) -> pd.DataFrame:
    if not tickers:
        return pd.DataFrame()
    lo, hi = ns_utc(d, 9, 30), ns_utc(d, 16, 0)
    tl = ",".join(f"'{t}'" for t in tickers if TICKER_RE.match(t))
    return con.execute(f"""
        SELECT ticker, window_start, open, high, low, close, volume
        FROM read_csv_auto('{min_file}')
        WHERE window_start >= {lo} AND window_start < {hi}
          AND ticker IN ({tl})
        ORDER BY ticker, window_start
    """).fetchdf()


def build_row(d: str, tkr: str, b: pd.DataFrame, pm: dict, prev_close: float,
              surge: float, mkt_gap: float, mkt_o5: float) -> dict | None:
    if len(b) < 30 or prev_close <= 0 or pm["pm_open"] <= 0:
        return None
    o, h, l, c, v = (b[x].to_numpy(float) for x in ("open", "high", "low", "close", "volume"))
    m = ((b["window_start"].to_numpy(np.int64) - ns_utc(d, 9, 30)) // 60_000_000_000)

    pm_pct = (pm["pm_close"] / prev_close - 1) * 100
    pm_range = (pm["pm_high"] - pm["pm_low"]) / pm["pm_open"] * 100

    o5 = m < 5
    if not o5.any():
        return None
    o5_open, o5_close = float(o[o5][0]), float(c[o5][-1])
    o5_high, o5_low = float(h[o5].max()), float(l[o5].min())
    o5_vol = float(v[o5].sum())
    o5_vwap = float((c[o5] * v[o5]).sum() / max(o5_vol, 1e-9))

    a = int(np.searchsorted(m, 5))
    if a >= len(m):
        return None
    open_price = float(o[a])
    end = int(np.searchsorted(m, m[a] + 400, side="right"))
    tp_lvl, sl_lvl = open_price * 1.01, open_price * 0.99
    tp_hits = np.nonzero(h[a:end] >= tp_lvl)[0]
    sl_hits = np.nonzero(l[a:end] <= sl_lvl)[0]
    tp_k = int(tp_hits[0]) if tp_hits.size else None
    sl_k = int(sl_hits[0]) if sl_hits.size else None
    post_close = float(c[end - 1])
    if tp_k is not None and (sl_k is None or tp_k < sl_k):
        label = 1
    elif sl_k is not None:
        label = 0
    else:
        label = int(post_close > open_price)

    bins = [-np.inf, 2.0, 5.0, 10.0, 25.0, 50.0, 100.0, np.inf]
    return dict(
        date=d, ticker=tkr,
        pm_open=float(pm["pm_open"]), pm_close=float(pm["pm_close"]),
        pm_high=float(pm["pm_high"]), pm_low=float(pm["pm_low"]),
        pm_volume=float(pm["pm_volume"]), pm_pct=pm_pct,
        pm_momentum=(pm["pm_close"] - pm["pm_open"]) / pm["pm_open"] * 100,
        pm_range_pct=max(pm_range, 1e-6), pm_vol_surge=max(surge, 0.05),
        gap_vol_quality=max(surge, 0.05) * abs(pm_pct) / max(pm_range, 1e-6),
        price_bucket=int(np.digitize(pm["pm_close"], bins, right=True) - 1),
        news_sentiment=0.0, news_count=0, has_earnings=0, has_fda=0,
        open5_pct=(o5_close - o5_open) / o5_open * 100,
        open5_range_pct=(o5_high - o5_low) / o5_open * 100,
        open5_vwap=(o5_vwap - o5_open) / o5_open * 100,
        open5_volume=o5_vol,
        open_price=open_price, post_open_close=post_close, label=label,
        intraday_open=float(o[0]), intraday_close=float(c[-1]),
        intraday_high=float(h.max()), intraday_low=float(l.min()),
        intraday_volume=float(v.sum()),
        intraday_pct=(float(c[-1]) - float(o[0])) / float(o[0]) * 100,
        intraday_range_pct=(float(h.max()) - float(l.min())) / float(o[0]) * 100,
        intraday_vwap=float(np.average(c, weights=np.maximum(v, 1e-9))),
        eod_label=int(c[-1] > o[0]),
        mkt_pm_pct=mkt_gap, mkt_open5_pct=mkt_o5,
        mkt_rel_pm_pct=pm_pct - mkt_gap,
    )


# ------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--from", dest="d0", required=True)
    ap.add_argument("--to", dest="d1", required=True)
    ap.add_argument("--top", type=int, default=12)
    ap.add_argument("--min-gap", type=float, default=2.0)
    ap.add_argument("--max-gap", type=float, default=0.0,
                    help="drop premarket gaps above this %% (0 = no cap). "
                         "Extreme gappers gap through stops -> deep drawdowns.")
    ap.add_argument("--min-price", type=float, default=5.0)
    ap.add_argument("--max-price", type=float, default=100.0)
    ap.add_argument("--min-pm-dollar", type=float, default=300_000.0)
    ap.add_argument("--db", default="data/market_data.duckdb")
    ap.add_argument("--halal", default="halal_stocks.json")
    ap.add_argument("--tmp", default="ff_tmp")
    ap.add_argument("--keep-files", action="store_true")
    ap.add_argument("--limit-days", type=int, default=0)
    args = ap.parse_args()

    bucket = os.environ.get("MASSIVE_S3_BUCKET", "flatfiles")
    s3 = s3_client()
    tmp = Path(args.tmp); tmp.mkdir(exist_ok=True)

    halal = None
    if Path(args.halal).exists():
        import json as _json
        halal = {e["ticker"] for e in _json.loads(Path(args.halal).read_text())}
        print(f"halal filter: {len(halal)} tickers")
    else:
        print("NOTE: no halal_stocks.json found — universe is UNFILTERED for halal.")

    dates = list_dates(s3, bucket, args.d0, args.d1)
    if len(dates) < 2:
        sys.exit(f"Only {len(dates)} day files in range — check dates/credentials.")
    if args.limit_days:
        dates = dates[: args.limit_days + 1]
    print(f"{len(dates)} trading days {dates[0]} → {dates[-1]}")

    Path(args.db).parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(args.db)
    con.execute("""CREATE TABLE IF NOT EXISTS minute_bars (ticker VARCHAR,
        window_start BIGINT, open DOUBLE, high DOUBLE, low DOUBLE,
        close DOUBLE, volume DOUBLE)""")
    con.execute("""CREATE TABLE IF NOT EXISTS market_news (ticker VARCHAR,
        title VARCHAR, published_utc TIMESTAMP, sentiment VARCHAR)""")
    have = set()
    try:
        have = {r[0] for r in con.execute("SELECT DISTINCT date FROM features").fetchall()}
    except Exception:
        pass

    pm_hist: dict[str, list[float]] = {}
    total_rows, t0 = 0, time.time()

    for i in range(1, len(dates)):
        d, dp = dates[i], dates[i - 1]
        if d in have:
            continue
        try:
            dayf = tmp / f"day_{dp}.csv.gz"
            fetch(s3, bucket, f"{PFX_DAY}/{dp[:4]}/{dp[5:7]}/{dp}.csv.gz", dayf)
            minf = tmp / f"min_{d}.csv.gz"
            mb = fetch(s3, bucket, f"{PFX_MIN}/{d[:4]}/{d[5:7]}/{d}.csv.gz", minf)
        except botocore.exceptions.ClientError as e:
            print(f"[{d}] download failed: {e} — skipping")
            continue

        prev = prev_close_map(con, dayf)
        pm = premarket_agg(con, minf, d)

        spy_prev = prev.get("SPY", (0.0, 0.0))[0]
        spy_pm = pm[pm["ticker"] == "SPY"]
        mkt_gap = ((float(spy_pm["pm_close"].iloc[0]) / spy_prev - 1) * 100
                   if len(spy_pm) and spy_prev > 0 else 0.0)

        picks: list[tuple[str, dict, float, float]] = []   # tkr, pm row, prev_close, surge
        cand = pm[pm["ticker"].map(lambda t: bool(TICKER_RE.match(str(t))))]
        for _, r in cand.iterrows():
            t = r["ticker"]
            pc = prev.get(t, (0.0, 0.0))[0]
            if pc <= 0 or t == "SPY":
                continue
            if halal is not None and t not in halal:
                continue
            px = float(r["pm_close"])
            if not (args.min_price <= px <= args.max_price):
                continue
            if px * float(r["pm_volume"]) < args.min_pm_dollar:
                continue
            gap = (px / pc - 1) * 100
            if gap < args.min_gap:
                continue
            if args.max_gap and gap > args.max_gap:
                continue
            hist = pm_hist.get(t, [])
            base = float(np.mean(hist)) if len(hist) >= 5 else 0.0
            surge = float(r["pm_volume"]) / base if base > 0 else 1.0
            picks.append((t, r.to_dict(), pc, surge))
        picks = sorted(picks, key=lambda x: -((x[1]["pm_close"] / x[2] - 1)))[: args.top]

        # update premarket-volume baselines AFTER selection (prior-days-only)
        for _, r in cand.iterrows():
            h = pm_hist.setdefault(r["ticker"], [])
            h.append(float(r["pm_volume"]))
            if len(h) > 20:
                del h[0]

        need = [t for t, *_ in picks] + ["SPY"]
        bars = rth_bars(con, minf, d, need)
        by_t = dict(tuple(bars.groupby("ticker"))) if not bars.empty else {}
        sb = by_t.get("SPY")
        mkt_o5 = 0.0
        if sb is not None and len(sb) >= 5:
            mkt_o5 = (float(sb["close"].iloc[4]) / float(sb["open"].iloc[0]) - 1) * 100

        con.execute("DELETE FROM minute_bars WHERE window_start >= ? AND window_start < ?",
                    [ns_utc(d, 0, 0), ns_utc(d, 23, 59)])
        rows = []
        for t, pmr, pc, surge in picks:
            b = by_t.get(t)
            if b is None:
                continue
            fr = build_row(d, t, b, pmr, pc, surge, mkt_gap, mkt_o5)
            if fr:
                rows.append(fr)
        if not bars.empty:
            keep = bars[bars["ticker"].isin([r["ticker"] for r in rows] + ["SPY"])]
            con.execute("INSERT INTO minute_bars SELECT * FROM keep")
        if rows:
            fdf = pd.DataFrame(rows)
            try:
                con.execute("DELETE FROM features WHERE date = ?", [d])
            except Exception:
                con.execute("CREATE TABLE features AS SELECT * FROM fdf WHERE 1=0")
            con.execute("INSERT INTO features BY NAME SELECT * FROM fdf")
            total_rows += len(rows)
        if not args.keep_files:
            minf.unlink(missing_ok=True)
            if i >= 2:
                (tmp / f"day_{dates[i-2]}.csv.gz").unlink(missing_ok=True)
        print(f"[{i}/{len(dates)-1}] {d}: {len(rows):>2} candidates "
              f"({mb:5.1f} MB, {time.time()-t0:6.0f}s elapsed)")

    con.close()
    print(f"\nDone. {total_rows} feature rows written to {args.db}.")
    print("Now run:  python edited.py --compare")
    print("This universe is TRUE point-in-time premarket gainers (delisted included).")
    print("News features are zeros (flat files carry no news) — stated, not faked.")


if __name__ == "__main__":
    main()
