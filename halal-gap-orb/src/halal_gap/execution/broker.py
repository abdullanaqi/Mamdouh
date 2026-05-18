"""Paper broker: NAV + position tracking for end-to-day simulation.

The broker accepts pre-computed `TradeSetup`s, runs them through
`strategy.orb.simulate_trade` against the supplied intraday bars, and
aggregates the resulting `TradeResult`s into NAV updates and a position
ledger. It is intentionally NOT a streaming/tick-by-tick simulator —
Stage 4 only requires end-of-day reconciliation for the paper loop.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Protocol

import pandas as pd

from halal_gap.backtest.engine import _trade_to_row
from halal_gap.strategy.orb import TradeResult, TradeSetup, simulate_trade
from halal_gap.utils.config import settings
from halal_gap.utils.logging import log


@dataclass(slots=True, frozen=True)
class FilledPosition:
    """Realised position record persisted to the ledger after EOD reconciliation."""

    symbol: str
    as_of: date
    entry_time: datetime | None
    entry_price: float | None
    exit_time: datetime | None
    exit_price: float | None
    exit_reason: str
    shares: int
    pnl_dollars: float
    pnl_r: float


class Broker(Protocol):
    """Abstract broker boundary so live brokers (Alpaca/IBKR) can drop in later."""

    def run_day(
        self,
        setups: list[TradeSetup],
        bars_by_symbol: dict[str, pd.DataFrame],
    ) -> list[TradeResult]:
        ...

    @property
    def nav(self) -> float: ...

    @property
    def positions(self) -> list[FilledPosition]: ...


@dataclass(slots=True)
class PaperBroker:
    """Paper broker that resolves a day's trades end-of-bar via simulate_trade.

    Hard constraints from config:
      - long-only (halal)
      - max_concurrent_positions caps how many setups we run per day
      - max_leverage and risk_per_trade enforced inside simulate_trade
    """

    starting_nav: float
    _nav: float = field(init=False)
    _positions: list[FilledPosition] = field(default_factory=list, init=False)

    def __post_init__(self) -> None:
        self._nav = float(self.starting_nav)

    @property
    def nav(self) -> float:
        return self._nav

    @property
    def positions(self) -> list[FilledPosition]:
        return list(self._positions)

    def run_day(
        self,
        setups: list[TradeSetup],
        bars_by_symbol: dict[str, pd.DataFrame],
    ) -> list[TradeResult]:
        """Run the day's setups serially. Returns each setup's TradeResult."""
        cfg = settings()["risk"]
        cap = int(cfg["max_concurrent_positions"])
        selected = setups[:cap]
        day_pnl = 0.0
        results: list[TradeResult] = []
        for setup in selected:
            bars = bars_by_symbol.get(setup.symbol)
            if bars is None or bars.empty:
                log.warning(f"no bars for {setup.symbol} on {setup.as_of}, skipping")
                continue
            result = simulate_trade(setup, bars, nav=self._nav)
            results.append(result)
            day_pnl += result.pnl_dollars
            if result.filled:
                self._positions.append(
                    FilledPosition(
                        symbol=setup.symbol,
                        as_of=setup.as_of,
                        entry_time=result.entry_time,
                        entry_price=result.entry_price,
                        exit_time=result.exit_time,
                        exit_price=result.exit_price,
                        exit_reason=result.exit_reason,
                        shares=result.shares,
                        pnl_dollars=result.pnl_dollars,
                        pnl_r=result.pnl_r,
                    )
                )
        self._nav += day_pnl
        log.info(f"paper broker: day_pnl={day_pnl:.2f} nav={self._nav:.2f}")
        return results


def results_to_frame(results: list[TradeResult]) -> pd.DataFrame:
    """Convert TradeResult objects into the canonical trade-row DataFrame."""
    return pd.DataFrame([_trade_to_row(r) for r in results])
