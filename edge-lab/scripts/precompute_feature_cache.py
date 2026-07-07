"""Precompute the disk feature cache (see src/data/feature_cache.py).

For every trading day with processed minute data, computes and persists:
  - features for ALL universe tickers at each grid decision time,
  - labeled rows (ATR reference exit) for the same,
  - full-day minute bars for every ticker that could pass ANY strategy
    gate (loose gate and ML gate, each with a safety margin).

Uses the exact same code paths as the live pipeline (_all_features /
_rows_for_day), so cached results are bit-identical to live computation.
Restartable: days whose files all exist are skipped.

Usage:
    python scripts/precompute_feature_cache.py [--start YYYY-MM-DD] [--end YYYY-MM-DD] [--workers 4]
"""
import argparse
import math
import shutil
import sys
import traceback
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

from src.data import feature_cache as fc
from src.research.strategy_search import GapRvolStrategy

DECISION_TIMES = sorted(GapRvolStrategy.PARAM_GRID["decision_time"])

_PANEL = None


def _init():
    global _PANEL
    from src.data.universe_builder import load_universe_panel
    _PANEL = load_universe_panel()


def _could_be_candidate(f: dict) -> bool:
    """Union of every strategy gate, loosened with a safety margin. Tickers
    passing this get their full-day bars cached for trade simulation."""
    g = f.get("gap_pct")
    rv = f.get("rvol")
    rv = rv if isinstance(rv, float) and not math.isnan(rv) else 0.0
    gap_ok = (g is not None and isinstance(g, float) and not math.isnan(g)
              and 0.004 <= g <= 0.52 and rv >= 0.95)        # GapRvol loose gate
    rso = f.get("ret_since_open")
    rso = rso if isinstance(rso, float) and not math.isnan(rso) else 0.0
    ml_ok = rv >= 1.1 and rso > -0.06                        # MLRanking gate
    return gap_ok or ml_ok


def one_day(d) -> str:
    from src.data.feature_builder import PointInTimeView
    from src.data.massive_loader import load_minute_day
    from src.research.strategy_search import (_all_features, _rows_for_day,
                                              clear_caches)
    d = pd.Timestamp(d)
    done = (all(fc._feat_path(d, h).exists() for h in DECISION_TIMES)
            and all(fc._rows_path(d, h).exists() for h in DECISION_TIMES)
            and fc._bars_path(d).exists())
    if done:
        return f"{d.date()} skip"
    mdf = load_minute_day(d)
    if mdf is None or len(mdf) == 0:
        return f"{d.date()} no-data"
    view = PointInTimeView.build(d, mdf, _PANEL)
    feats_by, rows_by, cand_tickers = {}, {}, set()
    for hhmm in DECISION_TIMES:
        feats = _all_features(view, _PANEL, hhmm)
        rows_by[hhmm] = _rows_for_day(view, _PANEL, hhmm)
        feats_by[hhmm] = feats
        cand_tickers |= {f["ticker"] for f in feats if _could_be_candidate(f)}
    bars = [view._full_day_unsafe(t) for t in sorted(cand_tickers)]  # noqa: SLF001
    bars_df = pd.concat(bars, ignore_index=True) if bars else pd.DataFrame()
    fc.save_day(d, feats_by, rows_by, bars_df)
    clear_caches()
    return f"{d.date()} cand={len(cand_tickers)}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default=None)
    ap.add_argument("--end", default=None)
    ap.add_argument("--workers", type=int, default=4)
    a = ap.parse_args()
    from src.data.massive_loader import available_minute_dates
    dates = available_minute_dates()
    if a.start:
        dates = [d for d in dates if d >= pd.Timestamp(a.start)]
    if a.end:
        dates = [d for d in dates if d <= pd.Timestamp(a.end)]
    print(f"{len(dates)} days, decision times {DECISION_TIMES}", flush=True)
    errors = 0
    with ProcessPoolExecutor(max_workers=a.workers, initializer=_init) as ex:
        futs = {ex.submit(one_day, d): d for d in dates}
        done = 0
        for fut in as_completed(futs):
            done += 1
            try:
                msg = fut.result()
            except Exception:
                errors += 1
                print(f"ERROR {futs[fut]}\n{traceback.format_exc()}", flush=True)
                continue
            if done % 25 == 0 or done == len(dates):
                free_gb = shutil.disk_usage("/").free / 1e9
                print(f"[{done}/{len(dates)}] {msg} | free {free_gb:.1f} GB", flush=True)
    if errors:
        print(f"FINISHED WITH {errors} ERRORS — rerun to retry failed days", flush=True)
        sys.exit(1)
    print("feature cache complete.", flush=True)


if __name__ == "__main__":
    main()
