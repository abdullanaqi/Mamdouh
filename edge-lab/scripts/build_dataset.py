"""Download Massive flat files for a date range and build processed data.

Usage:
    python scripts/build_dataset.py --start 2023-01-01 --end 2025-12-31
Requires MASSIVE_ACCESS_KEY_ID / MASSIVE_SECRET_ACCESS_KEY in .env and
network access to files.massive.com (NOT available inside restricted
sandboxes -- run on your own machine).
"""
import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.data.massive_loader import build_dataset, load_daily_history
from src.data.universe_builder import build_daily_panel, save_universe


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--kinds", default="day,minute")
    a = ap.parse_args()
    kinds = tuple(k.strip() for k in a.kinds.split(","))
    build_dataset(date.fromisoformat(a.start), date.fromisoformat(a.end), kinds)
    panel = build_daily_panel(load_daily_history())
    save_universe(panel)
    print("dataset + daily panel built.")


if __name__ == "__main__":
    main()
