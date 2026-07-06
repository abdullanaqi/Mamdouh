# MAMDOUH trade engine — handoff prompt for Codex

Paste everything below as your first message to Codex, running from the
project root that contains `edited.py`, `massive_backfill.py`,
`make_synth_db.py`, and `COMPARE_README.md`.

---

You are taking over an existing, working day-trading research system
called MAMDOUH. Read this brief fully, then read the code before changing
anything. Your job is to run the real-data experiment, interpret it
honestly, and iterate on the research — not to rewrite the system.

## Mission

Determine whether ONE carefully selected long trade per day on premarket
gappers has a real, net-of-costs edge — and report honestly either way.
The system already implements the full experiment; you operate it,
debug anything environmental, and extend it one careful change at a time.

## Non-negotiable invariants (do not weaken these, ever)

1. Halal universe, long-only, no leverage. `halal_stocks.json` filters the
   universe when present.
2. No leakage: `assert_no_leakage()` runs on every training call; all
   model scores are walk-forward (expanding window, retrained every
   `BT_RETRAIN_EVERY=10` test days on strictly prior dates). Entries
   before 09:35 use the premarket-only feature bank (`PM_FEATURES`) —
   never give them `open5_*` or `mkt_open5_pct`.
3. Conservative fills: same-bar TP+SL resolves as SL; gaps through a
   level fill at the bar open; event entries signal on a bar CLOSE and
   fill at the NEXT bar open (ORB fills at max(OR-high, bar open)); no
   entries after 11:00 ET; forced flat 15:55 ET.
4. Costs on every trade: `cost_bps` (default 8) per side plus surge
   slippage `min(0.5 × pm_vol_surge, 40)` bps; every report also shows
   the 2× slippage stress column.
5. Selection honesty: sweeps choose parameters ONLY on the first 60% of
   the test window (validation); the leaderboard, verdict, and success
   checklist come from the untouched last 40% (holdout). Never tune on
   holdout. Never report validation numbers as results.
6. If forced 1-trade/day has no positive net expectancy (or < 15 trades),
   the program prints exactly:
   `Forced daily trading does not currently have a proven edge.`
   followed by next-research steps. Keep that behavior and that sentence.
7. Never fabricate, extrapolate, or smooth results. If data is missing,
   say so. Synthetic-DB output validates code paths only — it is never
   evidence of edge.
8. Paper trading matching the backtest is the final success condition;
   it cannot be earned offline and stays marked "pending" until done.

## File map

- `edited.py` (~3,880 lines) — the whole engine: data pipeline, models,
  live IBKR trading, Telegram, Claude agent committee, `--backtest`,
  `--search`, and the compare lab (`--compare`).
- `massive_backfill.py` — Massive (Polygon) FLAT FILES → DuckDB backfill.
  True point-in-time premarket universe (delisted included), real pm_*
  features. Env: `MASSIVE_S3_KEY`, `MASSIVE_S3_SECRET`
  (endpoint `https://files.massive.com`, bucket `flatfiles`, prefixes
  `us_stocks_sip/day_aggs_v1|minute_aggs_v1/YYYY/MM/YYYY-MM-DD.csv.gz`).
  Premarket window is [04:00, 09:30) ET; `pm_vol_surge` uses each
  ticker's trailing premarket-volume baseline (prior days only). Resume
  is automatic; flat files carry no news → news features are honest
  zeros.
- `make_synth_db.py` — synthetic DB generator for smoke tests only.
- `COMPARE_README.md` — operator docs.

## DuckDB schemas (at `data/market_data.duckdb`)

- `features` (one row per candidate per day): date, ticker, pm_open,
  pm_close, pm_high, pm_low, pm_volume, pm_pct, pm_momentum,
  pm_range_pct, pm_vol_surge, gap_vol_quality, price_bucket,
  news_sentiment, news_count, has_earnings, has_fda, open5_pct,
  open5_range_pct, open5_vwap, open5_volume, open_price,
  post_open_close, label, intraday_open/close/high/low/volume/pct/
  range_pct/vwap, eod_label, mkt_pm_pct, mkt_open5_pct, mkt_rel_pm_pct.
- `minute_bars`: ticker, window_start (int64 ns UTC), open, high, low,
  close, volume. Regular session 09:30–16:00 ET for candidates + SPY.
- `market_news`: ticker, title, published_utc (naive UTC), sentiment.

Model feature list (`FEATURES`): pm_pct, pm_momentum, pm_range_pct,
pm_vol_surge, gap_vol_quality, news_sentiment, news_count, has_earnings,
has_fda, open5_range_pct, open5_vwap, open5_volume, open5_pct,
mkt_pm_pct, mkt_open5_pct, mkt_rel_pm_pct, price_bucket.

Label: from the 09:35 anchor open (`LABEL_ANCHOR_MIN=5`), TP +1% before
SL −1% within a 400-minute window; SL wins ties; otherwise sign of the
window close.

## The compare lab (`python edited.py --compare`)

