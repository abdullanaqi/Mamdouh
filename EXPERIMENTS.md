# MAMDOUH research log — step 5 (one change at a time, judged on HOLDOUT)

Rules: one change per experiment; justify before coding; re-judge ONLY on the
untouched holdout; log every result, including failures. Absolute levels are
suspect (survivorship / point-in-time risk) — direction and gate-passes matter
more than magnitude. Nothing is "proven" until paper trading matches.

Data context for all runs below: Massive flat files, 2026-01-05 → 2026-07-02
(124 trading days), REAL premarket features, news = honest zeros (flat files
carry no news), halal filter OFF (no halal_stocks.json), single ~6-month
regime. Split = 2026-04-22 (test = 50 days, holdout = last 40% ≈ 20 days).

Metrics reported = forced 1/day on HOLDOUT unless noted. Gates that must all
pass for an edge: positive net exp, positive holdout, robust across sub-periods,
maxDD within -15%, survives 2x slippage, >=15 trades, realistic fills, no
leakage, paper confirms (pending).

---

## Baseline (B0) — top-12 universe, default filters
Backfill: `--top 12 --min-gap 2.0 --min-price 5 --max-price 100 --min-pm-dollar 300k`
Compare:  `--split=2026-04-22 --cost-bps=8`
DB: data/market_data.duckdb | report: result v2/compare_20260706_062148/

Forced 1/day HOLDOUT (20 days):
  trades=13  win=46.2%  exp/tr=+1.075%  exp/day=+0.699%  2xslip=+0.566%
  PF=1.39  maxDD=-15.97%  streak=3  sharpe=2.16

Verdict: **Forced daily trading does not currently have a proven edge.**
Gates: 6/8 pass. FAIL: maxDD -15.97% (> -15% limit) and trades 13 (< 15).
Note: forced 1/day is genuinely positive and survives 2x slippage, but the two
failing gates (drawdown, sample size) block a proven-edge call. Honest.

---

## E1 — widen universe top-12 → top-24 (change vs B0: candidate breadth only)
Justification: candidate count hit the cap of 12 almost every day (binding), so
the model/ranking only ever saw the 12 most extreme gappers. Breadth is the #1
printed-impact item AND the only lever outside what the compare lab already
sweeps internally (it already sweeps 9 entries x 10 exits x 10 rankings). Data
fully supports it (flat files contain all tickers). Leak-safe: selection stays
premarket-only, baselines still update prior-days-only.
Backfill: `--top 24`  DB: data/e1_top24.duckdb | report: result v2/compare_20260706_182038/
Compare: `MAMDOUH_DB=data/e1_top24.duckdb --split=2026-04-22 --cost-bps=8`

Forced 1/day HOLDOUT (20 days):
  trades=20  win=35.0%  exp/tr=+0.354%  exp/day=+0.354%  2xslip=-0.116%
  PF=1.09  maxDD=-40.15%  streak=8  sharpe=0.58

Printed verdict: "SYSTEM MEETS the offline success conditions" — BUT this is on
the holdout-crowned winner (rule-only: 17 trades, +0.918%/day, PF 2.39,
maxDD -4.50%). `_cmp_pick_best` selects the best variant BY HOLDOUT among ~10
variants (>=15-trade floor), then the checklist is scored on it. Parameter
sweeps use validation (firewall intact), but the final variant crowning is a
selection-on-holdout on only 20 days — the "PASS" is weaker than it reads.

RESULT: **REJECT for the forced mandate.** Forced 1/day got WORSE on every
quality axis (exp/day +0.699→+0.354, 2xslip +0.566→-0.116 [now negative],
maxDD -15.97→-40.15). Widening drags worse extreme-gap names into the forced
daily pick. The rule-only selective signal (+0.918%/day, 64.7% win, DD -4.5%)
is a genuine LEAD but is holdout-selected + thin-sample (survivorship/small-N
trap the brief warns about). Do NOT believe it without more data. B0 universe
remains the reference for the forced question.

---

## E2 — extend window backward to 2025-01 at top-24 (change vs E1: more data/regimes)
Justification: E1 surfaced a promising selective signal but it was holdout-
selected on a 20-day holdout and thin. The dominant fix for both problems is
more data: a longer, multi-regime window grows every variant's sample and lets
the (fixed set of) variants be judged on a holdout with real N. Directly
attacks E1's two weaknesses. One change vs E1: add ~12 months of earlier data.
Backfill: 2025-01-02 → 2026-07-02, `--top 24`  DB: data/e2_2025_top24.duckdb
Status: RUNNING (see below on completion).

---
