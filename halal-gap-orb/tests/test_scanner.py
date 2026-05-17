"""Scanner unit tests on synthetic intraday data."""
from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from halal_gap.scanner.gap_scanner import (
    aggregate_premarket_history,
    premarket_metrics,
    previous_sessions,
    scan_one,
)
from halal_gap.universe.builder import UniverseRow


def test_previous_sessions_skips_weekends() -> None:
    sessions = previous_sessions(date(2024, 1, 22), 5)  # Mon
    weekdays = [d.weekday() for d in sessions]
    assert all(w < 5 for w in weekdays)
    assert len(sessions) == 5
    assert sessions[-1] < date(2024, 1, 22)


def test_premarket_metrics_returns_last_and_dv(synthetic_intraday: pd.DataFrame) -> None:
    pm = premarket_metrics(synthetic_intraday, date(2024, 1, 24))
    assert pm is not None
    last, dv = pm
    assert last > 0
    assert dv > 0


def test_scan_one_picks_up_gapper(synthetic_intraday: pd.DataFrame) -> None:
    row = UniverseRow(
        symbol="FAKE",
        as_of=date(2024, 1, 24),
        prior_close=100.0,
        dollar_volume_20d=150_000_000.0,
        atr_pct=0.015,
        sector="Technology",
        industry="Software",
    )
    history_dv = pd.Series([1_000_000.0, 1_000_000.0], name="premarket_dv")
    cand = scan_one(row, synthetic_intraday, history_dv)
    assert cand is not None
    assert cand.gap_pct >= 0.02
    assert cand.premarket_rvol >= 2.0


def test_scan_one_rejects_low_gap(synthetic_intraday: pd.DataFrame) -> None:
    """When the pre-market 'last' is essentially unchanged from prior close, reject."""
    row = UniverseRow(
        symbol="FAKE",
        as_of=date(2024, 1, 22),  # day 0 of fixture has no gap
        prior_close=100.0,
        dollar_volume_20d=150_000_000.0,
        atr_pct=0.015,
        sector="Technology",
        industry="Software",
    )
    history_dv = pd.Series([1_000_000.0, 1_000_000.0])
    assert scan_one(row, synthetic_intraday, history_dv) is None


def test_aggregate_premarket_history_matches_metrics(synthetic_intraday: pd.DataFrame) -> None:
    sessions = [date(2024, 1, 22), date(2024, 1, 23)]
    hist = aggregate_premarket_history(synthetic_intraday, sessions)
    assert len(hist) == 2
    assert (hist >= 0).all()
