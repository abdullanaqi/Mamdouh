# EDGE REPORT

_This report answers the project's core questions honestly. It will be
regenerated with real numbers after `scripts/run_research.py` is run on
real Massive data. As of this build, no real data has been evaluated._

## Verdict
**No valid edge found yet.** No real market data has been processed (the
build sandbox is blocked from Massive). All figures produced so far are
from synthetic random-walk data and are not evidence of any edge.

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

## What running on real data will add
Real out-of-sample metrics, the leakage audit on real data, the stress
battery outcome, and either an edge *candidate* (if every gate passes) or a
continued "no edge yet" with the specific failed rules listed.
