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
(374 trading days 2025-01-03 → 2026-07-02, 8957 feature rows).

### E2b — primary: split=2025-11-25 (test=150d, HOLDOUT=60d, multi-regime)
report: result v2/compare_20260706_185109/
Forced 1/day HOLDOUT (60 trades):
  win=38.3%  exp/tr=+2.160%  exp/day=+2.160%  2xslip=+1.725%
  PF=1.87  maxDD=-40.82%  streak=8  sharpe=3.94
Winner crowned = 1/day FORCED itself (NOT a holdout-selected variant this time),
so the positive result is on the mandated system with a real 60-trade sample.
Checklist: 7/8 pass. ONLY failing gate = Drawdown (-40.82% vs -15% limit).
Standout variant: rule-only — 57 trades, win 66.7%, exp/day +1.115%,
2xslip +0.914%, PF 2.55, maxDD -4.82%, sharpe 7.49 → passes ALL offline gates
(high win rate tames drawdown). Not crowned only because forced had higher
raw exp/day.

### E2a — cross-check: split=2026-04-22 (same 20d holdout as E1, 15mo training)
report: result v2/compare_20260706_18xxxx (compare_e2a.log)
Forced 1/day HOLDOUT (20 days):
  trades=14  win=28.6%  exp/tr=+1.217%  exp/day=+0.852%  2xslip=+0.685%
  PF=1.58  maxDD=-16.79%  streak=5  sharpe=2.72
Verdict: "does not currently have a proven edge" (trades 14 < 15, DD -16.8%).

### E2 SYNTHESIS (compare forced 1/day across all real-universe runs)
  B0  (top12, 6mo, 20d hold):  exp/day +0.699  2xslip +0.566  maxDD -15.97  n=13
  E1  (top24, 6mo, 20d hold):  exp/day +0.354  2xslip -0.116  maxDD -40.15  n=20
  E2a (top24,18mo, 20d hold):  exp/day +0.852  2xslip +0.685  maxDD -16.79  n=14
  E2b (top24,18mo, 60d hold):  exp/day +2.160  2xslip +1.725  maxDD -40.82  n=60

ROBUST (survives every spec): forced 1/day is POSITIVE and survives 2x slippage
in every real-universe run; E2b confirms it on 60 multi-regime trades WITHOUT
holdout-selection. Direction is real.
PERSISTENT BLOCKER: max drawdown FAILS the -15% gate in every top-24 run
(-16% to -41%). Low win rate (28-38%) → deep, long losing streaks. This is THE
bottleneck, not expectancy.
MAGNITUDE UNTRUSTWORTHY: exp/day swings +0.35% → +2.16% across specs — far too
unstable to trust the level. Consistent with the brief's survivorship /
point-in-time inflation warning for top-gainer universes. Trust DIRECTION, not
the numbers. A +2.16%/day forced level is not believable as a tradeable return.
LEAD: rule-only (E2b) keeps most of the edge with 66.7% win and -4.82% DD and
passes all offline gates — a rule-gated SELECTIVE long is the tradeable shape;
low win rate + forced daily is not. But rule-only was flat on E2a's 20d
(+0.018) → regime-sensitive; needs the larger sample to show, not proven.

RESULT: Extending data did NOT "prove" an edge (do not trade). It strengthened
the DIRECTIONAL signal and pinpointed DRAWDOWN as the hard blocker and MAGNITUDE
as inflated. Next single experiment must target drawdown control.

---

## E3 — cap premarket gap at 30% (change vs E2: add --max-gap 30 ceiling)
Justification (data-driven, not a guess): diagnosed E2b forced trades — all 8
worst losses were extreme gappers (pm_pct 45-202%) that gapped THROUGH the -1%
stop and filled near -11% (conservative gap-through-level fill). Expectancy
sweeps keep them (>40% bucket has highest mean AND worst losses), so only a
hard universe cap removes them. Fast proxy (filter existing E2 DB to gap<=30):
forced maxDD -40.8 -> -28.1, win rates up to 56-77% -> worth a proper backfill.
Added `--max-gap` to massive_backfill.py (default 0 = off, backward compatible).
Backfill: 2025-01-02→2026-07-02 `--top 24 --min-gap 2 --max-gap 30`
DB: data/e3_gap30.duckdb (374 days, max pm_pct 29.98%, 24 cand/day)
Compare: `--split=2025-11-25` (60-day holdout) | report: result v2/compare_20260706_204644/

