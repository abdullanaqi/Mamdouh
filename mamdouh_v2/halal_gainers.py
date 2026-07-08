"""Live top halal gainers, direct from the Massive REST snapshot API.

Usage:
    python halal_gainers.py              # top 25 gainers in the halal universe
    python halal_gainers.py --top 50     # show more rows
    python halal_gainers.py --min-price 5 --min-volume 100000

Requires MASSIVE_API_KEY in .env. Data is the real-time snapshot
(/v2/snapshot/locale/us/markets/stocks/tickers), fetched in chunks of 100
tickers, ranked by today's percent change vs the prior close.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_REST_URL = "https://api.massive.com"
CHUNK = 100


def _get_json(base: str, api_key: str, path: str, params: dict[str, str]) -> dict:
    url = f"{base}{path}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(
        url, headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_snapshots(tickers: list[str]) -> list[dict]:
    api_key = os.getenv("MASSIVE_API_KEY") or os.getenv("API_KEY")
    if not api_key:
        raise SystemExit("Missing MASSIVE_API_KEY in .env")
    base = os.getenv("MASSIVE_REST_URL", DEFAULT_REST_URL).rstrip("/")

    halal = set(tickers)
    rows: list[dict] = []
    for i in range(0, len(tickers), CHUNK):
        chunk = tickers[i : i + CHUNK]
        try:
            payload = _get_json(
                base, api_key,
                "/v2/snapshot/locale/us/markets/stocks/tickers",
                {"tickers": ",".join(chunk)},
            )
        except Exception as exc:
            print(f"chunk {i // CHUNK}: request failed: {exc}", file=sys.stderr)
            continue
        for t in payload.get("tickers", []):
            sym = t.get("ticker")
            if sym not in halal:
                continue
            day = t.get("day") or {}
            prev = t.get("prevDay") or {}
            min_bar = t.get("min") or {}
            rows.append({
                "ticker": sym,
                "pct": t.get("todaysChangePerc") or 0.0,
                "last": day.get("c") or min_bar.get("c") or 0,
                "day_o": day.get("o") or 0,
                "day_h": day.get("h") or 0,
                "day_v": day.get("v") or 0,
                "prev_c": prev.get("c") or 0,
            })
    return rows


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--top", type=int, default=25, help="How many gainers to print")
    p.add_argument("--min-price", type=float, default=0.0, help="Only show tickers with last price >= this")
    p.add_argument("--min-volume", type=float, default=0.0, help="Only show tickers with day volume >= this")
    args = p.parse_args()

    load_dotenv(os.path.join(BASE_DIR, ".env"))
    tickers = json.load(open(os.path.join(BASE_DIR, "halal_tickers.json")))

    rows = fetch_snapshots(tickers)
    active = [
        r for r in rows
        if r["last"] and r["day_v"]
        and r["last"] >= args.min_price and r["day_v"] >= args.min_volume
    ]
    active.sort(key=lambda r: r["pct"], reverse=True)

    now_ny = datetime.now(ZoneInfo("America/New_York"))
    print(f"\nLive halal gainers  |  {now_ny:%Y-%m-%d %H:%M:%S} NY  |  "
          f"{len(rows)} snapshots, {len(active)} after filters\n")
    print(f"{'#':>2}  {'TICKER':<7}{'CHG%':>8}  {'LAST':>9}  {'OPEN':>9}  {'HIGH':>9}  {'PREV C':>9}  {'VOLUME':>12}")
    for n, r in enumerate(active[: args.top], 1):
        print(f"{n:>2}  {r['ticker']:<7}{r['pct']:>+7.2f}%  {r['last']:>9.2f}  {r['day_o']:>9.2f}  "
              f"{r['day_h']:>9.2f}  {r['prev_c']:>9.2f}  {r['day_v']:>12,.0f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
