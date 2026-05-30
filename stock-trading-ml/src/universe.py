"""Build the halal stock universe from EODHD via Shariah sector/industry exclusion.

Mirrors halal-gap-orb/config/halal_exclusions.yaml: case-insensitive substring
match against EODHD `sector`/`industry`. Writes results/halal_stocks.json as a
list of {"ticker": CODE} entries (the format system_asis.py expects).
"""
import json
import os
import time
import urllib.parse
from pathlib import Path

import requests
from dotenv import load_dotenv

BASE = Path(__file__).resolve().parent.parent
load_dotenv(BASE / ".env")
EOD = os.getenv("EODHD_API_KEY")
OUT = BASE / "results" / "halal_stocks.json"

EXCLUDED_SECTORS = ["financial services", "financial", "banks"]
EXCLUDED_INDUSTRIES = [
    "banks", "insurance", "capital markets", "credit services",
    "mortgage finance", "asset management", "financial conglomerates",
    "financial data & stock exchanges", "gambling", "resorts & casinos",
    "alcoholic beverages", "beverages - wineries & distilleries",
    "beverages - brewers", "tobacco",
]

MIN_MCAP = 3.0e8           # $300M floor — liquid enough to trade
EXCHANGES = {"NYSE", "NASDAQ", "NYSE MKT", "NYSE ARCA", "BATS", "AMEX"}


def _haram(sector: str, industry: str) -> bool:
    s = (sector or "").lower()
    i = (industry or "").lower()
    if any(x in s for x in EXCLUDED_SECTORS):
        return True
    if any(x in i for x in EXCLUDED_INDUSTRIES):
        return True
    return False


def _screen_bucket(lo: float, hi: float | None) -> list[dict]:
    """EODHD caps limit at 100/call and offset ~1000, so page within a band."""
    rows, limit = [], 100
    for offset in range(0, 1000, limit):
        filt = [["exchange", "=", "us"], ["market_capitalization", ">=", lo]]
        if hi is not None:
            filt.append(["market_capitalization", "<", hi])
        url = (f"https://eodhd.com/api/screener?api_token={EOD}"
               f"&filters={urllib.parse.quote(json.dumps(filt))}"
               f"&limit={limit}&offset={offset}&sort=market_capitalization.desc")
        for attempt in range(4):
            r = requests.get(url, timeout=40)
            if r.status_code == 200:
                break
            time.sleep(2 ** attempt)
        else:
            raise RuntimeError(f"screener failed: {r.status_code} {r.text[:120]}")
        batch = r.json()
        if isinstance(batch, dict):
            batch = batch.get("data", [])
        if not batch:
            break
        rows.extend(batch)
        if len(batch) < limit:
            break
    return rows


def build() -> list[str]:
    # Fine market-cap bands keep each query's result set under the offset ceiling.
    edges = [3e8, 5e8, 7.5e8, 1e9, 1.5e9, 2e9, 3e9, 5e9, 1e10,
             2e10, 5e10, 1e11, 5e11, None]
    bands = [(edges[i], edges[i + 1]) for i in range(len(edges) - 1)]
    seen: dict[str, dict] = {}
    for lo, hi in bands:
        for row in _screen_bucket(lo, hi):
            code = row.get("code")
            if code and code not in seen:
                seen[code] = row
        print(f"  band ${lo:.0e}-{hi}: cumulative {len(seen)}")

    halal = []
    for code, row in seen.items():
        if "." in code or "-" in code:          # skip preferred/units/warrants
            continue
        if _haram(row.get("sector"), row.get("industry")):
            continue
        halal.append(code)
    halal.sort()
    return halal


if __name__ == "__main__":
    tickers = build()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps([{"ticker": t} for t in tickers], indent=0))
    print(f"\nHalal universe: {len(tickers)} tickers -> {OUT.relative_to(BASE)}")
    print("sample:", tickers[:25])
