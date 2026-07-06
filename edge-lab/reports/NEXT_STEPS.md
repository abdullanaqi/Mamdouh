# NEXT STEPS (ordered)

1. **Get real data.** Run `scripts/build_dataset.py` for 2+ years with at
   least the most recent 6 months set aside as a test window you will not
   look at while developing.
2. **Audit on real data.** `scripts/audit_for_leakage.py` must report
   clean before trusting anything downstream.
3. **Persist features to `data/features/`.** The current search memoizes
   feature/label rows in memory per run, which is fine for a handful of
   tickers but not for the full market over years. Write them to parquet
   keyed by (date, decision_time) so grid search and walk-forward scale.
4. **Replace the spread proxy with real quoted spreads** (from Massive
   quotes or minute NBBO) and recompute slippage/impact from data.
5. **Add corporate-action adjustment** (splits/dividends) via a real
   source; retire the split-suspect heuristic.
6. **Implement the live data client** (`src/data/massive_rest_client.py`)
   against the *verified* Massive REST/WebSocket docs, then wire
   `generate_daily_pick.py --live`. Do not guess endpoints.
7. **Run the research cycle** and read the verdict + failed rules. Iterate
   on hypotheses via the ledger, never by tuning on the test set.
8. **If (and only if) the gate passes on untouched data, paper trade
   forward** for a meaningful period before considering real capital.