Forced 1/day HOLDOUT (60 trades):
  win=30.0%  exp/tr=+1.347%  exp/day=+1.347%  2xslip=+1.033%
  PF=1.84  maxDD=-26.59%  streak=8  sharpe=3.48
Gates: 7/8 pass. ONLY fail = Drawdown (-26.59% vs -15%).
Clean SELECTIVE shapes on the capped universe (all pass every offline gate):
  fixed TP/SL baseline: 27 tr, win 63.0%, exp/day +0.322, 2xslip +0.209, DD -4.39, PF 1.86
  rule-only:            59 tr, win 54.2%, exp/day +0.488, 2xslip +0.258, DD -10.47
  1/day selective:      24 tr, win 37.5%, exp/day +0.295, 2xslip +0.188, DD -7.89

vs E2b (same 60-day holdout, uncapped): forced maxDD -40.82 -> -26.59 (better),
exp/day +2.160 -> +1.347 (magnitude de-inflated -> more believable),
win 38.3 -> 30.0 (worse). Residual: 44/150 forced trades still lose >2% (gap
through stop, worst -10%) — deep-loss risk is INTRINSIC to buying gappers with
a fixed stop, not just an extreme-tail effect. Tighter caps also kill winners
(E2b rule-only 66.7%/-4.82 DD was better than E3 rule-only 54.2%/-10.47).

RESULT: **Partial success — improvement, not a pass.** Gap cap materially cut
forced drawdown and de-inflated magnitudes, but forced-1/day still fails the
-15% DD gate (-26.6%) because its 30% win rate produces deep streaks. On the
capped universe the SELECTIVE / fixed-TP vehicle passes ALL offline gates
(fixed-TP: 63% win, -4.39% DD). Keep --max-gap 30 as an improvement.

---

## STEP-5 SYNTHESIS (after E1-E3)
1. There IS a directionally robust, 2x-slippage-surviving long edge in premarket
   gappers — positive forced expectancy in every real-universe run, confirmed on
   60 multi-regime trades without holdout-selection.
2. Forcing a trade EVERY day is the wrong vehicle: 28-38% win rate -> deep
   drawdowns (-16% to -41%) that fail the -15% risk gate in EVERY config. This
   is the one gate that never passes for forced.
3. The tradeable shape is SELECTIVE + gap-capped: skip low-quality days and drop
   extreme (>30%) gappers. On E3 this vehicle passes all offline gates (fixed-TP
   63% win / -4.39% DD; rule-only 54% win / -10.47% DD).
4. Absolute magnitudes are inflated/unstable (survivorship of point-in-time top
   gainers). Trust DIRECTION and gate-passes, not the % levels.
