"""Run walk-forward validation for one strategy family.

Usage:
    python scripts/run_walk_forward.py --strategy gap        # grid search
    python scripts/run_walk_forward.py --strategy ml
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.data.massive_loader import available_minute_dates, load_minute_day
from src.data.universe_builder import load_universe_panel
from src.reports.report_generator import write_walk_forward_report
from src.research.strategy_search import GapRvolStrategy, MLRankingStrategy, grid
from src.research.walk_forward import walk_forward_grid, walk_forward_single


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--strategy", choices=["gap", "ml"], default="gap")
    ap.add_argument("--provenance", default="REAL Massive flat files (verify dates in data/)")
    a = ap.parse_args()
    panel = load_universe_panel()
    dates = available_minute_dates()
    if a.strategy == "gap":
        res = walk_forward_grid(GapRvolStrategy, grid(GapRvolStrategy), dates,
                                panel, load_minute_day)
        name = "GapRvolStrategy (grid)"
    else:
        res = walk_forward_single(MLRankingStrategy, dates, panel, load_minute_day)
        name = "MLRankingStrategy"
    write_walk_forward_report(res, a.provenance, name)
    print(json.dumps(res.get("oos_card", res), indent=2, default=str))


if __name__ == "__main__":
    main()
