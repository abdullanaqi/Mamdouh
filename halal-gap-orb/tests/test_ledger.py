"""Ledger persistence tests with isolated tmp paths via monkeypatching."""
from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from halal_gap.execution import ledger
from halal_gap.execution.broker import FilledPosition

NY = ZoneInfo("America/New_York")


@pytest.fixture
def isolated_ledger(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect the ledger to a temp directory for one test."""
    monkeypatch.setattr(ledger, "_trades_path", lambda: tmp_path / "paper_trades.parquet")
    monkeypatch.setattr(ledger, "_equity_path", lambda: tmp_path / "paper_trades_equity.parquet")
    return tmp_path


def _pos(symbol: str = "X", pnl: float = 100.0) -> FilledPosition:
    return FilledPosition(
        symbol=symbol, as_of=date(2024, 1, 22),
        entry_time=datetime(2024, 1, 22, 9, 35, tzinfo=NY),
        entry_price=100.0,
        exit_time=datetime(2024, 1, 22, 15, 55, tzinfo=NY),
        exit_price=101.0,
        exit_reason="eod",
        shares=100, pnl_dollars=pnl, pnl_r=1.0,
    )


def test_append_trades_creates_file(isolated_ledger: Path) -> None:
    p = ledger.append_trades([_pos("A"), _pos("B")])
    assert p.exists()
    df = pd.read_parquet(p)
    assert len(df) == 2
    assert set(df["symbol"]) == {"A", "B"}


def test_append_trades_is_append(isolated_ledger: Path) -> None:
    ledger.append_trades([_pos("A")])
    ledger.append_trades([_pos("B")])
    df = ledger.load_trades()
    assert len(df) == 2


def test_append_trades_empty_is_noop(isolated_ledger: Path) -> None:
    p = ledger.append_trades([])
    assert not p.exists()


def test_append_equity_upserts_today(isolated_ledger: Path) -> None:
    d = date(2024, 1, 22)
    ledger.append_equity(d, 100_000.0)
    ledger.append_equity(d, 101_000.0)  # same day -> replace, not duplicate
    eq = ledger.load_equity()
    assert len(eq) == 1
    assert eq.iloc[0]["equity"] == 101_000.0


def test_daily_summary_aggregates_correctly(isolated_ledger: Path) -> None:
    ledger.append_trades(
        [
            _pos("A", pnl=150.0),
            _pos("B", pnl=-50.0),
            _pos("C", pnl=75.0),
        ]
    )
    s = ledger.daily_summary()
    assert len(s) == 1
    row = s.iloc[0]
    assert row["trades"] == 3
    assert row["wins"] == 2
    assert row["pnl_dollars"] == pytest.approx(175.0)
    assert row["hit_rate"] == pytest.approx(2 / 3)
