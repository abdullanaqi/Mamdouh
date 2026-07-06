"""End-to-end smoke test on SYNTHETIC data.

Proves: data layout -> features (no lookahead) -> walk-forward grid ->
stress battery -> edge gate -> daily selector all run and interlock.

Expected honest outcome on random-walk data: NO VALID EDGE FOUND YET.
If the gate ever declares an edge candidate on this data, that is itself
a bug (false-positive) and must be investigated.
"""
import subprocess
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.config import CFG, WalkForwardConfig
from src.data.leakage_checks import audit_sample
from src.data.massive_loader import available_minute_dates, load_minute_day
from src.data.universe_builder import load_universe_panel
from src.selector.daily_trade_selector import select_for_date
from src.utils.time_utils import ts_et

SMOKE_GRID = [
    {"decision_time": "09:45", "gap_min": 0.01, "gap_max": 0.30, "rvol_min": 1.5, "require_orb": False},
    {"decision_time": "09:45", "gap_min": 0.02, "gap_max": 0.30, "rvol_min": 1.5, "require_orb": False},
    {"decision_time": "10:00", "gap_min": 0.01, "gap_max": 0.30, "rvol_min": 1.5, "require_orb": False},
    {"decision_time": "10:00", "gap_min": 0.02, "gap_max": 0.30, "rvol_min": 2.5, "require_orb": False},
    {"decision_time": "10:00", "gap_min": 0.01, "gap_max": 0.30, "rvol_min": 1.5, "require_orb": True},
]
SMOKE_WF = WalkForwardConfig(train_months=2, val_months=1, test_months=1,
                             step_months=1, embargo_days=2)
PROV = "SYNTHETIC random-walk data (scripts/make_synthetic_data.py) — plumbing check only"


def main():
    if not list(CFG.paths.minute.glob("*.parquet")):
        print(">> generating synthetic data ...")
        subprocess.run([sys.executable, "scripts/make_synthetic_data.py"], check=True)

    panel = load_universe_panel()
    dates = available_minute_dates()
    print(f">> {len(dates)} synthetic trading days available")

    print(">> leakage audit (future-invariance) ...")
    d = dates[len(dates) // 2]
    mdf = load_minute_day(d)
    audit = audit_sample(mdf, panel, d, [ts_et(d.date(), h) for h in ("09:45", "13:00")],
                         max_tickers=10)
    assert audit["clean"], f"LEAKAGE DETECTED: {audit['leaks']}"
    print("   clean:", audit["clean"])

    print(">> research cycle on synthetic data (small grid) ...")
    from scripts.run_research import run
    out = run(param_grid=SMOKE_GRID, wf=SMOKE_WF, provenance=PROV,
              minute_loader=load_minute_day, dates=dates, panel=panel)

    print(">> daily selector demo (last 2 days, trained on prior data only) ...")
    from src.research.strategy_search import GapRvolStrategy
    train = dates[-45:-2]
    strat = GapRvolStrategy(**SMOKE_GRID[0]).fit(train, panel, load_minute_day)
    for dd in dates[-2:]:
        pick = select_for_date(strat, dd, panel, load_minute_day, write=True)
        tag = ("no candidates" if pick.no_candidates
               else ("FORCED" if pick.forced else "normal"))
        print(f"   {pick.date}: {tag}"
              + (f" -> {pick.selected['ticker']}" if pick.selected else ""))

    verdict = out.get("verdict", {})
    print("\n>> SMOKE RESULT: pipeline ran end-to-end.")
    print(">> edge gate on synthetic data:",
          "edge_candidate=", verdict.get("edge_candidate"),
          "(False is the CORRECT outcome on random data)")
    if verdict.get("edge_candidate"):
        print("!! FALSE POSITIVE on random data — investigate the gate.")
        sys.exit(2)


if __name__ == "__main__":
    main()
