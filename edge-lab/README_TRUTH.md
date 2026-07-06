# README_TRUTH.md — what is real and what is not

This file exists because the whole point of the project is to not fool
you (or ourselves). Read it before trusting anything.

## The single most important fact

**No real market data has been evaluated by this system.** It was built in
a sandbox whose network cannot reach `files.massive.com` or
`api.massive.com` (verified: those hosts are blocked). Every metric
generated so far comes from **synthetic random-walk data** created by
`scripts/make_synthetic_data.py`, which by construction contains no edge.
Synthetic numbers exist only to prove the code runs and the safeguards
fire. They are labeled as synthetic in every report banner.

So the honest current verdict is: **No valid edge found yet — because no
real data has been run.** To change that, run the workflow in `README.md`
on a machine with your Massive credentials and network access.

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
