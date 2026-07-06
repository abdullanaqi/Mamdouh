# AGENTS.md — research protocol & pre-publication checklist

This system is allowed to say "I found an edge" only after every item
below is satisfied on data that was untouched during development. Anything
short of that is reported as **"No valid edge found yet."**

## Roles
- **Code** finds and validates edges. Deterministic, testable, logged.
- **LLM advisor** (optional) proposes hypotheses and audits assumptions.
  It never selects trades and never sees a result before it is measured.
  Every LLM idea is logged to `research/experiments/ideas_log.md` with
  result "NOT TESTED" until code proves it out.

## The research loop
1. Write the hypothesis in `research/experiments/ideas_log.md` with fields:
   Idea, Reason, Data needed, Test method, Result, Accepted/Rejected, Why.
2. Implement it as a `Strategy` (features from `PointInTimeView` only).
3. Walk-forward: fit on train, select on validation, score the frozen
   winner once on test. Record the number of configurations tried.
4. Run the overfit battery on the out-of-sample trades.
5. Log the outcome to the ledger (`ledger.jsonl`). Rejections are archived
   to `research/failed_ideas/`. Failed ideas are kept, not deleted.

## Pre-publication checklist (all must be true)
- [ ] Leakage audit passes on **real** data (`reports/LEAKAGE_AUDIT.md`).
- [ ] >= 40 out-of-sample trades.
- [ ] Out-of-sample expectancy positive after modeled costs.
- [ ] >= 55% of out-of-sample months positive.
- [ ] Max drawdown better than -25%.
- [ ] No single ticker > 50% of PnL; no single month > 60% of PnL.
- [ ] Survives doubled slippage, doubled fees, and a 1-minute entry delay.
- [ ] Bootstrap p-value beats the trial-adjusted threshold (Bonferroni-ish
      on the number of configurations tried).
- [ ] Winning parameters are not a lonely spike vs. their neighbors.
- [ ] Test window was **never** used during development.
- [ ] Forward paper trading before real capital.

Passing the mechanical gate is **necessary, not sufficient** — it makes a
strategy a *candidate*, not a confirmed edge.

## 20-question self-check (answer honestly in reports/EDGE_REPORT.md)
1. Could any feature see data at or after the decision minute? 2. Are
labels computed only from post-decision bars the trade would actually
experience? 3. Is the universe filtered using only prior-day info?
4. Are splits/dividends handled or flagged? 5. Are fills pessimistic on
ambiguous bars? 6. Do gaps through the stop fill worse than the stop?
7. Are fees on every trade? 8. Is slippage size-aware? 9. Was the test set
touched during tuning? 10. How many configurations were tried, and is the
p-value adjusted for that? 11. Is PnL concentrated in a few names or days?
12. Does the edge survive cost stress? 13. Does it survive an entry delay?
14. Are forced picks separated from filtered picks? 15. Is "no trade" an
allowed output? 16. Did an LLM choose any trade? (must be no) 17. Are all
ideas — including failures — logged? 18. Are assumptions written down?
19. Is live vs. historical clearly distinguished? 20. Would the verdict
survive an adversarial reviewer reading only the reports?
