"""Event-driven backtester over daily slices of 5-min bars."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta

import pandas as pd

from halal_gap.scanner.gap_scanner import (
    GapCandidate,
    aggregate_premarket_history,
    previous_sessions,
    scan_one,
)
from halal_gap.strategy.orb import (
    TradeResult,
    TradeSetup,
    build_setup,
    opening_range,
    rank_and_select,
    simulate_trade,
)
from halal_gap.universe.builder import UniverseRow
from halal_gap.utils.config import settings
from halal_gap.utils.indicators import atr
from halal_gap.utils.logging import log
from halal_gap.utils.time_helpers import at_ny


@dataclass(slots=True)
class DailyBars:
    """Bundle of intraday + daily bars for one symbol."""

    intraday: pd.DataFrame   # 5-min OHLCV, full history
    daily: pd.DataFrame      # daily OHLCV, full history


@dataclass(slots=True, frozen=True)
class BacktestArtifacts:
    """Outputs of a backtest run."""

    trades: pd.DataFrame      # one row per executed setup
    equity: pd.DataFrame      # daily mark-to-market
    candidates: pd.DataFrame  # everything the scanner ranked, accepted or not


def _first_5min_volume_history(
    intraday: pd.DataFrame, sessions: list[date]
) -> pd.Series:
    """Per-session volume of the 09:30 5-min candle."""
    from halal_gap.scanner.gap_scanner import _ensure_ny_index
    df = _ensure_ny_index(intraday)
    out: list[float] = []
    for d in sessions:
        start = at_ny(d, time(9, 30))
        end = at_ny(d, time(9, 35))
        win = df[(df["ts"] >= start) & (df["ts"] < end)]
        out.append(float(win["volume"].iloc[0]) if not win.empty else 0.0)
    return pd.Series(out, index=pd.to_datetime(sessions), name="open5_vol")


def _atr_on(daily: pd.DataFrame, as_of: date, window: int) -> float:
    df = daily.copy()
    df["date"] = pd.to_datetime(df["date"])
    df = df[df["date"].dt.date <= as_of].sort_values("date")
    if len(df) < window + 1:
        return 0.0
    return float(atr(df, window=window).iloc[-1])


def run_backtest(
    sessions: list[date],
    universe_by_day: dict[date, list[UniverseRow]],
    bars_by_symbol: dict[str, DailyBars],
    initial_nav: float | None = None,
) -> BacktestArtifacts:
    """Run the full Stage 1 backtest.

    Args:
        sessions: trading dates to test, ascending.
        universe_by_day: daily universe rows keyed by date.
        bars_by_symbol: per-symbol intraday + daily bars.
        initial_nav: starting equity; defaults to config risk.nav.
    """
    cfg_scan = settings()["scanner"]
    cfg_strat = settings()["strategy"]
    cfg_uni = settings()["universe"]
    cfg_risk = settings()["risk"]

    nav = initial_nav or cfg_risk["nav"]
    equity_rows: list[dict[str, object]] = []
    trade_rows: list[dict[str, object]] = []
    cand_rows: list[dict[str, object]] = []

    for d in sessions:
        universe = universe_by_day.get(d, [])
        if not universe:
            equity_rows.append({"date": pd.Timestamp(d), "equity": nav})
            continue

        # 9:25 ET decision time for the pre-market scan
        as_of_pre = at_ny(d, time(9, 25))
        candidates: list[GapCandidate] = []
        for row in universe:
            bundle = bars_by_symbol.get(row.symbol)
            if bundle is None or bundle.intraday.empty:
                continue
            hist_sessions = previous_sessions(
                d, cfg_scan["premarket_rvol_lookback_days"]
            )
            hist_dv = aggregate_premarket_history(bundle.intraday, hist_sessions)
            cand = scan_one(row, bundle.intraday, hist_dv, as_of_ts=as_of_pre)
            if cand is not None:
                candidates.append(cand)

        if not candidates:
            equity_rows.append({"date": pd.Timestamp(d), "equity": nav})
            continue

        cand_setups: list[TradeSetup] = []
        for cand in candidates[: cfg_scan.get("max_premarket_candidates", 50)]:
            bundle = bars_by_symbol[cand.symbol]
            atr_val = _atr_on(bundle.daily, d, cfg_uni["atr_window"])
            if atr_val <= 0:
                cand_rows.append({**asdict(cand), "rejected_reason": "no_atr"})
                continue
            hist_5min = _first_5min_volume_history(
                bundle.intraday, previous_sessions(d, 14)
            )
            opening = opening_range(cand, bundle.intraday, hist_5min)
            if opening is None:
                cand_rows.append({**asdict(cand), "rejected_reason": "no_5min_candle"})
                continue
            setup = build_setup(cand, opening, atr_val)
            if setup is None:
                cand_rows.append(
                    {
                        **asdict(cand),
                        "rejected_reason": f"setup_reject(direction={opening.direction},rvol={opening.rvol_5min:.2f})",
                    }
                )
                continue
            cand_setups.append(setup)
            cand_rows.append(
                {
                    **asdict(cand),
                    "rejected_reason": "",
                    "entry_stop": setup.entry_stop,
                    "initial_stop": setup.initial_stop,
                    "rvol_5min": setup.rvol_5min,
                }
            )

        selected = rank_and_select(cand_setups)
        day_pnl = 0.0
        for setup in selected:
            bundle = bars_by_symbol[setup.symbol]
            result: TradeResult = simulate_trade(setup, bundle.intraday, nav=nav)
            trade_rows.append(_trade_to_row(result))
            day_pnl += result.pnl_dollars

        nav += day_pnl
        equity_rows.append({"date": pd.Timestamp(d), "equity": nav})
        log.info(f"backtest[{d}] trades={len(selected)} pnl={day_pnl:.2f} nav={nav:.2f}")

    return BacktestArtifacts(
        trades=pd.DataFrame(trade_rows),
        equity=pd.DataFrame(equity_rows),
        candidates=pd.DataFrame(cand_rows),
    )


def _trade_to_row(r: TradeResult) -> dict[str, object]:
    s = r.setup
    return {
        "symbol": s.symbol,
        "as_of": pd.Timestamp(s.as_of),
        "filled": r.filled,
        "entry_time": r.entry_time,
        "entry_price": r.entry_price,
        "exit_time": r.exit_time,
        "exit_price": r.exit_price,
        "exit_reason": r.exit_reason,
        "shares": r.shares,
        "entry_stop": s.entry_stop,
        "initial_stop": s.initial_stop,
        "risk_per_share": s.risk_per_share,
        "atr_value": s.atr_value,
        "rvol_5min": s.rvol_5min,
        "premarket_rvol": s.candidate.premarket_rvol,
        "gap_pct": s.candidate.gap_pct,
        "pnl_dollars": r.pnl_dollars,
        "pnl_r": r.pnl_r,
        "high_water_r": r.high_water_r,
        "bars_held": r.bars_held,
    }
