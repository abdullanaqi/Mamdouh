# Walk-forward results — GapRvolStrategy (grid)
_Generated 2026-07-06 04:22 UTC · Data: SYNTHETIC random-walk data (scripts/make_synthetic_data.py) — plumbing check only_

> **WARNING: SYNTHETIC DATA.** These numbers verify code plumbing only. They are NOT evidence of any market edge.


Folds: 3 · configurations tried: 15

## Fold 0
- chosen params: `None`
- validation expectancy: None
### Test (out-of-sample for this fold)
_no results_

## Fold 1
- chosen params: `{'decision_time': '09:45', 'gap_min': 0.01, 'gap_max': 0.3, 'rvol_min': 1.5, 'require_orb': False}`
- validation expectancy: -0.00305
### Test (out-of-sample for this fold)
- **total_trades:** 8
- **win_rate:** 0.75
- **avg_win:** 0.02495
- **avg_loss:** -0.01858
- **profit_factor:** 4.028
- **expectancy:** 0.01406
- **net_return_compounded:** 0.1147
- **net_pnl_usd:** 91.34
- **max_drawdown:** -0.0371
- **sharpe:** 4.611
- **sortino:** 3.792
- **worst_day:** -0.0361
- **worst_week:** -0.0372
- **worst_month:** 0.1125
- **pct_months_positive:** 1.0
- **n_days:** 19
- **n_months:** 1
- **stability:** `{"top_ticker": "SPY", "top_ticker_pnl_share": 0.593, "top_month": "2025-08", "top_month_pnl_share": 1.0, "n_unique_tickers": 6}`
- **by_exit_reason:** `{"count": {"time_stop": 8}, "mean": {"time_stop": 0.0141}}`

## Fold 2
- chosen params: `{'decision_time': '09:45', 'gap_min': 0.01, 'gap_max': 0.3, 'rvol_min': 1.5, 'require_orb': False}`
- validation expectancy: 0.01406
### Test (out-of-sample for this fold)
- **total_trades:** 9
- **win_rate:** 0.3333
- **avg_win:** 0.0412
- **avg_loss:** -0.02808
- **profit_factor:** 0.733
- **expectancy:** -0.00499
- **net_return_compounded:** -0.0508
- **net_pnl_usd:** -19.84
- **max_drawdown:** -0.1249
- **sharpe:** -1.295
- **sortino:** -1.867
- **worst_day:** -0.063
- **worst_week:** -0.1305
- **worst_month:** -0.0449
- **pct_months_positive:** 0.0
- **n_days:** 20
- **n_months:** 1
- **stability:** `{"top_ticker": "SYN08", "top_ticker_pnl_share": null, "top_month": "2025-09", "top_month_pnl_share": null, "n_unique_tickers": 7}`
- **by_exit_reason:** `{"count": {"sl": 1, "time_stop": 7, "tp": 1}, "mean": {"sl": -0.063, "time_stop": -0.0082, "tp": 0.0752}}`

## Aggregate out-of-sample (all fold test windows)
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
