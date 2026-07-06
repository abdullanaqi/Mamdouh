"""Performance metrics on a trades DataFrame.

Expected columns: date, net_ret, gross_ret, pnl_usd, exit_reason,
entry_ts, ticker, entry_price (+ optional feature columns for buckets).

Daily series construction: one-trade-per-day systems are evaluated on a
calendar of ALL trading days in the period; days without a trade
contribute 0. This keeps Sharpe honest for filtered strategies.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def _drawdown(cum: pd.Series) -> float:
    peak = cum.cummax()
    dd = cum / peak - 1.0
    return float(dd.min()) if len(dd) else 0.0


def daily_return_series(trades: pd.DataFrame, all_days: pd.DatetimeIndex | None = None) -> pd.Series:
    t = trades[trades["exit_reason"] != "no_fill"]
    by_day = t.groupby(t["date"].dt.normalize())["net_ret"].sum()
    if all_days is None:
        all_days = pd.DatetimeIndex(sorted(by_day.index))
    return by_day.reindex(all_days.normalize(), fill_value=0.0)


def summarize(trades: pd.DataFrame, all_days: pd.DatetimeIndex | None = None) -> dict:
    t = trades[trades["exit_reason"] != "no_fill"].copy()
    n = len(t)
    if n == 0:
        return {"total_trades": 0, "note": "no filled trades"}
    wins = t[t["net_ret"] > 0]
    losses = t[t["net_ret"] <= 0]
    win_rate = len(wins) / n
    avg_win = float(wins["net_ret"].mean()) if len(wins) else 0.0
    avg_loss = float(losses["net_ret"].mean()) if len(losses) else 0.0
    gross_win = float(wins["net_ret"].sum())
    gross_loss = float(-losses["net_ret"].sum())
    pf = gross_win / gross_loss if gross_loss > 0 else float("inf")
    expectancy = float(t["net_ret"].mean())

    dr = daily_return_series(t, all_days)
    cum = (1 + dr).cumprod()
    ann = np.sqrt(252)
    sharpe = float(dr.mean() / dr.std() * ann) if dr.std() > 0 else 0.0
    downside = dr[dr < 0]
    sortino = float(dr.mean() / downside.std() * ann) if len(downside) > 1 and downside.std() > 0 else 0.0

    weekly = dr.resample("W").sum()
    monthly = dr.resample("ME").sum()
    return {
        "total_trades": n,
        "win_rate": round(win_rate, 4),
        "avg_win": round(avg_win, 5),
        "avg_loss": round(avg_loss, 5),
        "profit_factor": round(pf, 3) if pf != float("inf") else "inf",
        "expectancy": round(expectancy, 5),
        "net_return_compounded": round(float(cum.iloc[-1] - 1.0), 4),
        "net_pnl_usd": round(float(t["pnl_usd"].sum()), 2),
        "max_drawdown": round(_drawdown(cum), 4),
        "sharpe": round(sharpe, 3),
        "sortino": round(sortino, 3),
        "worst_day": round(float(dr.min()), 4),
        "worst_week": round(float(weekly.min()), 4) if len(weekly) else 0.0,
        "worst_month": round(float(monthly.min()), 4) if len(monthly) else 0.0,
        "pct_months_positive": round(float((monthly > 0).mean()), 3) if len(monthly) else 0.0,
        "n_days": int(len(dr)),
        "n_months": int(len(monthly)),
    }


def monthly_table(trades: pd.DataFrame, all_days=None) -> pd.DataFrame:
    dr = daily_return_series(trades, all_days)
    m = dr.resample("ME").sum().to_frame("net_ret")
    m["n_trades"] = trades[trades["exit_reason"] != "no_fill"].groupby(
        trades["date"].dt.to_period("M")).size().reindex(m.index.to_period("M"), fill_value=0).to_numpy()
    return m


def bucket_breakdown(trades: pd.DataFrame, col: str, bins) -> pd.DataFrame:
    t = trades[trades["exit_reason"] != "no_fill"].copy()
    if col not in t.columns or t[col].notna().sum() == 0:
        return pd.DataFrame()
    t["bucket"] = pd.cut(t[col], bins=bins)
    g = t.groupby("bucket", observed=True)["net_ret"]
    out = pd.DataFrame({"n": g.size(), "win_rate": g.apply(lambda s: (s > 0).mean()),
                        "expectancy": g.mean()})
    return out.round(4)


def stability_checks(trades: pd.DataFrame, all_days=None) -> dict:
    """Concentration diagnostics used by the overfitting gate."""
    t = trades[trades["exit_reason"] != "no_fill"]
    if len(t) == 0:
        return {"ok": False, "reason": "no trades"}
    total = t["net_ret"].sum()
    by_ticker = t.groupby("ticker")["net_ret"].sum().sort_values(ascending=False)
    by_month = t.groupby(t["date"].dt.to_period("M"))["net_ret"].sum().sort_values(ascending=False)
    top_ticker_share = float(by_ticker.iloc[0] / total) if total > 0 and len(by_ticker) else float("nan")
    top_month_share = float(by_month.iloc[0] / total) if total > 0 and len(by_month) else float("nan")
    return {
        "top_ticker": by_ticker.index[0] if len(by_ticker) else None,
        "top_ticker_pnl_share": round(top_ticker_share, 3) if total > 0 else None,
        "top_month": str(by_month.index[0]) if len(by_month) else None,
        "top_month_pnl_share": round(top_month_share, 3) if total > 0 else None,
        "n_unique_tickers": int(t["ticker"].nunique()),
    }
