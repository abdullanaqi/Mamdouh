"""Trade execution simulator with deliberately pessimistic fill rules.

Fill conventions (all documented, all stress-tested):
- A decision made at time T uses only bars that CLOSED before T. Entry
  fills at the OPEN of the bar starting at T, plus adverse slippage.
- If both TP and SL are touched within one bar, the SL is assumed to hit
  FIRST (worst case).
- If a bar opens through the SL, the fill is the bar open (gap-through:
  you do not get your stop price), minus stop slippage.
- If a bar opens through the TP, the fill is the TP price (a resting
  limit fills at the limit or better; taking the limit is conservative).
- Time stop / end-of-day: market order at the open of the exit bar,
  minus slippage.
- Fees: flat round-trip USD from config, charged on every closed trade.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from src.config import CFG
from src.utils.time_utils import ts_et


@dataclass
class ExitSpec:
    """Composite exit: static TP/SL levels + optional dynamic behaviors."""
    tp_pct: float                 # e.g. 0.03  -> TP at entry*(1+0.03)
    sl_pct: float                 # e.g. 0.015 -> SL at entry*(1-0.015)
    time_stop: str = CFG.session.default_time_stop  # 'HH:MM' ET
    breakeven_trigger_pct: float | None = None      # move SL to entry after this gain
    trail_pct: float | None = None                  # trail SL at high*(1-trail)

    def validate(self) -> None:
        assert self.tp_pct > 0 and self.sl_pct > 0, "tp/sl must be positive fractions"


@dataclass
class TradeResult:
    ticker: str
    date: pd.Timestamp
    entry_ts: pd.Timestamp
    entry_price: float
    exit_ts: pd.Timestamp | None
    exit_price: float | None
    exit_reason: str          # tp | sl | time_stop | eod | no_fill
    shares: float
    gross_ret: float
    net_ret: float            # after slippage on both sides + fees
    pnl_usd: float
    mfe: float                # max favorable excursion (to time stop), on raw prices
    mae: float                # max adverse excursion
    tp_pct: float
    sl_pct: float
    slippage_bps_entry: float
    slippage_bps_exit: float


def _slip(price: float, bps: float, side: str) -> float:
    m = bps / 10_000.0
    return price * (1 + m) if side == "buy" else price * (1 - m)


def size_position(entry_price: float, sl_pct: float, bar_dollar_vol: float) -> float:
    s = CFG.sizing
    risk_usd = s.account_equity * s.risk_per_trade_frac
    stop_dist = entry_price * sl_pct
    if stop_dist <= 0:
        return 0.0
    shares = risk_usd / stop_dist
    shares = min(shares, s.account_equity * s.max_position_frac / entry_price)
    if bar_dollar_vol and bar_dollar_vol > 0:
        shares = min(shares, s.max_participation_of_minute_vol * bar_dollar_vol / entry_price)
    return max(0.0, float(int(shares)))


def simulate_trade(day_bars: pd.DataFrame, ticker: str, date, decision_ts: pd.Timestamp,
                   exit_spec: ExitSpec, costs=None) -> TradeResult | None:
    """day_bars: full-day minute bars for `ticker` (RTH + extended). This is
    the ONLY module (with the labeler) allowed to see post-decision data,
    because it is simulating the future, not predicting it."""
    costs = costs or CFG.costs
    exit_spec.validate()
    bars = day_bars[day_bars["ts"] >= decision_ts].reset_index(drop=True)
    if len(bars) == 0:
        return None
    entry_bar = bars.iloc[0]
    if entry_bar["ts"] != decision_ts:
        # no bar printed at the decision minute (halt / illiquidity) -> no fill
        return _no_fill(ticker, date, decision_ts, exit_spec)

    raw_entry = float(entry_bar["open"])
    bar_dvol = float(entry_bar["close"] * entry_bar["volume"])
    slip_in = costs.one_way_bps(raw_entry, order_usd=0.0, bar_dollar_vol=bar_dvol)
    entry = _slip(raw_entry, slip_in, "buy")
    shares = size_position(entry, exit_spec.sl_pct, bar_dvol)
    if shares <= 0:
        return _no_fill(ticker, date, decision_ts, exit_spec)

    tp = entry * (1 + exit_spec.tp_pct)
    sl = entry * (1 - exit_spec.sl_pct)
    ts_stop = ts_et(pd.Timestamp(date).date(), exit_spec.time_stop)

    running_high = raw_entry
    mfe = 0.0
    mae = 0.0
    exit_px = None
    exit_ts = None
    reason = "eod"
    slip_out_bps = costs.one_way_bps(raw_entry) + costs.stop_extra_slippage_bps

    for i in range(len(bars)):
        b = bars.iloc[i]
        if b["ts"] >= ts_stop:
            raw = float(b["open"])
            exit_px = _slip(raw, costs.one_way_bps(raw), "sell")
            exit_ts, reason = b["ts"], "time_stop"
            break

        o, h, l = float(b["open"]), float(b["high"]), float(b["low"])
        # dynamic SL updates use info known at bar OPEN (running_high from
        # PRIOR bars only) -- updated after processing each bar below.
        if i > 0:
            if o <= sl:  # gap through stop
                exit_px = _slip(o, slip_out_bps, "sell")
                exit_ts, reason = b["ts"], "sl"
                break
            if o >= tp:  # gap through target: limit fills at limit
                exit_px = _slip(tp, 0.0, "sell")
                exit_ts, reason = b["ts"], "tp"
                break
        hit_sl = l <= sl
        hit_tp = h >= tp
        if hit_sl:  # worst case: SL first when both touched
            exit_px = _slip(sl, costs.stop_extra_slippage_bps, "sell")
            exit_ts, reason = b["ts"], "sl"
            mae = min(mae, l / entry - 1.0)
            break
        if hit_tp:
            exit_px = _slip(tp, 0.0, "sell")
            exit_ts, reason = b["ts"], "tp"
            mfe = max(mfe, h / entry - 1.0)
            break

        mfe = max(mfe, h / entry - 1.0)
        mae = min(mae, l / entry - 1.0)
        running_high = max(running_high, h)
        # update dynamic stops AFTER the bar completes (info now known)
        if exit_spec.breakeven_trigger_pct is not None and running_high >= entry * (1 + exit_spec.breakeven_trigger_pct):
            sl = max(sl, entry)
        if exit_spec.trail_pct is not None:
            sl = max(sl, running_high * (1 - exit_spec.trail_pct))

    if exit_px is None:  # ran out of bars before time stop (early close etc.)
        last = bars.iloc[-1]
        raw = float(last["close"])
        exit_px = _slip(raw, costs.one_way_bps(raw), "sell")
        exit_ts, reason = last["ts"], "eod"

    gross = exit_px / entry - 1.0
    fees = costs.fee_round_trip_usd
    pnl = (exit_px - entry) * shares - fees
    net = gross - (fees / (entry * shares) if shares > 0 else 0.0)
    return TradeResult(
        ticker=ticker, date=pd.Timestamp(date), entry_ts=decision_ts,
        entry_price=entry, exit_ts=exit_ts, exit_price=exit_px, exit_reason=reason,
        shares=shares, gross_ret=gross, net_ret=net, pnl_usd=pnl,
        mfe=mfe, mae=mae, tp_pct=exit_spec.tp_pct, sl_pct=exit_spec.sl_pct,
        slippage_bps_entry=slip_in, slippage_bps_exit=slip_out_bps,
    )


def _no_fill(ticker, date, decision_ts, exit_spec) -> TradeResult:
    return TradeResult(
        ticker=ticker, date=pd.Timestamp(date), entry_ts=decision_ts,
        entry_price=float("nan"), exit_ts=None, exit_price=None, exit_reason="no_fill",
        shares=0.0, gross_ret=0.0, net_ret=0.0, pnl_usd=0.0, mfe=0.0, mae=0.0,
        tp_pct=exit_spec.tp_pct, sl_pct=exit_spec.sl_pct,
        slippage_bps_entry=0.0, slippage_bps_exit=0.0,
    )
