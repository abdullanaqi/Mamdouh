"""Backfill catalyst classifications for the candidate set produced by Stage 1.

Reads `reports/stage1_candidates.parquet`, fetches news for each
(symbol, as_of) via FMP, calls the LLM classifier with disk caching + daily
cost cap, and writes `reports/stage2_catalysts.parquet`.

Usage:
    export FMP_API_KEY=... OPENAI_API_KEY=...
    uv run python scripts/backfill_catalysts.py
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import date, datetime, time
from pathlib import Path

import pandas as pd

from halal_gap.catalyst.llm_classifier import classify
from halal_gap.catalyst.news_fetcher import fetch_news_before
from halal_gap.data.fmp_client import FMPClient
from halal_gap.utils.config import reports_dir
from halal_gap.utils.logging import log
from halal_gap.utils.time_helpers import at_ny


async def main_async(input_path: Path, output_path: Path, since: date | None) -> None:
    df = pd.read_parquet(input_path)
    if since is not None:
        df = df[pd.to_datetime(df["as_of"]).dt.date >= since]
    log.info(f"loaded {len(df)} candidates from {input_path}")

    rows: list[dict[str, object]] = []
    async with FMPClient() as fmp:
        for _, r in df.iterrows():
            sym = str(r["symbol"])
            as_of = pd.Timestamp(r["as_of"]).to_pydatetime().date()
            ts = at_ny(as_of, time(9, 25))
            gap = float(r.get("gap_pct", 0.0))
            items = await fetch_news_before(fmp, sym, ts)
            result = classify(sym, ts, gap, items)
            rows.append(
                {
                    "symbol": sym,
                    "as_of": pd.Timestamp(as_of),
                    "direction": result.output.direction,
                    "strength": result.output.strength,
                    "confidence": result.output.confidence,
                    "catalyst_type": result.output.catalyst_type,
                    "summary": result.output.summary,
                    "item_count": result.item_count,
                    "cached": result.cached,
                    "skipped_reason": result.skipped_reason,
                    "cost_usd": result.cost_usd,
                    "prompt_version": result.prompt_version,
                }
            )
            log.info(
                f"{sym} {as_of} -> {result.output.direction}/{result.output.strength} "
                f"(items={result.item_count}, cached={result.cached}, skip={result.skipped_reason})"
            )

    out = pd.DataFrame(rows)
    out.to_parquet(output_path, index=False)
    log.info(f"wrote {len(out)} rows to {output_path}")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument(
        "--input",
        type=Path,
        default=reports_dir() / "stage1_candidates.parquet",
    )
    p.add_argument(
        "--output",
        type=Path,
        default=reports_dir() / "stage2_catalysts.parquet",
    )
    p.add_argument("--since", type=lambda s: date.fromisoformat(s), default=None)
    args = p.parse_args()
    if not args.input.exists():
        raise SystemExit(
            f"{args.input} not found; run scripts/run_stage1_backtest.py first"
        )
    asyncio.run(main_async(args.input, args.output, args.since))


if __name__ == "__main__":
    main()
