"""Produce the one-trade pick for a date.

Historical mode replays a past date using ONLY data available at the
decision time on that date (training strictly on prior dates).

Live mode is intentionally not implemented: flat files are end-of-day, so
a live pick needs the Massive REST/WebSocket intraday feed
(src/data/massive_rest_client.py is an honest stub).

Usage:
    python scripts/generate_daily_pick.py --date 2025-09-15
"""
import argparse
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.data.massive_loader import available_minute_dates, load_minute_day
from src.data.universe_builder import load_universe_panel
from src.research.strategy_search import GapRvolStrategy
from src.selector.daily_trade_selector import select_for_date

TRAIN_LOOKBACK_DAYS = 120


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True)
    ap.add_argument("--live", action="store_true")
    a = ap.parse_args()
    if a.live:
        sys.exit("Live mode not implemented: needs Massive REST/WS intraday feed. "
                 "See src/data/massive_rest_client.py and README_TRUTH.md.")
    d = pd.Timestamp(a.date)
    panel = load_universe_panel()
    dates = [x for x in available_minute_dates() if x < d]
    if len(dates) < 40:
        sys.exit(f"Not enough history before {a.date} ({len(dates)} days).")
    train = dates[-TRAIN_LOOKBACK_DAYS:]
    prod = Path("models/production/frozen_params.json")
    params = {}
    if prod.exists():
        import json
        params = json.load(open(prod))
        print(f"using frozen production params: {params}")
    else:
        print("WARNING: no frozen production params; using defaults. "
              "Run research first and freeze a validated configuration.")
    strat = GapRvolStrategy(**params).fit(train, panel, load_minute_day)
    pick = select_for_date(strat, d, panel, load_minute_day, write=True)
    print(pick.to_markdown())


if __name__ == "__main__":
    main()
