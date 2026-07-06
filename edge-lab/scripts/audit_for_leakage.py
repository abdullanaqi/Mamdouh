"""Run the leakage battery on real (or synthetic) processed data."""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.data.leakage_checks import audit_sample, suspicious_correlation_scan
from src.data.massive_loader import available_minute_dates, load_minute_day
from src.data.universe_builder import load_universe_panel
from src.reports.report_generator import write_leakage_audit
from src.research.strategy_search import build_training_frame
from src.utils.time_utils import ts_et


def main():
    panel = load_universe_panel()
    dates = available_minute_dates()
    d = dates[len(dates) // 2]
    mdf = load_minute_day(d)
    times = [ts_et(d.date(), h) for h in ("09:45", "11:00", "14:30")]
    audit = audit_sample(mdf, panel, d, times)
    tail = dates[-15:]
    frame = build_training_frame(tail, panel, ["10:00"], lambda f: True, load_minute_day)
    susp = suspicious_correlation_scan(frame, frame) if len(frame) else pd.DataFrame()
    prov = "SYNTHETIC" if (Path("data/SYNTHETIC_DATA_WARNING.md").exists()) else "REAL Massive flat files"
    write_leakage_audit(audit, susp, prov)
    print("clean:", audit["clean"], "| leaks:", audit["leaks"])
    print("suspicious features:", len(susp))
    if not audit["clean"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
