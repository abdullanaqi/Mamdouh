# README_TRUTH.md — what is real and what is not

This file exists because the whole point of the project is to not fool
you (or ourselves). Read it before trusting anything.

## The single most important fact

**Real data HAS now been evaluated — and the verdict is still "no valid
edge found yet", this time as a real result rather than a default.**
On 2026-07-07 the full workflow ran on real Massive flat files covering
2023-01-03 .. 2025-12-31 (752 trading days, full US stock universe):
the leakage audit passed on that data, the 108-config walk-forward grid
(7 folds, 756 trials) froze **zero** configurations (every candidate had
negative empirical expectancy net of pessimistic costs — median ≈ −0.43%
per trade), and the ML ranking strategy lost −0.107% per trade over 360
out-of-sample trades. See `reports/EDGE_REPORT.md` for the numbers.

The original build happened in a sandbox blocked from Massive, using
synthetic random-walk data; those synthetic runs only proved the
machinery and safeguards. That caveat is now historical.

## What has actually been demonstrated

- The pipeline runs end-to-end: data layout → point-in-time features →
  walk-forward grid → stress battery → edge gate → one-trade selector.
- The no-lookahead guarantee is enforced and tested: a structural
  future-invariance check perturbs every post-decision bar and asserts no
  feature changes, plus a negative control proving the check has teeth.
- The execution simulator is pessimistic and unit-tested: stop-first on
  ambiguous bars, gap-through-stop fills worse than the stop, gap-through-
  target fills only at the target, fees on every trade.
- The edge gate correctly **rejects** lucky backtests on random data (the
  smoke test treats a false positive as a failure and exits non-zero).

## Assumptions baked in (each is a place real data could differ)

1. **Slippage** is a price-bucketed base (bps) plus a square-root impact
   term, not measured from quotes. Wider in reality for small caps.
2. **Spread proxy** = 10% of the prior day's high-low range. A crude
   stand-in for a real quoted spread.
3. **Relative volume** uses a fixed intraday volume curve, not a per-name
   seasonal curve.
4. **Flat files are end-of-day and unadjusted** for splits/dividends. A
   suspected-split flag masks history on >40% overnight gaps, but this is
   heuristic. Real corporate-action adjustment needs a proper source.
5. **No market-cap / float / borrow / hard-to-borrow** data is used.
6. **No quotes (NBBO)** — fills are modeled from trade bars only.
7. **Sizing** assumes you can get filled within a small fraction of
   one-minute volume; large sizes would move the market more than modeled.

## What is NOT implemented (on purpose, honestly)

- **Live same-day picks.** Flat files publish after the close, so they
  cannot drive a live intraday decision. `src/data/massive_rest_client.py`
  is a stub that raises `NotImplementedError` instead of guessing REST
  endpoints. Implement it against the verified Massive API docs, then wire
  it into `generate_daily_pick.py --live`.
- **Corporate-action adjustment** beyond the split-suspect heuristic.
- **Quote-based spreads / impact.**

## What would make a result trustworthy

At least 2+ years of real data with **6+ months of test data never touched
during development**, the leakage audit passing on real data, the edge gate
passing on the untouched test window, survival of doubled slippage / doubled
fees / entry delay, PnL not concentrated in one ticker or one month, and
finally **forward paper trading** before any real capital. Until all of
that holds, the correct output remains "No valid edge found yet."
