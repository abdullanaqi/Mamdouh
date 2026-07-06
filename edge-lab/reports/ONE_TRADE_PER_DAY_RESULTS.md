# One-trade-per-day results
_Generated 2026-07-06 04:22 UTC · Data: SYNTHETIC random-walk data (scripts/make_synthetic_data.py) — plumbing check only_

> **WARNING: SYNTHETIC DATA.** These numbers verify code plumbing only. They are NOT evidence of any market edge.


## Filtered mode (trade only when EV > 0)
- **total_trades:** 17
- **win_rate:** 0.5294
- **avg_win:** 0.03036
- **avg_loss:** -0.02571
- **profit_factor:** 1.329
- **expectancy:** 0.00398
- **net_return_compounded:** 0.0581
- **net_pnl_usd:** 71.5
- **max_drawdown:** -0.1574
- **sharpe:** 0.915
- **sortino:** 0.933
- **worst_day:** -0.063
- **worst_week:** -0.1305
- **worst_month:** -0.0449
- **pct_months_positive:** 0.333
- **n_days:** 60
- **n_months:** 3
- **stability:** `{"top_ticker": "SYN08", "top_ticker_pnl_share": 1.122, "top_month": "2025-08", "top_month_pnl_share": 1.664, "n_unique_tickers": 13}`
- **by_exit_reason:** `{"count": {"sl": 1, "time_stop": 15, "tp": 1}, "mean": {"sl": -0.063, "time_stop": 0.0037, "tp": 0.0752}}`

## Forced mode (exactly one trade every day, least-bad allowed)
- **total_trades:** 17
- **win_rate:** 0.5294
- **avg_win:** 0.03036
- **avg_loss:** -0.02571
- **profit_factor:** 1.329
- **expectancy:** 0.00398
- **net_return_compounded:** 0.0581
- **net_pnl_usd:** 71.5
- **max_drawdown:** -0.1574
- **sharpe:** 0.915
- **sortino:** 0.933
- **worst_day:** -0.063
- **worst_week:** -0.1305
- **worst_month:** -0.0449
- **pct_months_positive:** 0.333
- **n_days:** 60
- **n_months:** 3
- **stability:** `{"top_ticker": "SYN08", "top_ticker_pnl_share": 1.122, "top_month": "2025-08", "top_month_pnl_share": 1.664, "n_unique_tickers": 13}`
- **by_exit_reason:** `{"count": {"sl": 1, "time_stop": 15, "tp": 1}, "mean": {"sl": -0.063, "time_stop": 0.0037, "tp": 0.0752}}`

Forced-mode results are reported separately and never averaged into the filtered results.

## Monthly breakdown (filtered)
| month | net_ret | n_trades |
|---|---|---|
| 2025-07 | +0.000% | 0 |
| 2025-08 | +11.251% | 8 |
| 2025-09 | -4.491% | 9 |
