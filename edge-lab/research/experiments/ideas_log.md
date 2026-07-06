# Ideas log

Every hypothesis, tested or not, lives here. Untested ideas carry **zero**
evidentiary weight until code walk-forward-tests them on real data.

Fields: Idea / Reason / Data needed / Test method / Result /
Accepted-or-rejected / Why.

---

## SEED-001 — Gap-up + relative-volume continuation
- **Idea:** Long stocks gapping up 1–15% with >=1.5x relative volume that
  hold above the opening-range high and session VWAP at the decision time.
- **Reason:** Gap-and-go is a widely documented momentum pattern; RVOL and
  VWAP add confirmation and liquidity.
- **Data needed:** Minute aggregates + prior-day daily panel.
- **Test method:** GapRvol grid, walk-forward, overfit battery.
- **Result:** NOT TESTED on real data (synthetic run rejected, as expected).
- **Accepted or rejected:** Pending real data.
- **Why:** Needs real out-of-sample confirmation.

## SEED-002 — Opening-range breakout with volatility-scaled exits
- **Idea:** Breakout above the first 15-minute range, TP/SL scaled to ATR.
- **Reason:** Range expansion after a tight opening range can precede trend.
- **Data needed:** Minute aggregates; ATR from daily panel.
- **Test method:** ExitVariant comparison vs. dynamic quantile exits.
- **Result:** NOT TESTED on real data.
- **Accepted or rejected:** Pending.
- **Why:** —

## SEED-003 — Prior-day strength continuation
- **Idea:** Favor names with positive 3–5 day returns entering an intraday
  long, filtering for liquidity.
- **Reason:** Short-horizon momentum persistence.
- **Data needed:** Daily panel `ret_3d_prev`, `ret_5d_prev`.
- **Test method:** Add as ranker feature; ablate its permutation importance.
- **Result:** NOT TESTED.
- **Accepted or rejected:** Pending.
- **Why:** —

## SEED-004 — VWAP reclaim after morning dip
- **Idea:** Long when price reclaims session VWAP from below with rising
  volume in the first hour.
- **Reason:** Mean-reversion-to-trend around VWAP is a common intraday tell.
- **Data needed:** Minute aggregates (VWAP distance feature exists).
- **Test method:** New gate variant; walk-forward.
- **Result:** NOT TESTED.
- **Accepted or rejected:** Pending.
- **Why:** —

## SEED-005 — Liquidity-tiered thresholds
- **Idea:** Use different gap/RVOL thresholds for large- vs. small-cap
  liquidity tiers rather than one global threshold.
- **Reason:** Microstructure and typical move sizes differ by liquidity.
- **Data needed:** 20-day median dollar volume tiers (available).
- **Test method:** Tiered parameter sets in the grid; compare OOS by tier.
- **Result:** NOT TESTED.
- **Accepted or rejected:** Pending.
- **Why:** —

## EXP-20260706-da97a5 — GapRvol grid + ML ranking walk-forward cycle
- **Idea:** GapRvol grid + ML ranking walk-forward cycle
- **Reason:** baseline continuation hypotheses per research plan
- **Data needed:** SYNTHETIC random-walk data (scripts/make_synthetic_data.py) — plumbing check only
- **Test method:** walk-forward grid (15 trials), stress battery, edge gate
- **Result:** {"oos": {"total_trades": 17, "win_rate": 0.5294, "avg_win": 0.03036, "avg_loss": -0.02571, "profit_factor": 1.329, "expectancy": 0.00398, "net_return_compounded": 0.0581, "net_pnl_usd": 71.5, "max_drawdown": -0.1574, "sharpe": 0.915, "sortino": 0.933, "worst_day": -0.063, "worst_week": -0.1305, "worst_month": -0.0449, "pct_months_positive": 0.333, "n_days": 60, "n_months": 3, "stability": {"top_ticker": "SYN08", "top_ticker_pnl_share": 1.122, "top_month": "2025-08", "top_month_pnl_share": 1.664, "n_unique_tickers": 13}, "by_exit_reason": {"count": {"sl": 1, "time_stop": 15, "tp": 1}, "mean": {"sl": -0.063, "time_stop": 0.0037, "tp": 0.0752}}}, "stress_keys": ["base", "slippage_x2", "slippage_x3", "fees_x2", "entry_delay_1m", "entry_delay_3m", "drop_top_ticker", "drop_best_month", "bootstrap_p_mean_gt0", "n_trials"], "ml_oos": {"total_trades": 58, "win_rate": 0.4655, "avg_win": 0.02968, "avg_loss": -0.03102, "profit_factor": 0.833, "expectancy": -0.00276, "net_return_compounded": -0.1748, "net_pnl_usd": -250.96, "max_drawdown": -0.2943, "sharpe": -1.297, "sortino": -2.765, "worst_day": -0.0557, "worst_week": -0.135, "worst_month": -0.1812, "pct_months_positive": 0.333, "n_days": 60, "n_months": 3, "stability": {"top_ticker": "SYN16", "top_ticker_pnl_share": null, "top_month": "2025-08", "top_month_pnl_share": null, "n_unique_tickers": 23}, "by_exit_reason": {"count": {"sl": 24, "time_stop": 12, "tp": 22}, "mean": {"sl": -0.0369, "time_stop": -0.0011, "tp": 0.0336}}}}
- **Accepted or rejected:** rejected
- **Why:** fewer than 40 out-of-sample trades; fewer than 55% of OOS months positive; more than half of PnL from one ticker; more than 60% of PnL from one month; expectancy not positive under stress: slippage_x2; expectancy not positive under stress: fees_x2; expectancy not positive under stress: entry_delay_1m; bootstrap p=nan fails trial-adjusted threshold (n_trials=15)
- **Logged:** 2026-07-06T04:23:49.362049+00:00
