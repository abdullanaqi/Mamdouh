"""Opening Range Breakout entry/exit logic (Zarattini-Barbon-Aziz 2024)."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time
from typing import Literal

import pandas as pd

from halal_gap.scanner.gap_scanner import GapCandidate, _ensure_ny_index
from halal_gap.utils.config import settings
from halal_gap.utils.indicators import ema, rvol
from halal_gap.utils.logging import log
from halal_gap.utils.time_helpers import at_ny

Direction = Literal["long", "skip"]


@dataclass(slots=True, frozen=True)
class OpeningRange:
    """The first 5-minute candle and its derived RVOL."""

    symbol: str
    as_of: date
    open: float
    high: float
    low: float
    close: float
    volume: float
    rvol_5min: float
    direction: Direction


@dataclass(slots=True, frozen=True)
class TradeSetup:
    """A fully-specified trade ready for the backtester."""

    symbol: str
    as_of: date
    entry_stop: float            # the stop-buy price (high of 5-min candle)
    initial_stop: float
    risk_per_share: float        # entry_stop - initial_stop
    atr_value: float
    rvol_5min: float
    direction: Direction
    candidate: GapCandidate


@dataclass(slots=True)
class TradeResult:
    """The realised outcome of a trade after the backtester runs it."""

    setup: TradeSetup
    filled: bool
    entry_time: datetime | None
    entry_price: float | None
    exit_time: datetime | None
    exit_price: float | None
    exit_reason: str
    shares: int
    pnl_dollars: float
    pnl_r: float
    high_water_r: float
    bars_held: int
    log: list[str] = field(default_factory=list)


def opening_range(
    candidate: GapCandidate, bars: pd.DataFrame, history_5min_vol: pd.Series
) -> OpeningRange | None:
    """Return the 9:30-9:35 candle plus rvol_5min for `candidate.as_of`.

    `bars` is 5-min OHLCV containing at least 09:30 of the as_of date.
    `history_5min_vol` is the past N-day volume of the first 5-min candle.
    """
    df = _ensure_ny_index(bars)
    target_start = at_ny(candidate.as_of, time(9, 30))
    target_end = at_ny(candidate.as_of, time(9, 35))
    mask = (df["ts"] >= target_start) & (df["ts"] < target_end)
    win = df[mask]
    if win.empty:
        return None
    row = win.iloc[0]
    o, h, lo, c, v = float(row["open"]), float(row["high"]), float(row["low"]), float(row["close"]), float(row["volume"])
    rv = rvol(v, history_5min_vol)
    if c > o:
        direction: Direction = "long"
    else:
        direction = "skip"
    return OpeningRange(
        symbol=candidate.symbol,
        as_of=candidate.as_of,
        open=o,
        high=h,
        low=lo,
        close=c,
        volume=v,
        rvol_5min=rv,
        direction=direction,
    )


def build_setup(
    candidate: GapCandidate, opening: OpeningRange, atr_value: float
) -> TradeSetup | None:
    """Apply Zarattini ORB rules to produce a tradeable setup, or None to skip."""
    cfg = settings()["strategy"]
    if not cfg["long_only"]:
        raise NotImplementedError("short side disabled by halal constraint")
    if opening.direction == "skip":
        return None
    if opening.rvol_5min != opening.rvol_5min:  # NaN
        return None
    if opening.rvol_5min < cfg["rvol_5min_min"]:
        return None

    entry_stop = opening.high
    stop_atr = entry_stop - cfg["stop_atr_multiplier"] * atr_value
    stop_low = opening.low
    stop_pct = entry_stop * (1.0 - cfg["stop_min_pct"])
    initial_stop = max(stop_atr, stop_low, stop_pct)
    risk = entry_stop - initial_stop
    if risk <= 0:
        return None
    return TradeSetup(
        symbol=candidate.symbol,
        as_of=candidate.as_of,
        entry_stop=entry_stop,
        initial_stop=initial_stop,
        risk_per_share=risk,
        atr_value=atr_value,
        rvol_5min=opening.rvol_5min,
        direction="long",
        candidate=candidate,
    )


def rank_and_select(setups: list[TradeSetup]) -> list[TradeSetup]:
    """Sort by RVOL desc and cap at `candidate_top_n`."""
    cfg = settings()["strategy"]
    ranked = sorted(setups, key=lambda s: s.rvol_5min, reverse=True)
    return ranked[: cfg["candidate_top_n"]]


def simulate_trade(
    setup: TradeSetup,
    bars: pd.DataFrame,
    *,
    nav: float,
) -> TradeResult:
    """Run the trade forward across 5-min bars until stop, target, or EOD."""
    cfg_strat = settings()["strategy"]
    cfg_risk = settings()["risk"]
    cfg_exec = settings()["execution"]

    df = _ensure_ny_index(bars)
    df = df[df["ts"] >= at_ny(setup.as_of, time(9, 35))].reset_index(drop=True)
    eod = at_ny(setup.as_of, time(*map(int, cfg_strat["eod_exit_time"].split(":"))))
    df = df[df["ts"] <= eod].reset_index(drop=True)
    if df.empty:
        return TradeResult(
            setup=setup, filled=False, entry_time=None, entry_price=None,
            exit_time=None, exit_price=None, exit_reason="no_bars",
            shares=0, pnl_dollars=0.0, pnl_r=0.0, high_water_r=0.0, bars_held=0,
        )

    risk_dollars = cfg_risk["risk_per_trade"] * nav
    raw_shares = int(risk_dollars / setup.risk_per_share) if setup.risk_per_share > 0 else 0
    max_notional = cfg_risk["max_leverage"] * nav / cfg_risk["max_concurrent_positions"]
    max_shares_by_notional = int(max_notional / max(setup.entry_stop, 1e-6))
    shares = max(0, min(raw_shares, max_shares_by_notional))
    if shares == 0:
        return TradeResult(
            setup=setup, filled=False, entry_time=None, entry_price=None,
            exit_time=None, exit_price=None, exit_reason="zero_shares",
            shares=0, pnl_dollars=0.0, pnl_r=0.0, high_water_r=0.0, bars_held=0,
        )

    entry_filled = False
    entry_price = 0.0
    entry_time: datetime | None = None
    remaining = shares
    realized_pnl = 0.0
    exit_reason = "eod"
    exit_time: datetime | None = None
    exit_price = 0.0
    stop_price = setup.initial_stop
    high_water_r = 0.0
    bars_held = 0
    closes_5min: list[float] = []
    partial_taken = False
    log_lines: list[str] = []

    for i, bar in df.iterrows():
        bars_held = i + 1 if entry_filled else 0
        ts = bar["ts"]
        b_high = float(bar["high"])
        b_low = float(bar["low"])
        b_close = float(bar["close"])

        if not entry_filled:
            if b_high >= setup.entry_stop:
                entry_price = setup.entry_stop + cfg_exec["entry_slippage_per_share"]
                entry_time = ts
                entry_filled = True
                log_lines.append(f"{ts} entry @ {entry_price:.4f} stop @ {stop_price:.4f}")
            continue

        closes_5min.append(b_close)
        # Update high-water in R
        if remaining > 0:
            move = (b_high - entry_price) / setup.risk_per_share
            high_water_r = max(high_water_r, move)

        # Stop check first
        if b_low <= stop_price:
            fill = stop_price - cfg_exec["exit_slippage_per_share"]
            exit_price = fill
            exit_time = ts
            realized_pnl += (fill - entry_price) * remaining
            remaining = 0
            exit_reason = "stop"
            log_lines.append(f"{ts} stop hit @ {fill:.4f}")
            break

        # Partial take at +2R for mode_b
        if (
            cfg_strat["exit_mode"] == "mode_b_2r_trail"
            and not partial_taken
            and remaining > 0
        ):
            partial_target = entry_price + cfg_strat["partial_take_r"] * setup.risk_per_share
            if b_high >= partial_target:
                take = int(shares * cfg_strat["partial_take_fraction"])
                if take > 0 and take < remaining:
                    fill = partial_target - cfg_exec["exit_slippage_per_share"]
                    realized_pnl += (fill - entry_price) * take
                    remaining -= take
                    partial_taken = True
                    # raise stop to breakeven on remainder
                    stop_price = max(stop_price, entry_price)
                    log_lines.append(
                        f"{ts} partial {take}sh @ {fill:.4f}, raise stop to {stop_price:.4f}"
                    )

        # Trail stop using 9-EMA of 5-min closes once we have enough samples
        if cfg_strat["exit_mode"] == "mode_b_2r_trail" and partial_taken:
            if len(closes_5min) >= cfg_strat["trail_ema_period"]:
                ema_val = float(
                    ema(pd.Series(closes_5min), cfg_strat["trail_ema_period"]).iloc[-1]
                )
                stop_price = max(stop_price, ema_val)

    if remaining > 0:
        last = df.iloc[-1]
        fill = float(last["close"]) - cfg_exec["exit_slippage_per_share"]
        exit_price = fill
        exit_time = last["ts"]
        realized_pnl += (fill - entry_price) * remaining
        if exit_reason == "eod":
            exit_reason = "eod"
        remaining = 0
        log_lines.append(f"{exit_time} EOD exit @ {fill:.4f}")

    commission = cfg_exec["commission_per_share"] * shares * 2
    realized_pnl -= commission
    pnl_r = realized_pnl / (setup.risk_per_share * shares) if shares > 0 else 0.0
    log.debug(f"{setup.symbol} {setup.as_of} pnl={realized_pnl:.2f} R={pnl_r:.2f}")
    return TradeResult(
        setup=setup,
        filled=entry_filled,
        entry_time=entry_time,
        entry_price=entry_price if entry_filled else None,
        exit_time=exit_time,
        exit_price=exit_price if entry_filled else None,
        exit_reason=exit_reason if entry_filled else "no_fill",
        shares=shares if entry_filled else 0,
        pnl_dollars=realized_pnl if entry_filled else 0.0,
        pnl_r=pnl_r if entry_filled else 0.0,
        high_water_r=high_water_r,
        bars_held=bars_held,
        log=log_lines,
    )