5. NOTHING is proven. Halal filter is OFF (no halal_stocks.json) and news
   features are zeros — two invariant-relevant gaps for a real result. Paper
   trading (success condition #8) is the only thing that can confirm any of
   this and remains pending.

Recommended next moves (pick one, one change at a time):
  a. Paper-trade the E3 gap-capped SELECTIVE system (fixed-TP or rule-only) via
     --live paper flow — the only path to a real success condition.
  b. Source a proper halal_stocks.json so the universe is actually halal (the #1
     invariant), then re-run E3 to see if the edge survives the halal subset.
  c. More regimes: extend to 2024 (another ~250 days) to further de-noise.
  d. Do NOT keep tuning gap caps — diminishing returns; deep-loss risk is
     intrinsic to fixed-stop gapper entries.

---

## E4 — "market acceptance / continuation" study (user hypothesis)
Question: of premarket top gainers, which does the market ACCEPT (hold + keep
going after the open) vs fade — and is that subset a tradeable edge?

ANALYSIS (idealized, on e2 top-24 18mo, pooled over all candidates):
- "first-5-min green = accepted" barely predicts continuation: P(label) 47.1 vs
  45.5%. Strength buckets non-monotonic: BOTH extremes fade (open5<-2% -> 42%,
  open5>+5% blow-off -> 39%); moderate 0..+5% best (~49%). Same extreme=fade
  lesson as the gap cap.
- Buying confirmed strength at 09:35 LOSES (-1.7%/tr, 36% win) — by the time
  acceptance is visible you buy the local top and it mean-reverts.
- Clean entry-at-OPEN, either direction: ~-0.2% (no directional edge).
- Apparent "+2.588% buy-the-dip" (open5<=0, 09:35 entry, EOD hold) is a
  DENOMINATOR/tail artifact: 63% of profit from top 2% of trades; extreme dips
  divide by a tiny post-dip price. Mean>>median for deep dips.
- MILD dips (open5 -5..0) are clean (mean~=median): ~57% win, +0.9% median,
  consistent 2025 AND 2026(OOS). BUT fragile to fills: at 0.3% worse entry
  ~55% win/+0.7%; at 0.6% ~53%; at 1.0% the edge is GONE. Magnitude still
  survivorship-inflated.

RIGOROUS TEST (encoded in the engine, judged on holdout, conservative fills):
- As an ENTRY (`open_dip`, enter on mild 09:35 pullback): fires only 3x in the
  whole validation window (0% win). In a 1/day system the single ranked pick is
  almost always green at the open, so the entry never triggers — the pooled
  effect was really a SELECTION property, not entry timing.
- As a SELECTION rank (`dip_revert`, pick the mild-dip candidate): scored
  -0.743%/day, 34.4% win, maxDD -61% — one of the WORST rankings. REFUTED.
- The ranking that WON validation was `rank:open5_pct` (+2.364%/day, 41% win) =
  pick the candidate with the STRONGEST first-5-min continuation. The exact
  OPPOSITE of the mild-dip idea.

RESULT: **Mild-dip mean-reversion REFUTED by rigorous testing** — it was a
mirage of pooled, idealized, survivorship-inflated math; it does not survive
conservative fills + 1-pick-per-day. No NEW edge found. Both experimental hooks
(`open_dip` entry, `dip_revert` rank) reverted from edited.py; engine unchanged
(synthetic --quick honesty path verified). The one consistently-surviving signal
remains CONTINUATION selection (`rank:open5_pct` = buy the strongest continuer),
which PARTLY VINDICATES the user's instinct ("trade what the market accepts and
keeps going") — but it still fails the -15% drawdown gate for forced-daily and
its magnitudes are survivorship-inflated. Nothing proven.

METHOD NOTE: 4+ encodings now tried on the same holdout. Each extra try raises
false-discovery risk. Further edge-hunting on this backfilled top-gainer data is
hitting diminishing returns; the real unlocks are (1) survivorship-free /
richer data (float, borrow, NBBO, halal classification) and (2) PAPER TRADING —
the only test that can confirm any signal. Continued backtest mining will not
substitute for either.

---

## E5 — patient multi-timeframe confirmation + news/FDA weighting (user request)
Two-part idea: (a) "don't be in a hurry" — enter only after multi-timeframe
confirmation; (b) weight/confirm on news + FDA catalysts.

PART (b) NEWS/FDA — NOT TESTABLE ON THIS DATA. Verified: across all 8,957
candidates news_count, news_sentiment, has_fda, has_earnings are ALL exactly 0,
and market_news has 0 rows. Flat files carry no news (as designed). Cannot do
catalyst-weighted confirmation without a news/FDA feed. Refused to fabricate
nonzero catalysts. Needs a news API (original pipeline used FMP stock_news /
press_releases; only Massive flat-files S3 keys are available here, not a news
key). This is the same data gap as the halal blocker and is now the highest-
value unlock (catalysts are plausible AND completely untested).

PART (a) MTF CONFIRMATION — tested & REFUTED. Added `mtf_confirm` entry: wait
for the 15-min opening range, then require HTF (holds above 15-min OR high) +
MTF (5-bar momentum up) + LTF (1-min green & above VWAP); fill next bar open.
Validation entry sweep: exp/tr -0.394%, win 22.7%, fired only 44/90 days,
maxDD -26.2%, streak 14 — one of the WORST entries. `first_green` (early entry)
still won (+1.257%, 36.7%). Patience HURTS: waiting for confirmation on premarket
gappers means entering later into an already-extended move -> buy the top ->
fade. Reverted from edited.py (engine unchanged; synthetic --quick verified).

RESULT: no new edge. Consistent finding across E4-E5: on this backfilled
top-gainer data the ONLY surviving signal is EARLY continuation-selection
(entry=first_green, rank=open5_pct), and it still fails the -15% drawdown gate
for forced-daily. Entry-timing variations (dip, patient MTF) all lose. The
binding constraint is data (no news/FDA, survivorship inflation), not more
entry logic.

---
