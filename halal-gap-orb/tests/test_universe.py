"""Universe builder + survivorship-bias-free invariants."""
from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from halal_gap.data.halal_filter import HalalFilter
from halal_gap.universe.builder import sp500_as_of


def _history_df() -> pd.DataFrame:
    return pd.DataFrame(
        [
            # An add-event that happened AFTER our test as_of: must be reversed.
            {"date": "2023-06-15", "symbol": "FUTURE_ADD", "removedTicker": "FUTURE_OUT"},
            # An add-event BEFORE our as_of: leave alone.
            {"date": "2020-01-10", "symbol": "OLD_ADD", "removedTicker": "OLD_OUT"},
        ]
    )


def _current_df() -> pd.DataFrame:
    return pd.DataFrame({"symbol": ["AAPL", "MSFT", "FUTURE_ADD", "OLD_ADD"]})


def test_sp500_as_of_reverses_future_changes() -> None:
    as_of = date(2023, 1, 1)
    members = sp500_as_of(_history_df(), _current_df(), as_of)
    # FUTURE_ADD was added in June 2023 (after as_of) -> must be removed
    assert "FUTURE_ADD" not in members
    # FUTURE_OUT was the one removed in June 2023 -> must be back in the set
    assert "FUTURE_OUT" in members
    # OLD_ADD was added pre-as_of -> still in the set
    assert "OLD_ADD" in members


def test_sp500_as_of_today_matches_current() -> None:
    as_of = date(2099, 1, 1)
    members = sp500_as_of(_history_df(), _current_df(), as_of)
    assert members == {"AAPL", "MSFT", "FUTURE_ADD", "OLD_ADD"}


def test_universe_not_equal_to_current_at_historical_date() -> None:
    """Survivorship-bias guard: a historical universe must differ from today's list
    when at least one composition change has occurred since."""
    historical = sp500_as_of(_history_df(), _current_df(), date(2023, 1, 1))
    current = set(_current_df()["symbol"])
    assert historical != current, "point-in-time universe must differ from current"


def test_halal_filter_rejects_banks() -> None:
    f = HalalFilter.from_config()
    assert not f.is_halal(ticker="ZZBANK", sector="Financial Services", industry="Banks - Regional")
    assert f.is_halal(ticker="AAPL", sector="Technology", industry="Consumer Electronics")


def test_halal_filter_blacklist_overrides() -> None:
    f = HalalFilter(
        excluded_sectors=("financial services",),
        excluded_industries=("banks",),
        excluded_tickers=frozenset({"AAPL"}),
    )
    assert not f.is_halal(ticker="AAPL", sector="Technology", industry="Consumer Electronics")
