"""Performance metrics on a trade log and per-day equity curve."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(slots=True, frozen=True)
class Metrics:
    """Headline stats for a backtest run."""

    total_return: float
    cagr: float
    sharpe: float
    sortino: float
    max_drawdown: float
    hit_rate: float
    avg_win_r: float
    avg_loss_r: float
    profit_factor: float
    trades: int
    trading_days: int
    trades_per_day: float

    def as_dict(self) -> dict[str, float | int]:
        return {
            "total_return": self.total_return,
            "cagr": self.cagr,
            "sharpe": self.sharpe,
            "sortino": self.sortino,
            "max_drawdown": self.max_drawdown,
            "hit_rate": self.hit_rate,
            "avg_win_r": self.avg_win_r,
            "avg_loss_r": self.avg_loss_r,
            "profit_factor": self.profit_factor,
            "trades": self.trades,
            "trading_days": self.trading_days,
            "trades_per_day": self.trades_per_day,
        }


def compute_metrics(trades: pd.DataFrame, equity: pd.DataFrame) -> Metrics:
    """Compute headline metrics.

    `trades` requires columns: pnl_dollars, pnl_r, filled.
    `equity` requires columns: date, equity (daily mark).
    """
    filled = trades[trades["filled"]] if "filled" in trades.columns and not trades.empty else trades
    n = len(filled)
    if equity.empty:
        return Metrics(0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)

    eq = equity.copy().sort_values("date").reset_index(drop=True)
    if n == 0:
        trading_days = int(eq["date"].nunique())
        return Metrics(0, 0, 0, 0, 0, 0, 0, 0, 0, 0, trading_days, 0)
    eq["ret"] = eq["equity"].pct_change().fillna(0.0)
    total_return = float(eq["equity"].iloc[-1] / eq["equity"].iloc[0] - 1.0)

    days = max(1, (eq["date"].iloc[-1] - eq["date"].iloc[0]).days)
    years = max(1e-9, days / 365.25)
    cagr = (1 + total_return) ** (1.0 / years) - 1.0 if total_return > -1 else -1.0

    std = float(eq["ret"].std(ddof=1))
    mean = float(eq["ret"].mean())
    sharpe = (mean / std * np.sqrt(252.0)) if std > 0 else 0.0
    downside = eq.loc[eq["ret"] < 0, "ret"]
    dstd = float(downside.std(ddof=1)) if len(downside) > 1 else 0.0
    sortino = (mean / dstd * np.sqrt(252.0)) if dstd > 0 else 0.0

    cumulative = eq["equity"].cummax()
    drawdown = eq["equity"] / cumulative - 1.0
    max_dd = float(drawdown.min())

    wins = filled[filled["pnl_dollars"] > 0]
    losses = filled[filled["pnl_dollars"] <= 0]
    hit_rate = len(wins) / n
    avg_win_r = float(wins["pnl_r"].mean()) if not wins.empty else 0.0
    avg_loss_r = float(losses["pnl_r"].mean()) if not losses.empty else 0.0
    gross_win = float(wins["pnl_dollars"].sum())
    gross_loss = float(-losses["pnl_dollars"].sum())
    pf = gross_win / gross_loss if gross_loss > 0 else float("inf")

    trading_days = int(eq["date"].nunique())
    return Metrics(
        total_return=total_return,
        cagr=cagr,
        sharpe=sharpe,
        sortino=sortino,
        max_drawdown=max_dd,
        hit_rate=hit_rate,
        avg_win_r=avg_win_r,
        avg_loss_r=avg_loss_r,
        profit_factor=pf,
        trades=n,
        trading_days=trading_days,
        trades_per_day=n / trading_days if trading_days else 0.0,
    )
