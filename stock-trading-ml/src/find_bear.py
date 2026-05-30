"""Locate bear/selloff stretches in 2024-2025 using small day-aggs flat files.

Downloads only us_stocks_sip/day_aggs_v1 (tiny vs minute aggs), computes a
breadth-weighted daily market proxy across the halal universe, and prints the
worst rolling windows so we can pull minute data for just those dates.
"""
import gzip, io, json, os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from pathlib import Path

import boto3, pandas as pd
from botocore.config import Config
from dotenv import load_dotenv

BASE = Path(__file__).resolve().parent.parent
load_dotenv(BASE / ".env")
HALAL = {e["ticker"] for e in json.load(open(BASE / "results" / "halal_stocks.json"))}


def s3():
    return boto3.client("s3", endpoint_url="https://files.massive.com",
        aws_access_key_id=os.getenv("MASSIVE_S3_ACCESS_KEY_ID"),
        aws_secret_access_key=os.getenv("MASSIVE_S3_SECRET_ACCESS_KEY"),
        config=Config(signature_version="s3v4", read_timeout=60,
                      retries={"max_attempts": 3}, max_pool_connections=24))


def days(a, b):
    d, out = date.fromisoformat(a), []
    e = date.fromisoformat(b)
    while d <= e:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def fetch(cli, d):
    key = f"us_stocks_sip/day_aggs_v1/{d:%Y/%m/%Y-%m-%d}.csv.gz"
    try:
        raw = cli.get_object(Bucket="flatfiles", Key=key)["Body"].read()
    except Exception:
        return d, None
    df = pd.read_csv(io.BytesIO(gzip.decompress(raw)))
    df = df[df["ticker"].isin(HALAL) & (df["open"] > 0) & (df["close"] >= 5)]
    if df.empty:
        return d, None
    ret = ((df["close"] - df["open"]) / df["open"] * 100)
    # volume-weighted daily breadth proxy
    w = df["volume"].clip(lower=0) + 1
    return d, float((ret * w).sum() / w.sum())


def main(a="2024-01-01", b="2025-12-31"):
    cli = s3()
    rows = {}
    dl = days(a, b)
    with ThreadPoolExecutor(max_workers=16) as p:
        futs = {p.submit(fetch, cli, d): d for d in dl}
        for i, f in enumerate(as_completed(futs), 1):
            d, v = f.result()
            if v is not None:
                rows[d.isoformat()] = v
            if i % 50 == 0:
                print(f"  scanned {i}/{len(dl)}", flush=True)
    s = pd.Series(rows).sort_index()
    s.to_csv(BASE / "results" / "universe_drift_2024_2025.csv")
    cum = s.cumsum()
    # worst 40-trading-day windows by forward sum
    win = s.rolling(40).sum()
    print("\nWorst 40-day stretches (end date, sum drift%):")
    for dt, v in win.dropna().sort_values().head(8).items():
        print(f"  ...ending {dt}: {v:.1f}%")
    print(f"\nFull-period mean daily drift: {s.mean():.3f}%  | down days: {(s<0).mean()*100:.0f}%")
    print(f"Saved -> results/universe_drift_2024_2025.csv ({len(s)} days)")


if __name__ == "__main__":
    main()
