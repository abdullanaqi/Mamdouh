"""Load Massive/Polygon flat-file aggregates into DuckDB for backtesting.

Downloads us_stocks_sip minute + day aggregates from the S3 flat-file bucket,
filters to the halal universe, and loads into data/market_data.duckdb:
  - minute_bars(ticker, window_start[ns UTC], open, close, high, low, volume)
  - day_bars(date, ticker, open, close, high, low, volume)

Restartable: skips dates already present. Parallel downloads.
"""
import gzip
import io
import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from pathlib import Path

import boto3
import duckdb
import pandas as pd
from botocore.config import Config
from dotenv import load_dotenv

BASE = Path(__file__).resolve().parent.parent
load_dotenv(BASE / ".env")
DB = BASE / "data" / "market_data.duckdb"
BUCKET = "flatfiles"
MIN_PREFIX = "us_stocks_sip/minute_aggs_v1"
DAY_PREFIX = "us_stocks_sip/day_aggs_v1"


def _s3():
    return boto3.client(
        "s3", endpoint_url="https://files.massive.com",
        aws_access_key_id=os.getenv("MASSIVE_S3_ACCESS_KEY_ID"),
        aws_secret_access_key=os.getenv("MASSIVE_S3_SECRET_ACCESS_KEY"),
        config=Config(signature_version="s3v4", connect_timeout=10,
                      read_timeout=120, retries={"max_attempts": 3},
                      max_pool_connections=32),
    )


def _halal() -> set:
    return {e["ticker"] for e in json.load(open(BASE / "results" / "halal_stocks.json"))}


def _trading_days(start: date, end: date) -> list[date]:
    d, out = start, []
    while d <= end:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def _fetch_csv(s3, prefix: str, d: date) -> pd.DataFrame | None:
    key = f"{prefix}/{d:%Y/%m/%Y-%m-%d}.csv.gz"
    try:
        raw = s3.get_object(Bucket=BUCKET, Key=key)["Body"].read()
    except s3.exceptions.NoSuchKey:
        return None
    except Exception as e:
        if "NoSuchKey" in str(e) or "404" in str(e):
            return None
        raise
    return pd.read_csv(io.BytesIO(gzip.decompress(raw)))


def _ensure_tables(con):
    con.execute("""CREATE TABLE IF NOT EXISTS minute_bars(
        ticker VARCHAR, window_start BIGINT, open DOUBLE, close DOUBLE,
        high DOUBLE, low DOUBLE, volume DOUBLE)""")
    con.execute("""CREATE TABLE IF NOT EXISTS day_bars(
        date VARCHAR, ticker VARCHAR, open DOUBLE, close DOUBLE,
        high DOUBLE, low DOUBLE, volume DOUBLE)""")
    con.execute("""CREATE TABLE IF NOT EXISTS loaded_days(
        date VARCHAR PRIMARY KEY)""")


def load(start="2026-01-01", end="2026-05-29", workers=12):
    halal = _halal()
    con = duckdb.connect(str(DB))
    _ensure_tables(con)
    done = {r[0] for r in con.execute("SELECT date FROM loaded_days").fetchall()}
    days = [d for d in _trading_days(date.fromisoformat(start), date.fromisoformat(end))
            if d.isoformat() not in done]
    print(f"Halal universe: {len(halal)} | days to load: {len(days)}")
    s3 = _s3()

    def work(d: date):
        mdf = _fetch_csv(s3, MIN_PREFIX, d)
        if mdf is None:
            return d, None, None          # holiday / missing
        mdf = mdf[mdf["ticker"].isin(halal)].copy()
        ddf = _fetch_csv(s3, DAY_PREFIX, d)
        if ddf is not None:
            ddf = ddf[ddf["ticker"].isin(halal)].copy()
        return d, mdf, ddf

    n_min = n_day = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = {pool.submit(work, d): d for d in days}
        for i, fut in enumerate(as_completed(futs), 1):
            d, mdf, ddf = fut.result()
            ds = d.isoformat()
            if mdf is None:
                con.execute("INSERT OR IGNORE INTO loaded_days VALUES (?)", [ds])
                print(f"  [{i}/{len(days)}] {ds}: holiday/missing")
                continue
            mb = mdf[["ticker", "window_start", "open", "close", "high", "low", "volume"]]
            con.execute("INSERT INTO minute_bars SELECT * FROM mb")
            if ddf is not None and not ddf.empty:
                ddf["date"] = ds
                db = ddf[["date", "ticker", "open", "close", "high", "low", "volume"]]
                con.execute("INSERT INTO day_bars SELECT * FROM db")
                n_day += len(db)
            con.execute("INSERT OR IGNORE INTO loaded_days VALUES (?)", [ds])
            con.commit()
            n_min += len(mb)
            print(f"  [{i}/{len(days)}] {ds}: {len(mb):,} min rows "
                  f"({mdf['ticker'].nunique()} tickers)  | total {n_min:,}", flush=True)

    print(f"\nDone. minute rows added: {n_min:,} | day rows added: {n_day:,}")
    con.close()


if __name__ == "__main__":
    import sys
    a = sys.argv[1:]
    load(start=a[0] if len(a) > 0 else "2026-01-01",
         end=a[1] if len(a) > 1 else "2026-05-29")
