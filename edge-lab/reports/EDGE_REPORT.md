# EDGE REPORT

_This report answers the project's core questions honestly._
_Last updated 2026-07-07 after the first full run on REAL data:_
_Massive flat files, 2023-01-03 .. 2025-12-31 (752 trading days)._

## Verdict
**No valid edge found yet — now established on real data, not just by
default.** The full research cycle ran on 3 years of real Massive minute
and daily data (leakage audit passed on that data first: structural
future-invariance clean, no suspicious correlations).

- **GapRvol grid (756 trials, 7 walk-forward folds): zero out-of-sample
  trades.** In every fold, every configuration produced either fewer than
  8 validation trades or no candidate with positive empirical expectancy
  net of modeled costs, so no configuration was ever frozen for testing.
  A diagnostic probe confirms candidates DO pass the gates (~8/day); their
  EV is simply negative (median ≈ −0.43% per trade, of which only ≈ −0.25%
  is modeled costs). Gap-up continuation at these decision times has
  negative gross edge on this data.
- **ML ranking strategy (7 folds, models never see val/test): 360
  out-of-sample trades, win rate 55.6%, expectancy −0.107%/trade, profit
  factor 0.90, compounded return −39.2%, Sharpe −0.65.** More trades, same
  conclusion: costs and adverse selection eat the gross edge.
- Edge gate: failed at the first rule ("no fold produced a chosen
  configuration"); the stress battery therefore had nothing to stress.

The system did exactly what it was designed to do: it refused to promote
a losing hypothesis family into a strategy.

**Independently replicated (2026-07-08):** the negative-EV finding was
re-checked in a separate environment with an independently written
feature/label store and an intact 2023 minute dataset (this run had pruned
2023 minute files after caching them). Strict-gated candidates there:
12.3/day, mean net return −0.30% / median −0.12% per trade under the
reference exit (n=332). Same conclusion; details in REPLICATION-20260708
in `research/experiments/ideas_log.md`.

## The 20-question self-check

1. **Lookahead in features?** No. Enforced by `PointInTimeView` (hard
   assert that feature bars are strictly before the decision minute) and a
   structural future-invariance test with a negative control.
2. **Labels only from experienced future bars?** Yes. The labeler reuses
   the execution simulator forward from the decision minute.
3. **Universe filtered on prior-day info only?** Yes. All rolling stats are
   shifted one day into `*_prev` columns before gating.
4. **Splits/dividends?** Flat files are unadjusted; a >40% overnight gap
   sets a `suspect_split` flag that masks history features. Heuristic, not
   a substitute for real corporate-action data.
5. **Pessimistic fills on ambiguous bars?** Yes. If both target and stop
   fall in one bar, the stop is assumed hit first.
6. **Gap through stop fills worse than stop?** Yes (open + stop slippage).
7. **Fees on every trade?** Yes ($2 round trip by default, configurable).
8. **Size-aware slippage?** Yes (price-bucketed bps + sqrt impact).
9. **Test set touched during tuning?** No. Validation selects; test is
   scored once with the frozen winner.
10. **Multiple-testing correction?** Yes. The number of configurations is
    recorded and the bootstrap threshold is trial-adjusted.
11. **PnL concentration checks?** Yes — top-ticker and top-month PnL share
    are gate rules.
12. **Survives cost stress?** Required (doubled slippage, doubled fees).
13. **Survives entry delay?** Required (1-minute delay).
14. **Forced vs. filtered separated?** Yes, always reported separately.
15. **"No trade" allowed?** Yes, it is a valid daily output.
16. **Did an LLM choose trades?** No. LLM proposes/audits only.
17. **All ideas logged, including failures?** Yes (`ledger.jsonl`,
    `research/failed_ideas/`).
18. **Assumptions written down?** Yes (`README_TRUTH.md`).
19. **Live vs. historical distinguished?** Yes; live is not implemented and
    says so rather than faking a feed.
20. **Would the verdict survive an adversarial reviewer?** Yes — the
    honest verdict is "no edge found yet," which is the safe default.

## What would change this verdict
A *different hypothesis family* — not more tuning of this one. The grid
was already 108 configurations across 3 decision times; the failure mode
is not "wrong parameters" but "negative gross expectancy of the entry
class". Next candidates worth coding and testing under the same protocol:
mean-reversion entries, catalyst/news conditioning, short side, or
different holding horizons. Every new idea goes through the same gate:
leakage audit → walk-forward → stress battery → untouched test window.