Stage A: walk-forward scoring — trains TWO banks per retrain (full
features for entries ≥ 09:35; PM-only for 09:30/09:31), plus a k-NN
similar-setup TP/SL (k=50) and dilution/SEC/FDA risk-news flags from
`market_news` headlines.
Stage B (validation slice, run FORCED to isolate each dimension):
entry sweep (0930, 0931, 0935, 0940, 0945, orb15, vwap_reclaim,
first_pullback, first_green) → exit sweep (model_dyn, knn_dyn, vol_dyn,
fixed_3_2, fixed_1_1, structure, vwap_inval, model_trail, model_be,
model_time) → ranking sweep (ev, up_prob, pred_ev, mfe_mae, ranker,
pm_pct, pm_vol_surge, open5_pct, catalyst, rel_strength) → greedy filter
ablation (no_neg_news, has_news, no_risk_news, mkt_ok, gap_cap,
range_cap, dollar_hi, open5_green, above_o5vwap).
Stage C (holdout leaderboard): 1/day FORCED, 1/day selective, 2/day,
3/day, fixed TP/SL baseline, dynamic TP/SL, model-only, rule-only,
model+rule, model+rule+veto, plus a no-trade baseline row. Metrics: win
rate, avg win/loss, PF, expectancy per trade and per day (and at 2×
slippage), net return, max DD, longest losing streak, worst day/week,
Sharpe, Sortino (clipped ±99.9), monthly, regime split on SPY premarket.
Stage D: verdict + success checklist; reports written to
`result v2/compare_<ts>/` (leaderboard CSVs, per-trade CSVs, daily
selection JSONL with rejected candidates + reasons, summary.md).

CLI: `--compare [--quick] [--split=YYYY-MM-DD] [--cost-bps=8]`, plus the
pre-existing `--train / --backtest / --search / --live`.

The backtest veto (`_cmp_veto`) is a deterministic offline proxy
(risk_news, sentiment < −0.15, SPY pm < −0.30, gap > 60%). The LIVE
agent committee (news/risk/market) calls Claude via the Anthropic API:
`ANTHROPIC_API_KEY` in `.env`, model `claude-haiku-4-5-20251001`
(override with `AGENT_MODEL`), header `anthropic-version: 2023-06-01`,
JSON contract `{"action": "TRADE"|"SKIP", "reason": "..."}`, fail-open
to TRADE on any error. `--compare` never calls the API.

## Environment & secrets

- `.env`: `ANTHROPIC_API_KEY`, `MASSIVE_S3_KEY`, `MASSIVE_S3_SECRET`
  (+ the pipeline's existing Massive REST/IBKR/Telegram vars). Never
  hardcode keys; never print them; suggest rotation if you see one in
  history.
- Python: pandas, numpy, scikit-learn, duckdb, python-dotenv, requests,
  boto3, lightgbm (catboost optional — code falls back gracefully).

## Current state

Validated: full `--compare` end-to-end on synthetic data (honesty gates
fire correctly on random data); massive_backfill per-day pipeline
unit-tested on fabricated flat files; agent committee verified fail-open.
Pending: real-data backfill + compare run; valid Anthropic key in .env;
paper trading.

## Your task sequence (in order)

1. Environment gate: deps import; `.env` keys present;
   `python make_synth_db.py && python edited.py --compare --quick`
   completes with zero tracebacks. Fix environment issues only.
2. Real backfill:
   `python massive_backfill.py --from 2026-01-02 --to 2026-07-02`
   (resumable; verify candidate counts per day look sane: ~5–12).
3. Real experiment: pick `--split` so the test window has ≥ 40 trading
   days, then `python edited.py --compare --split=<date>`.
4. Report the holdout leaderboard, verdict, and checklist VERBATIM.
   State limitations that apply to this run (e.g., no news features from
   flat files; halal filter only if the JSON is present; single ~6-month
   regime).
5. If no edge: follow the printed next-research list. One change at a
   time; justify it before coding; re-judge only on holdout; keep a log
   of every experiment and its holdout result, including failures.
6. If edge appears: extend the window backward for more regimes, rerun,
   then hand off to paper trading (`--live` paper flow). Do not call
   anything "proven" before paper results match.

## Acceptance criteria for any change you make

- `--compare --quick` still completes on the synthetic DB with zero
  tracebacks and unchanged honesty behavior.
- No invariant above is weakened; validation/holdout separation intact.
- Schemas remain backward-compatible with existing DuckDB files.
- Every claim in your summary is traceable to a produced artifact.

## Known landmines (do not reintroduce)

- `_TICKER_RE = ^[A-Z]{1,5}$` guards SQL interpolation — synthetic or
  test tickers must be letters-only.
- DuckDB is single-writer: never parallelize writers on one DB file.
- `_cmp_train_bank` swaps the global `FEATURES` on purpose (single-
  threaded); don't make training concurrent.
- Timestamps: flat-file and FMP-style feeds are ET wall-clock or ns-UTC —
  every conversion goes through the tz-aware helpers; never naive math.
- Backfill resume deletes-then-reinserts per date; keep that idempotency.
- Sharpe/Sortino are clipped ±99.9 because tiny samples explode; keep it.
- pm_* features from the Massive path are REAL premarket values; do not
  replace them with gap proxies.
