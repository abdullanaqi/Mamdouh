"""Daily kill switch.

The switch is evaluated BEFORE each day's entries. A tripped switch blocks
new positions for that day; it does NOT close existing positions (those
exit through normal stop / EOD logic).

Rules from `config/settings.yaml -> kill_switch`:
  1. Consecutive losing days >= consec_losing_days
  2. Rolling drawdown (over rolling_dd_window_days) <= -rolling_dd_pct
  3. Paper-live Sharpe divergence > paper_live_sharpe_divergence
     (skipped when no live history is supplied)
  4. VIX threshold (>= vix_threshold for vix_consec_closes consecutive closes)
  5. Position correlation (>= position_correlation_max) — informational
     guard, takes a precomputed pairwise correlation matrix
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

import numpy as np
import pandas as pd

from halal_gap.utils.config import settings
from halal_gap.utils.logging import log


@dataclass(slots=True, frozen=True)
class KillSwitchDecision:
    """Result of one evaluation."""

    tripped: bool
    triggered_rules: tuple[str, ...] = ()
    detail: dict[str, float] = field(default_factory=dict)

    @property
    def reason(self) -> str:
        return ", ".join(self.triggered_rules) if self.triggered_rules else "ok"


def _equity_to_daily_returns(equity: pd.DataFrame) -> pd.Series:
    """Day-over-day return series (dropping the leading NaN)."""
    if equity.empty:
        return pd.Series(dtype=float)
    eq = equity.sort_values("date").reset_index(drop=True)
    return eq["equity"].pct_change().dropna()


def _consec_losing_days(returns: pd.Series) -> int:
    """Length of the tail run of strictly-negative returns."""
    if returns.empty:
        return 0
    streak = 0
    for r in reversed(returns.tolist()):
        if r < 0:
            streak += 1
        else:
            break
    return streak


def _rolling_drawdown(equity: pd.DataFrame, window_days: int) -> float:
    """Most-negative drawdown over the trailing `window_days` rows."""
    if equity.empty:
        return 0.0
    eq = equity.sort_values("date").reset_index(drop=True).tail(window_days)
    peak = eq["equity"].cummax()
    dd = eq["equity"] / peak - 1.0
    return float(dd.min())


def _sharpe(returns: Sequence[float]) -> float:
    arr = np.asarray(list(returns), dtype=float)
    if arr.size < 2:
        return 0.0
    std = float(arr.std(ddof=1))
    return float(arr.mean() / std * np.sqrt(252.0)) if std > 0 else 0.0


def _max_pairwise_corr(corr_matrix: pd.DataFrame | None) -> float:
    """Maximum off-diagonal absolute correlation in the matrix; 0 if empty."""
    if corr_matrix is None or corr_matrix.empty or corr_matrix.shape[0] < 2:
        return 0.0
    arr = corr_matrix.values.copy().astype(float)
    np.fill_diagonal(arr, 0.0)
    return float(np.nanmax(np.abs(arr)))


@dataclass(slots=True, frozen=True)
class KillSwitch:
    """Stateless evaluator: all state is supplied per `evaluate()` call."""

    @classmethod
    def from_config(cls) -> "KillSwitch":
        return cls()

    def evaluate(
        self,
        *,
        equity: pd.DataFrame,
        vix_closes: Sequence[float] = (),
        paper_returns: Sequence[float] = (),
        live_returns: Sequence[float] = (),
        position_corr: pd.DataFrame | None = None,
    ) -> KillSwitchDecision:
        """Apply every rule and return the union of triggered ones.

        Args:
            equity: daily NAV log with columns `date`, `equity`.
            vix_closes: recent VIX closes, oldest-first.
            paper_returns / live_returns: paired return series for divergence
                comparison. Empty `live_returns` skips the rule.
            position_corr: pairwise correlation matrix of current holdings.
        """
        cfg = settings()["kill_switch"]
        triggers: list[str] = []
        detail: dict[str, float] = {}

        # Rule 1: consec losing days
        rets = _equity_to_daily_returns(equity)
        losing = _consec_losing_days(rets)
        detail["consec_losing_days"] = float(losing)
        if losing >= int(cfg["consec_losing_days"]):
            triggers.append(f"consec_losing_days={losing}")

        # Rule 2: rolling drawdown
        dd = _rolling_drawdown(equity, int(cfg["rolling_dd_window_days"]))
        detail["rolling_dd"] = float(dd)
        if dd <= -float(cfg["rolling_dd_pct"]):
            triggers.append(f"rolling_dd={dd:.3f}")

        # Rule 3: paper-live Sharpe divergence
        if len(live_returns) >= 5 and len(paper_returns) >= 5:
            sp = _sharpe(paper_returns)
            sl = _sharpe(live_returns)
            div = abs(sp - sl)
            detail["sharpe_divergence"] = div
            if div > float(cfg["paper_live_sharpe_divergence"]):
                triggers.append(f"sharpe_divergence={div:.2f}")

        # Rule 4: VIX threshold
        n = int(cfg["vix_consec_closes"])
        vix_thr = float(cfg["vix_threshold"])
        if len(vix_closes) >= n:
            recent = list(vix_closes)[-n:]
            detail["vix_last"] = float(recent[-1])
            if all(v >= vix_thr for v in recent):
                triggers.append(f"vix>={vix_thr}_for_{n}d")

        # Rule 5: position correlation (informational)
        max_corr = _max_pairwise_corr(position_corr)
        detail["max_pairwise_corr"] = max_corr
        if max_corr >= float(cfg["position_correlation_max"]):
            triggers.append(f"position_corr={max_corr:.2f}")

        decision = KillSwitchDecision(
            tripped=bool(triggers),
            triggered_rules=tuple(triggers),
            detail=detail,
        )
        if decision.tripped:
            log.warning(f"kill switch TRIPPED: {decision.reason}")
        return decision
