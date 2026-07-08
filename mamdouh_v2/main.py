"""Orchestrates the full halal 9:35->15:55 research pipeline, Stage 1-15.

Usage:
    python main.py                  # run everything, skip stages already built
    python main.py --force          # rebuild every stage from scratch
    python main.py --stage 6        # run only stage 6
    python main.py --from-stage 9   # run stage 9 through the end
    python main.py --to-stage 8     # run stage 1 through 8 (inclusive)

Every stage function checks whether its target table already exists in
halal_research.duckdb and skips itself unless --force is passed - the
pipeline is resumable and each stage's output is independently inspectable
with a plain DuckDB client.
"""
from __future__ import annotations

import argparse
import time

import analysis
import backtest
import build_candidates
import config
import features_news
import features_price
import leakage_checks
import load_halal
import ranking_labels
import ranking_model
import report
import risk_model
import trap_analysis
from db import get_connection, row_count

_log = config.setup_logging("main")


def _stage_2(con, force):
    build_candidates.build_halal_minute_bars(con, force)
    build_candidates.build_trading_days_universe(con, force)


                                                   
STAGES = [
    (1, "Load halal tickers -> halal_universe", lambda con, f: load_halal.run(con, f)),
    (2, "Filter minute_bars -> halal_minute_bars / trading_days_universe", _stage_2),
    (3, "Extract entry/exit prices -> entry_exit_prices", lambda con, f: build_candidates.build_entry_exit_prices(con, f)),
    (4, "Basic eligibility (no liquidity filter) -> eligibility", lambda con, f: build_candidates.build_eligibility(con, f)),
    (5, "Pre-entry price/premarket features -> pre_entry_price_features", lambda con, f: features_price.build(con, f)),
    (6, "Pre-entry news features -> pre_entry_news_features", lambda con, f: features_news.build(con, f)),
    (7, "Daily return ranking labels (research only) -> ranking_labels", lambda con, f: ranking_labels.build(con, f)),
    (8, "Post-entry outcome + trap labels -> post_entry_outcome_features / trap_labels", lambda con, f: trap_analysis.build_pre_candidates(con, f)),
    (9, "Assemble final table -> halal_daily_candidates", lambda con, f: build_candidates.finalize_halal_daily_candidates(con, f)),
    (10, "Trap-pattern aggregate summaries -> trap_summary_by_*", lambda con, f: trap_analysis.build_trap_summaries(con, f)),
    (11, "Winners-vs-rest analysis -> analysis_*", lambda con, f: analysis.run(con, f)),
    (12, "Leakage validation (pre-model)", lambda con, f: leakage_checks.run_all(con)),
    (13, "Ranking model (walk-forward, pre-entry features only) -> ranking_model_scores", lambda con, f: ranking_model.build(con, f)),
    (14, "Risk/trap model (walk-forward, pre-entry features only) -> risk_model_scores", lambda con, f: risk_model.build(con, f)),
    (15, "Backtest daily selection strategies -> backtest_results", lambda con, f: backtest.run(con, f)),
    (16, "Final leakage validation (post-model)", lambda con, f: leakage_checks.run_all(con)),
    (17, "Final report -> reports/report_halal_backtest.md", lambda con, f: report.run(con, f)),
]


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--force", action="store_true", help="Rebuild every stage from scratch, ignoring existing tables")
    p.add_argument("--stage", type=int, help="Run only this stage number")
    p.add_argument("--from-stage", type=int, help="Run from this stage number to the end")
    p.add_argument("--to-stage", type=int, help="Run from stage 1 up to and including this stage number")
    return p.parse_args()


def main() -> int:
    args = parse_args()

    if args.stage is not None:
        stages_to_run = [s for s in STAGES if s[0] == args.stage]
    else:
        lo = args.from_stage or 1
        hi = args.to_stage or STAGES[-1][0]
        stages_to_run = [s for s in STAGES if lo <= s[0] <= hi]

    _log.info(f"Running {len(stages_to_run)} stage(s): {[s[0] for s in stages_to_run]}")

    con = get_connection()
    t0 = time.time()
    try:
        for num, desc, fn in stages_to_run:
            _log.info(f"=== Stage {num}: {desc} ===")
            t_stage = time.time()
            fn(con, args.force)
            _log.info(f"=== Stage {num} done in {time.time() - t_stage:.1f}s ===")

        _log.info("Pipeline summary:")
        for table in [
            "halal_universe", "halal_minute_bars", "entry_exit_prices", "eligibility",
            "pre_entry_price_features", "pre_entry_news_features", "ranking_labels",
            "post_entry_outcome_features", "trap_labels", "halal_daily_candidates",
            "trap_summary_by_ticker", "ranking_model_scores", "risk_model_scores",
            "backtest_results", "leakage_validation_report",
        ]:
            try:
                _log.info(f"  {table}: {row_count(con, table):,} rows")
            except Exception:
                _log.info(f"  {table}: not built")

    finally:
        con.close()

    _log.info(f"Total pipeline time: {time.time() - t0:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
