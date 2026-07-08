"""Massive REST news -> DuckDB `market_news` + refresh news features.

Fills the news gap in flat-file backfills: fetches headlines (with per-ticker
sentiment insights) for every candidate ticker in `features`, stores them in
`market_news`, then recomputes news_sentiment / news_count / has_earnings /
has_fda per (date) by reusing the engine's own `_sentiment_col` — identical
leak-safe window: [D-1 16:00 ET, D 09:30+NEWS_CUTOFF_MIN ET).

    python news_backfill.py --db data/e3_gap30.duckdb

Requires MASSIVE_API_KEY in .env. Resume-safe: news fetch is per-ticker
delete-then-insert; feature refresh is a plain UPDATE.
"""
from __future__ import annotations
import argparse
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import duckdb
import pandas as pd
import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

API = "https://api.massive.com/v2/reference/news"


def fetch_ticker_news(sess: requests.Session, key: str, ticker: str,
                      lo_utc: str, hi_utc: str) -> list[dict]:
    """All news for `ticker` published in [lo_utc, hi_utc], paginated."""
    out, url, params = [], API, {
        "ticker": ticker, "published_utc.gte": lo_utc, "published_utc.lte": hi_utc,
        "order": "asc", "limit": 1000, "apiKey": key}
    while url:
        for attempt in range(4):
            try:
                r = sess.get(url, params=params, timeout=30)
                if r.status_code == 429:
                    time.sleep(2 ** attempt)
                    continue
                r.raise_for_status()
                break
            except requests.RequestException:
                if attempt == 3:
                    raise
                time.sleep(2 ** attempt)
        j = r.json()
        for res in j.get("results", []):
            sent = None
            for ins in res.get("insights") or []:
                if ins.get("ticker") == ticker:
                    sent = ins.get("sentiment")
                    break
            pub = (res.get("published_utc") or "").replace("T", " ").rstrip("Z")
            out.append({"ticker": ticker, "title": res.get("title") or "",
                        "published_utc": pub, "sentiment": sent})
        nxt = j.get("next_url")
        url, params = (nxt, {"apiKey": key}) if nxt else (None, None)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", default="data/market_data.duckdb")
    ap.add_argument("--pad-days", type=int, default=2,
                    help="fetch window padding before first / after last date")
    args = ap.parse_args()

    key = os.environ.get("MASSIVE_API_KEY", "").strip()
    if not key:
        sys.exit("MASSIVE_API_KEY missing from .env")

    con = duckdb.connect(args.db)
    con.execute("""CREATE TABLE IF NOT EXISTS market_news (
        ticker VARCHAR, title VARCHAR, published_utc TIMESTAMP, sentiment VARCHAR)""")

    tickers = [r[0] for r in con.execute(
        "SELECT DISTINCT ticker FROM features ORDER BY ticker").fetchall()]
    d0, d1 = con.execute("SELECT MIN(date), MAX(date) FROM features").fetchone()
    lo = (pd.Timestamp(d0) - timedelta(days=args.pad_days)).strftime("%Y-%m-%dT00:00:00Z")
    hi = (pd.Timestamp(d1) + timedelta(days=args.pad_days)).strftime("%Y-%m-%dT23:59:59Z")
    done = {r[0] for r in con.execute(
        "SELECT DISTINCT ticker FROM market_news").fetchall()}
    todo = [t for t in tickers if t not in done]
    print(f"{len(tickers)} tickers, {len(done)} already fetched, {len(todo)} to go "
          f"({lo[:10]} .. {hi[:10]})")

    sess = requests.Session()
    t0, total = time.time(), 0
    for i, t in enumerate(todo, 1):
        rows = fetch_ticker_news(sess, key, t, lo, hi)
        con.execute("DELETE FROM market_news WHERE ticker = ?", [t])
        if rows:
            nf = pd.DataFrame(rows)
            nf["published_utc"] = pd.to_datetime(nf["published_utc"], errors="coerce")
            nf = nf.dropna(subset=["published_utc"])
            con.execute("INSERT INTO market_news SELECT * FROM nf")
            total += len(nf)
        if i % 100 == 0 or i == len(todo):
            print(f"[{i}/{len(todo)}] {t}: +{len(rows)} articles "
                  f"(total {total}, {time.time()-t0:.0f}s)")

    n_news = con.execute("SELECT COUNT(*) FROM market_news").fetchone()[0]
    print(f"market_news now holds {n_news} articles")

    # ---- refresh features' news columns via the ENGINE's own logic --------
    os.environ.setdefault("MAMDOUH_DB", args.db)   # keep edited.py import happy
    import edited                                   # noqa: E402
    dates = [str(r[0]) for r in con.execute(
        "SELECT DISTINCT date FROM features ORDER BY date").fetchall()]
    upd = 0
    for d in dates:
        day = con.execute(
            "SELECT ticker FROM features WHERE date = ?", [d]).df()
        day = edited._sentiment_col(con, day, d)
        for _, r in day.iterrows():
            con.execute("""UPDATE features SET news_sentiment=?, news_count=?,
                has_earnings=?, has_fda=? WHERE date=? AND ticker=?""",
                [float(r.news_sentiment), int(r.news_count),
                 int(r.has_earnings), int(r.has_fda), d, r.ticker])
        upd += len(day)
    nz = con.execute(
        "SELECT SUM(CASE WHEN news_count>0 THEN 1 ELSE 0 END) FROM features").fetchone()[0]
    print(f"refreshed {upd} feature rows across {len(dates)} dates; "
          f"{nz} now have news_count>0")


if __name__ == "__main__":
    main()
