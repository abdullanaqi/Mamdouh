"""Append-only paper trade ledger persisted to parquet.

One row per realised position; the daily NAV log is a separate parquet so
the equity curve and the trade history can be reasoned about independently.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd

from halal_gap.execution.broker import FilledPosition
from halal_gap.utils.config import REPO_ROOT, settings
from halal_gap.utils.logging import log


def _trades_path() -> Path:
    return REPO_ROOT / settings()["paths"]["paper_trades"]


def _equity_path() -> Path:
    p = _trades_path()
    return p.with_name(p.stem + "_equity.parquet")


def _to_row(p: FilledPosition) -> dict[str, object]:
    return {
        "symbol": p.symbol,
        "as_of": pd.Timestamp(p.as_of),
        "entry_time": p.entry_time,
        "entry_price": p.entry_price,
        "exit_time": p.exit_time,
        "exit_price": p.exit_price,
        "exit_reason": p.exit_reason,
        "shares": p.shares,
        "pnl_dollars": p.pnl_dollars,
        "pnl_r": p.pnl_r,
    }


def append_trades(positions: list[FilledPosition]) -> Path:
    """Persist a batch of FilledPositions to the ledger parquet.

    Existing rows are preserved; this is an append, not a rewrite.
    """
    if not positions:
        return _trades_path()
    new = pd.DataFrame([_to_row(p) for p in positions])
    path = _trades_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        old = pd.read_parquet(path)
        new = pd.concat([old, new], ignore_index=True)
    new.to_parquet(path, index=False)
    log.info(f"ledger: appended {len(positions)} rows -> {path}")
    return path


def append_equity(d: date, nav: float) -> Path:
    """Append today's NAV to the equity log."""
    path = _equity_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    row = pd.DataFrame([{"date": pd.Timestamp(d), "equity": float(nav)}])
    if path.exists():
        old = pd.read_parquet(path)
        # Replace today's row if it already exists, otherwise append.
        old = old[old["date"] != pd.Timestamp(d)]
        row = pd.concat([old, row], ignore_index=True)
    row.to_parquet(path, index=False)
    return path


def load_trades() -> pd.DataFrame:
    """Return the entire trade ledger (empty DataFrame if absent)."""
    p = _trades_path()
    return pd.read_parquet(p) if p.exists() else pd.DataFrame()


def load_equity() -> pd.DataFrame:
    """Return the entire equity log (empty DataFrame if absent)."""
    p = _equity_path()
    return pd.read_parquet(p) if p.exists() else pd.DataFrame()


def daily_summary() -> pd.DataFrame:
    """Per-day P&L, trade count, and hit rate from the trade ledger."""
    trades = load_trades()
    if trades.empty:
        return pd.DataFrame(columns=["date", "trades", "wins", "pnl_dollars", "hit_rate"])
    df = trades.copy()
    df["date"] = pd.to_datetime(df["as_of"]).dt.date
    out = (
        df.groupby("date")
        .agg(
            trades=("symbol", "count"),
            wins=("pnl_dollars", lambda s: int((s > 0).sum())),
            pnl_dollars=("pnl_dollars", "sum"),
        )
        .reset_index()
    )
    out["hit_rate"] = out["wins"] / out["trades"].replace(0, pd.NA)
    return out
