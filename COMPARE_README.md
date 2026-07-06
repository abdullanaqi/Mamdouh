# `--compare` — one-trade-per-day research mode

New mode added to **edited.py** (everything else untouched). It answers, from your
own DuckDB, whether one careful trade per day has a real edge — and reports
honestly when it doesn't.

## Run
```
python edited.py --compare                 # full grid
python edited.py --compare --quick         # reduced grid smoke test
python edited.py --compare --split=2025-06-01 --cost-bps=8
```
Needs the usual `data/market_data.duckdb` (`features`, `minute_bars`,
optionally `market_news`). No API keys are touched; it is read-only.

## What it does
- **Stage A** — walk-forward scoring (expanding window, retrain every
  BT_RETRAIN_EVERY days). Trains TWO model banks: full features for entries
  ≥ 09:35, and a premarket-only bank for 09:30/09:31 (no open5 leakage).
  Also fits a k-NN "similar historical setup" TP/SL and flags
  dilution/offering/SEC/FDA-risk headlines.
- **Stage B** — sweeps on the FIRST 60 % of the test window (validation),
  run in FORCED mode to isolate each dimension:
  9 entry timings (09:30/31/35/40/45, ORB-15, VWAP reclaim, first pullback,
  first green) → 10 exit logics (model/kNN/vol dynamic, fixed 3/2 & 1/1,
  structure SL, VWAP invalidation, trailing, break-even, time stop; EOD
  always) → 10 candidate rankings → greedy filter ablation.
- **Stage C** — leaderboard on the untouched LAST 40 % (holdout):
  forced-1, selective-1, 2/day, 3/day, no-trade baseline, fixed vs dynamic
  TP/SL, model-only, rule-only, model+rule, model+rule+veto (deterministic
  offline stand-in for the live Claude agents (Anthropic API)).
- **Stage D** — verdict + success-condition checklist. If forced 1/day has
  no positive net expectancy it prints exactly:
  `Forced daily trading does not currently have a proven edge.` plus the
  next research steps.

## Fills & costs (conservative by construction)
Same-bar TP+SL resolves as SL. Gaps through a level fill at the bar open.
Event entries signal on a bar close and fill at the next bar open. No entries
after 11:00, forced flat 15:55. Net = gross − (cost_bps + surge slippage)
per side; every table also shows the 2× slippage stress column.

## Outputs
`result v2/compare_<ts>/`: `leaderboard_holdout.csv`,
`leaderboard_fulltest.csv`, `trades_*.csv`, `daily_selection_*.jsonl`
(selected + top rejected with reasons), `summary.md`.

## Honest limitations (also printed in summary.md)
- NBBO spread and premarket-VWAP filters aren't testable from 1-min bars —
  approximated by price/liquidity floors, stated, not claimed.
- Historical "top premarket gainer" universes carry survivorship /
  point-in-time risk — the single biggest fake-profit trap in this strategy
  class. Treat absolute levels with caution; confirm in paper mode.
- Success condition #8 (paper trading matches backtest) can only be earned
  live; the checklist marks it pending.

## Smoke test
`python make_synth_db.py` builds a synthetic DB (random gappers, weak
planted signal) so the whole path runs without market data. Its P&L
validates the CODE only — it is not evidence of any edge.

---

# Massive flat-files path (`massive_backfill.py`) — gold standard

Minute aggregates for ALL tickers including extended hours: builds the
TRUE point-in-time premarket-gainer universe (delisted names included)
with real pm_open/high/low/close/volume, momentum, range and a proper
trailing premarket-volume surge baseline. No proxies.

```
export MASSIVE_S3_KEY=...      # flat-files Access Key ID
export MASSIVE_S3_SECRET=...   # flat-files Secret Access Key
python massive_backfill.py --from 2026-01-02 --to 2026-07-02
python edited.py --compare
```

Per day it downloads one minute_aggs file (tens of MB, deleted after
processing), aggregates premarket [04:00, 09:30) for the universe, and
stores regular-session bars for the selected candidates + SPY only.
Resume is automatic. Flat files carry no news, so news features are
honest zeros unless your live pipeline merges market_news separately.

Running it from this chat's sandbox requires adding `files.massive.com`
to the allowed network domains in Claude's settings — otherwise run it
locally with the same two env vars.

---

# Agent committee (Anthropic API)

The live news/risk/market veto agents now call Claude via the
Anthropic API instead of local Ollama. Add to `.env`:

```
ANTHROPIC_API_KEY=sk-ant-...
AGENT_MODEL=claude-haiku-4-5-20251001   # optional override
```

Fail-open behavior is unchanged: any API error defaults that agent's
vote to TRADE, and backtests still use the deterministic offline veto
proxy (no LLM calls during `--compare`).
