"""DailyOrchestrator: glue Stages 1/2/3 into one paper-trading day.

Flow:
    1. Evaluate the kill switch using the equity log + (optional) VIX series.
       If tripped, skip new entries; existing positions still resolve normally.
    2. Run the Stage 1 scanner over the day's universe.
    3. Build OR setups for every gap candidate.
    4. Optionally classify catalysts (Stage 2) and apply the catalyst gate.
    5. Optionally score with the Stage 3 ML model and apply the P(win) gate.
    6. Hand surviving setups to the PaperBroker.
    7. Append realised positions to the ledger and update equity.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, time
from typing import Sequence

import pandas as pd

from halal_gap.backtest.engine import DailyBars, _atr_on, _first_5min_volume_history
from halal_gap.catalyst.models import ClassifierResult
from halal_gap.catalyst.pipeline import passes_catalyst_gate
from halal_gap.execution.broker import FilledPosition, PaperBroker
from halal_gap.execution.kill_switch import KillSwitch, KillSwitchDecision
from halal_gap.execution.ledger import (
    append_equity,
    append_trades,
    load_equity,
)
from halal_gap.features.builder import build_feature_row, feature_columns
from halal_gap.model.predictor import ScoringModel
from halal_gap.scanner.gap_scanner import (
    GapCandidate,
    aggregate_premarket_history,
    previous_sessions,
    scan_one,
)
from halal_gap.strategy.orb import (
    OpeningRange,
    TradeResult,
    TradeSetup,
    build_setup,
    opening_range,
    rank_and_select,
)
from halal_gap.universe.builder import UniverseRow
from halal_gap.utils.config import settings
from halal_gap.utils.logging import log
from halal_gap.utils.time_helpers import at_ny


@dataclass(slots=True)
class DayReport:
    """Per-day outcome bundle returned to the caller."""

    date: date
    kill: KillSwitchDecision
    candidates: list[GapCandidate]
    setups: list[TradeSetup]
    accepted_setups: list[TradeSetup]
    trade_results: list[TradeResult]
    filled_positions: list[FilledPosition]
    nav: float
    reasons_rejected: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class DailyOrchestrator:
    """End-to-end paper-trading driver, parameterised for ablations."""

    broker: PaperBroker
    kill_switch: KillSwitch = field(default_factory=KillSwitch.from_config)
    scoring_model: ScoringModel | None = None
    win_threshold: float | None = None
    use_catalyst_gate: bool = False
    persist: bool = True

    # ---- public entrypoint --------------------------------------------------

    def run(
        self,
        d: date,
        universe: list[UniverseRow],
        bars_by_symbol: dict[str, DailyBars],
        *,
        catalysts: dict[str, ClassifierResult] | None = None,
        vix_closes: Sequence[float] = (),
    ) -> DayReport:
        """Run one paper-trading day end-to-end."""
        kill = self.kill_switch.evaluate(
            equity=load_equity() if self.persist else pd.DataFrame(),
            vix_closes=vix_closes,
        )

        candidates = self._scan(d, universe, bars_by_symbol)
        setups, rejected = self._build_setups(d, candidates, bars_by_symbol)

        if self.use_catalyst_gate:
            setups, cat_rejected = self._apply_catalyst_gate(setups, catalysts or {})
            rejected.update(cat_rejected)
        if self.scoring_model is not None:
            setups, ml_rejected = self._apply_ml_gate(setups, candidates, bars_by_symbol, catalysts)
            rejected.update(ml_rejected)

        if kill.tripped:
            log.warning(f"{d}: kill switch tripped ({kill.reason}); skipping entries")
            accepted: list[TradeSetup] = []
            results: list[TradeResult] = []
        else:
            accepted = rank_and_select(setups)
            intraday_only = {s: b.intraday for s, b in bars_by_symbol.items()}
            results = self.broker.run_day(accepted, intraday_only)

        positions = self.broker.positions[-len(results):] if results else []
        # Persist outside the broker so we control test isolation.
        if self.persist:
            if positions:
                append_trades(positions)
            append_equity(d, self.broker.nav)

        return DayReport(
            date=d,
            kill=kill,
            candidates=candidates,
            setups=setups,
            accepted_setups=accepted,
            trade_results=results,
            filled_positions=positions,
            nav=self.broker.nav,
            reasons_rejected=rejected,
        )

    # ---- internal helpers ---------------------------------------------------

    def _scan(
        self,
        d: date,
        universe: list[UniverseRow],
        bars_by_symbol: dict[str, DailyBars],
    ) -> list[GapCandidate]:
        cfg = settings()["scanner"]
        as_of_pre = at_ny(d, time(9, 25))
        out: list[GapCandidate] = []
        for row in universe:
            bundle = bars_by_symbol.get(row.symbol)
            if bundle is None or bundle.intraday.empty:
                continue
            hist_sessions = previous_sessions(d, cfg["premarket_rvol_lookback_days"])
            hist_dv = aggregate_premarket_history(bundle.intraday, hist_sessions)
            cand = scan_one(row, bundle.intraday, hist_dv, as_of_ts=as_of_pre)
            if cand is not None:
                out.append(cand)
        return out

    def _build_setups(
        self,
        d: date,
        candidates: list[GapCandidate],
        bars_by_symbol: dict[str, DailyBars],
    ) -> tuple[list[TradeSetup], dict[str, str]]:
        cfg_uni = settings()["universe"]
        setups: list[TradeSetup] = []
        rejected: dict[str, str] = {}
        for cand in candidates:
            bundle = bars_by_symbol[cand.symbol]
            atr_val = _atr_on(bundle.daily, d, cfg_uni["atr_window"])
            if atr_val <= 0:
                rejected[cand.symbol] = "no_atr"
                continue
            hist_5min = _first_5min_volume_history(
                bundle.intraday, previous_sessions(d, 14)
            )
            opening = opening_range(cand, bundle.intraday, hist_5min)
            if opening is None:
                rejected[cand.symbol] = "no_5min_candle"
                continue
            setup = build_setup(cand, opening, atr_val)
            if setup is None:
                rejected[cand.symbol] = f"setup_rejected(dir={opening.direction})"
                continue
            # Stash the opening range so downstream gates can use it.
            setup_with_or = (setup, opening)
            setups.append(setup)
            # Stuff the opening range into a side-dict via the candidate id for ML.
            self._or_by_symbol[setup.symbol] = opening
        return setups, rejected

    def _apply_catalyst_gate(
        self,
        setups: list[TradeSetup],
        catalysts: dict[str, ClassifierResult],
    ) -> tuple[list[TradeSetup], dict[str, str]]:
        passed: list[TradeSetup] = []
        rejected: dict[str, str] = {}
        for s in setups:
            r = catalysts.get(s.symbol)
            if r is None:
                rejected[s.symbol] = "catalyst_missing"
                continue
            if not passes_catalyst_gate(r):
                rejected[s.symbol] = f"catalyst_fail({r.output.direction}/{r.output.strength})"
                continue
            passed.append(s)
        return passed, rejected

    def _apply_ml_gate(
        self,
        setups: list[TradeSetup],
        candidates: list[GapCandidate],
        bars_by_symbol: dict[str, DailyBars],
        catalysts: dict[str, ClassifierResult] | None,
    ) -> tuple[list[TradeSetup], dict[str, str]]:
        if not setups or self.scoring_model is None:
            return setups, {}
        rows: list[dict[str, object]] = []
        for s in setups:
            opening = self._or_by_symbol.get(s.symbol)
            if opening is None:
                continue
            cat = (catalysts or {}).get(s.symbol)
            rows.append(build_feature_row(s, opening, catalyst=cat))
        if not rows:
            return setups, {}
        X = pd.DataFrame(rows)[feature_columns()]
        proba = self.scoring_model.predict_proba(X)
        thr = self.win_threshold if self.win_threshold is not None else settings()["ml"]["win_probability_threshold"]
        passed: list[TradeSetup] = []
        rejected: dict[str, str] = {}
        for s, p in zip(setups, proba, strict=True):
            if p >= thr:
                passed.append(s)
            else:
                rejected[s.symbol] = f"ml_below_threshold(p={p:.2f}<{thr:.2f})"
        return passed, rejected

    # The orchestrator stashes opening ranges by symbol so the ML gate can
    # reuse them without recomputing.
    _or_by_symbol: dict[str, OpeningRange] = field(default_factory=dict, init=False)
