"""PaperBroker tests: NAV updates, max-concurrent cap, ledger round-trip."""
from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from halal_gap.execution.broker import PaperBroker, results_to_frame
from halal_gap.scanner.gap_scanner import GapCandidate
from halal_gap.strategy.orb import TradeSetup


def _setup(symbol: str = "FAKE") -> TradeSetup:
    cand = GapCandidate(
        symbol=symbol, as_of=date(2024, 1, 24), prior_close=100.0,
        premarket_last=103.0, gap_pct=0.03,
        premarket_dollar_volume=20_000_000.0, premarket_rvol=5.0,
    )
    return TradeSetup(
        symbol=symbol, as_of=date(2024, 1, 24),
        entry_stop=105.0, initial_stop=104.0, risk_per_share=1.0,
        atr_value=2.0, rvol_5min=6.0, direction="long", candidate=cand,
    )


def test_paper_broker_starts_at_supplied_nav() -> None:
    b = PaperBroker(starting_nav=50_000.0)
    assert b.nav == 50_000.0
    assert b.positions == []


def test_paper_broker_handles_missing_bars(synthetic_intraday: pd.DataFrame) -> None:
    b = PaperBroker(starting_nav=100_000.0)
    # No bars for FAKE -> skip but don't crash, NAV unchanged.
    results = b.run_day([_setup("FAKE")], {})
    assert results == []
    assert b.nav == 100_000.0


def test_paper_broker_processes_setup(synthetic_intraday: pd.DataFrame) -> None:
    b = PaperBroker(starting_nav=100_000.0)
    results = b.run_day([_setup("FAKE")], {"FAKE": synthetic_intraday})
    assert len(results) == 1
    # NAV should have moved by exactly the trade's pnl_dollars.
    assert b.nav == pytest.approx(100_000.0 + results[0].pnl_dollars)
    if results[0].filled:
        assert len(b.positions) == 1
        assert b.positions[0].symbol == "FAKE"


def test_paper_broker_caps_concurrent_setups(synthetic_intraday: pd.DataFrame) -> None:
    """max_concurrent_positions from config caps how many setups run."""
    b = PaperBroker(starting_nav=100_000.0)
    bars = {f"SYM{i}": synthetic_intraday for i in range(10)}
    setups = [_setup(f"SYM{i}") for i in range(10)]
    results = b.run_day(setups, bars)
    # Default cap is 5 in settings.yaml
    assert len(results) <= 5


def test_results_to_frame_columns(synthetic_intraday: pd.DataFrame) -> None:
    b = PaperBroker(starting_nav=100_000.0)
    results = b.run_day([_setup("FAKE")], {"FAKE": synthetic_intraday})
    df = results_to_frame(results)
    for col in ("symbol", "as_of", "filled", "pnl_dollars", "pnl_r"):
        assert col in df.columns
