# Does the ML actually pick better names? (fair selection test)

This answers the question that started this: *"are you trading the same names?"* —
i.e. is the model's apparent edge real selection skill, or an artifact of it simply
**taking more trades** than the baselines?

## The flaw it exposed in the old ablation

The old `ablation.py` let each method trade a *different number* of names. The model
took ~1,085 trades (14/day) vs ~688 for scanner/random (8.9/day) and shared only ~half
its names with random. So the headline "model +0.115% vs random −0.115%" mixed two
things: **selection quality** and **trade count / fill mechanics**. It could not show
the ML picks better names.

The fix: hold the traded set/structure constant and vary **only** the selection method.

## The final rule (your spec)

At most **2 entries per day**, taken **when each candidate confirms** (the existing
2-bar up-confirmation) — not on a clock — and **no slot-refilling** after an exit. Once
a position closes it is not replaced that day. This is now the default in both the
backtest (`simulate_day(no_refill=True)`) and the live engine (`system_fixed.py`,
`REFILL = False`).

## Test A — selection only, no fills (the clean test)

Each day, build one candidate pool, rank it three ways (model score / premarket-momentum
scanner / random), and score each ranking by the mean **`target_gain`** (the post-open
30-min return from the 09:30 open) of its top-N names. Same N, same day, same pool — the
**only** difference is the ranking. Paired per-day t-tests; bootstrap CIs.

| window | N | model | scanner | random | model−random (t, p) |
|--------|---|-------|---------|--------|---------------------|
| **2026** | 1 | +6.64% | −0.05% | −0.26% | +6.90 (t=9.9, p≈0) |
| 2026 | 2 | +6.08% | +0.38% | −0.01% | +6.08 (t=11.7, p≈0) |
| 2026 | 5 | +4.47% | +0.03% | −0.05% | +4.52 (t=12.9, p≈0) |
| 2026 | 20 | +1.95% | −0.15% | +0.03% | +1.92 (t=19.3, p≈0) |
| **bear2025** | 1 | +5.36% | −2.54% | +0.08% | +5.29 (t=7.1, p≈0) |
| bear2025 | 2 | +4.77% | −1.71% | −0.13% | +4.90 (t=7.7, p≈0) |
| bear2025 | 5 | +3.07% | −0.81% | −0.14% | +3.20 (t=9.7, p≈0) |
| bear2025 | 20 | +1.18% | −0.46% | −0.15% | +1.33 (t=13.0, p≈0) |

**Read:**
- The model ranks names by forward return **far** above random and the momentum scanner,
  highly significantly, and the edge is **monotone in N** (strongest at the very top of
  the ranking) — the hallmark of a genuinely ordered signal, not noise.
- It **replicates out-of-sample** on the 2025 bear window — the strongest evidence here.
- Sanity check passes: random's top-N ≈ the candidate-pool mean (−0.07% in 2026, −0.28%
  in bear2025), confirming the random baseline is unbiased.

So the answer to *"are you trading the same names / is the ML noise?"* is: **no — the ML
demonstrably picks better names.** The old near-noise reading came from refilling slots
all day with lower-ranked names, which diluted the top-of-book edge away.

## Test B — equalized no-refill *real fills* (2026 only)

The same three rankings replayed through the real simulator under the final rule (≤2
entries/day, confirm, no refill), so all three take an identical 154 trades / 2-per-day.

| method | trades/day | win% | avg/trade | total |
|--------|-----------|------|-----------|-------|
| model | 2.0 | 65.6% | **+1.43%** | +219.7% |
| scanner | 2.0 | 51.3% | −0.07% | −11.5% |
| random | 2.0 | 53.2% | −0.31% | −47.8% |

model − random day-PnL = +3.47%/day (t=5.4, p≈0); model − scanner = +3.00%/day (t=4.7).
The selection edge survives realistic confirmation + slippage + SL-first exits — **when
fills are benign.**

## The big caveat: profit ≠ selection skill

The identical 154 model trades, under the **pessimistic** fill model (enter at the 09:32
bar *high*, exit any SL-touching bar at the bar *low*), flip to:

| fills | win% | avg/trade | total |
|-------|------|-----------|-------|
| fixed (entry +10bps, SL at level +10bps) | 65.6% | **+1.43%** | +219.7% |
| pessimistic (buy the high, sold at the low on stops) | 24.0% | **−1.72%** | −264.4% |

A **3.1%/trade swing on the same trades.** These are high-volatility halal small-caps
with large intrabar ranges, so *where inside the bar you actually fill* dominates the
P&L. The selection signal is robust; the **money is not** — it lives or dies on execution
quality, which a bar-data backtest cannot pin down. Realistic fills for thin names at
size are likely worse than "fixed."

### Other caveats
- **Test A's magnitudes overstate capturable edge.** `target_gain` is measured from the
  09:30 open, but live entry is ~09:32 after confirmation, and `open5` (first-5-min)
  features overlap the front of the target window. Test B (+1.43% fixed / −1.72%
  pessimistic) already excludes the un-enterable part and is the honest figure.
- **Realistic profitability is 2026-only.** The DuckDB has no 2025 intraday bars, so Test
  B / the fills backtest cannot be replicated out-of-sample. Only the *selection* edge
  (Test A) is confirmed OOS.
- **News features are zero** (no REST key); the model is effectively a premarket/open
  momentum-continuation predictor.

## Bottom line

The ML earns its keep **as a stock picker** — clean, significant, and replicated out of
sample. Your 2-per-day / no-refill rule is what converts that picking skill into a usable
strategy (refilling was throwing it away). But whether it **makes money** is unresolved
and hinges entirely on fill quality: +1.43%/trade under benign fills, −1.72%/trade under
adversarial ones, on the very same trades.

## Reproduce

```
cd stock-trading-ml/src
python3 ablation.py            # Test A (2026 + bear2025) + Test B (2026) -> ../results/ablation_fair_summary.json
python3 -c "import backtest as bt; bt.run(modes=('fixed','pessimistic'), no_refill=True, out_suffix='_norefill')"
```
Outputs: `ablation_fair_summary.json`, `backtest_{fixed,pessimistic}_norefill.csv`,
`backtest_summary_norefill.json`.
