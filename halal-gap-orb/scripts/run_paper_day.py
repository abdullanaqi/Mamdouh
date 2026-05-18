"""Run one paper-trading day end-to-end.

Fetches the daily universe and intraday bars for `--date`, optionally loads
cached catalysts and the Stage 3 scoring model, runs the orchestrator
(kill switch + scan + setups + gates + paper broker), and appends to the
on-disk ledger.

Usage:
    export FMP_API_KEY=...
    uv run python scripts/run_paper_day.py --date 2024-01-22 \
        [--use-catalyst] [--scoring-model models/scoring_model.pkl]
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import date
from pathlib import Path

import pandas as pd

from halal_gap.backtest.engine import DailyBars
from halal_gap.backtest.runner import _bars_for_symbol, _build_universe_for_period
from halal_gap.data.fmp_client import FMPClient
from halal_gap.execution import DailyOrchestrator, PaperBroker
from halal_gap.model.predictor import ScoringModel
from halal_gap.utils.config import settings
from halal_gap.utils.logging import log


async def _load_catalysts(path: Path | None, target: date) -> dict | None:
    if path is None or not path.exists():
        return None
    df = pd.read_parquet(path)
    df = df[pd.to_datetime(df["as_of"]).dt.date == target]
    if df.empty:
        return {}
    from halal_gap.catalyst.models import ClassifierOutput, ClassifierResult
    out: dict = {}
    for _, r in df.iterrows():
        out[str(r["symbol"])] = ClassifierResult(
            symbol=str(r["symbol"]),
            as_of=pd.Timestamp(r["as_of"]).to_pydatetime(),
            prompt_version=str(r.get("prompt_version", "v1")),
            item_count=int(r.get("item_count", 0) or 0),
            output=ClassifierOutput(
                direction=str(r["direction"]),
                strength=int(r["strength"]),
                confidence=float(r["confidence"]),
                catalyst_type=str(r["catalyst_type"]),
                summary=str(r.get("summary", ""))[:400],
            ),
        )
    return out


async def main_async(args: argparse.Namespace) -> None:
    target = date.fromisoformat(args.date)
    async with FMPClient() as fmp:
        universe_by_day = await _build_universe_for_period(fmp, [target])
        symbols = [r.symbol for r in universe_by_day.get(target, [])]
        log.info(f"universe[{target}]: {len(symbols)} symbols")
        bars: dict[str, DailyBars] = {}
        for sym in symbols:
            try:
                bars[sym] = await _bars_for_symbol(fmp, sym, target, target)
            except Exception as exc:  # noqa: BLE001
                log.warning(f"bars failed for {sym}: {exc}")

    catalysts = await _load_catalysts(args.catalysts, target) if args.use_catalyst else None
    scoring = ScoringModel.load(args.scoring_model) if args.scoring_model else None

    orch = DailyOrchestrator(
        broker=PaperBroker(starting_nav=float(args.nav)),
        scoring_model=scoring,
        use_catalyst_gate=args.use_catalyst,
        win_threshold=args.win_threshold,
    )
    report = orch.run(target, universe_by_day.get(target, []), bars, catalysts=catalysts)
    log.info(
        f"day report {target}: kill={report.kill.reason} "
        f"candidates={len(report.candidates)} accepted={len(report.accepted_setups)} "
        f"filled={len(report.filled_positions)} nav={report.nav:.2f}"
    )


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--date", required=True, help="YYYY-MM-DD trading date")
    p.add_argument("--nav", type=float, default=settings()["risk"]["nav"])
    p.add_argument("--use-catalyst", action="store_true",
                   help="Apply Stage 2 catalyst gate")
    p.add_argument("--catalysts", type=Path, default=None,
                   help="Parquet of pre-classified catalysts (defaults reports/stage2_catalysts.parquet)")
    p.add_argument("--scoring-model", type=Path, default=None,
                   help="Path to a pickled ScoringModel for the ML gate")
    p.add_argument("--win-threshold", type=float, default=None,
                   help="Override ml.win_probability_threshold")
    args = p.parse_args()
    if args.use_catalyst and args.catalysts is None:
        args.catalysts = Path("reports/stage2_catalysts.parquet")
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
