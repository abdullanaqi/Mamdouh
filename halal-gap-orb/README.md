# halal-gap-orb

A production-grade halal Opening Range Breakout (ORB) gap-trading system for
US equities, built around the Zarattini-Barbon-Aziz (2024) "Stocks in Play"
research, augmented with an LLM catalyst classifier and an XGBoost scoring
layer. Data source: Financial Modeling Prep.

## Status

| Stage  | Description                              | Status |
|--------|------------------------------------------|--------|
| 1      | Rule-based ORB backtest (Zarattini base) | code complete, real-data run pending |
| 2      | LLM catalyst classifier                  | code complete, real-data run pending |
| 3      | XGBoost scoring layer                    | code complete, real-data run pending |
| 4      | Paper trading loop                       | code complete, real-data run pending |

## Quickstart

```bash
# 1. install deps (uv)
uv sync

# 2. copy env template, fill keys
cp .env.example .env
# edit .env -> FMP_API_KEY=..., OPENAI_API_KEY=...

# 3. run synthetic smoke test (no API key required)
uv run python scripts/smoke_test.py

# 4. run unit tests
uv run pytest -q

# 5. run the full Stage 1 backtest (requires FMP_API_KEY)
uv run python scripts/run_stage1_backtest.py 2022-01-03 2023-12-29

# 6. run the gate tests against the persisted artifacts
uv run pytest tests/test_stage1_gate.py -q

# 7. backfill catalyst classifications over the Stage 1 candidates
uv run python scripts/backfill_catalysts.py

# 8. train the Stage 3 XGBoost scoring model (walk-forward + final fit)
uv run python scripts/train_stage3.py

# 9. run one paper-trading day end-to-end (kill switch + scan + gates + ledger)
uv run python scripts/run_paper_day.py --date 2024-01-22 \
    --use-catalyst --scoring-model models/scoring_model.pkl
```

## Stage 4: paper-trading loop

`execution.DailyOrchestrator` is the live/paper driver. Given a date, a
universe, and per-symbol bars it:

1. Evaluates `KillSwitch` against the equity log (+ optional VIX series).
   If tripped, new entries are blocked for the day; existing positions
   still resolve through normal stop / EOD logic.
2. Runs the Stage 1 scanner and builds OR setups.
3. Optionally applies the Stage 2 catalyst gate
   (`passes_catalyst_gate`) and the Stage 3 ML gate
   (`ScoringModel.passes`).
4. Hands surviving setups to `PaperBroker.run_day`, which routes them
   through `simulate_trade` and aggregates NAV.
5. Appends realised positions to `data/paper_trades.parquet` and the
   daily NAV to `data/paper_trades_equity.parquet`.

`KillSwitch` is stateless: every rule comes from `config/settings.yaml
-> kill_switch`. Rules covered:
- consecutive losing days >= `consec_losing_days`
- rolling drawdown over `rolling_dd_window_days` <= -`rolling_dd_pct`
- paper-vs-live Sharpe divergence > `paper_live_sharpe_divergence`
  (skipped when no live history is supplied)
- VIX >= `vix_threshold` for `vix_consec_closes` consecutive closes
- max pairwise position correlation >= `position_correlation_max`

`PaperBroker` is intentionally a thin wrapper that uses the existing
`strategy.orb.simulate_trade` (so the paper loop and the backtester
share one fill model). A `Broker` Protocol is exposed so a live broker
(Alpaca / IBKR) can drop in without orchestrator changes.

## Stage 3: XGBoost scoring layer

`features.build_dataset(trades_df, catalysts_df)` projects the Stage 1
trade log + Stage 2 catalyst log into a model-ready training set whose
columns are exactly `features.feature_columns()` plus `symbol`, `as_of`,
and `label` (1 if `pnl_r > 0`).

`model.run_walk_forward(df)` runs CV with windows from
`settings.ml.cv` (12-month train, 3-month test, 3-month step). Each fold
returns AUC / PR-AUC / log-loss / Brier on its OOS slice. Folds smaller
than 30 train rows or with homogeneous labels are skipped, not failed.

`model.fit_final_model(df)` trains the production model on the full
labelled dataset; the result is a `ScoringModel` that pickles itself with
its canonical feature column list, so inference time cannot mis-order
inputs. `ScoringModel.passes(X, threshold=None)` enforces the
`ml.win_probability_threshold` gate (default 0.55).

`model.explain(model, X)` runs SHAP TreeExplainer and returns an
`ExplanationBundle` with `top_k(row_idx, k)` and `mean_abs_importance()`
helpers for monitoring.

Hard rules:
- No leakage. `features/dataset.py` projects only the as-of-09:35
  inputs (gap, RVOL, ATR, risk) and the as-of-09:25 catalyst features.
  A regression test (`test_build_dataset_no_leakage_columns`) refuses to
  let `entry_price`, `exit_price`, `pnl_dollars`, `exit_reason`, or
  `high_water_r` leak into the feature matrix.
- Train and test slices are strictly non-overlapping in time
  (`train_end == test_start`). No purging is necessary because the labels
  are realised intraday on the same date as the features.
- Categorical `catalyst_type` is one-hot encoded against the fixed
  `CATALYST_TYPES` tuple so the schema is stable across runs.

## Architecture

```
src/halal_gap/
  data/        FMP async client, parquet cache, halal filter
  universe/    daily universe (point-in-time S&P 500 + halal + liquidity + ATR)
  scanner/     pre-market gap scanner (9:25 ET)
  strategy/    ORB entry / exit rules (Zarattini)
  catalyst/    Stage 2: news fetch + OpenAI structured-output classifier
               + disk cache + daily cost cap + feature row
  features/    Stage 3: feature builder + training dataset assembly
  model/       Stage 3: XGBoost predictor, walk-forward trainer, SHAP
  execution/   Stage 4: paper broker + kill switch + ledger + orchestrator
  backtest/    event-driven backtester + metrics + HTML report
  utils/       time helpers, indicators, config, logging
```

## Stage 2: catalyst classifier

`catalyst.classify(symbol, as_of, gap_pct, items)` returns a strict-JSON
classification (`direction`, `strength`, `confidence`, `catalyst_type`,
`summary`) using OpenAI's `json_schema` response format. Results are
parquet-cached per (symbol + prompt-version + item titles); a per-day USD
cost meter blocks calls once the `llm.max_daily_spend_usd` cap is reached.

Hard rules:
- News is fetched via `news_fetcher.fetch_news_before(symbol, as_of_ts)` and
  strictly filters out anything published at or after `as_of_ts`.
- When `OPENAI_API_KEY` is not set, a deterministic keyword-based fallback
  fires and the result is flagged `skipped_reason="no_api_key"`.
- When the day's cap is hit, the classifier returns
  `skipped_reason="cost_cap"` and `passes_catalyst_gate` defaults to True
  (do not block trades just because the budget is gone).
- `PROMPT_VERSION` is part of the cache key, so editing the system prompt
  forces re-classification automatically.

All parameters live in `config/settings.yaml`. Halal exclusions live in
`config/halal_exclusions.yaml`. No magic numbers in code.

## Hard rules

1. **Long-only.** No shorts (halal + locate-free).
2. **Min price $25, 20-day $20M dollar volume, ATR &ge; 1% of price.**
3. **Point-in-time S&P 500 universe**, reconstructed from FMP's historical
   constituent change log. Never the current list.
4. **No look-ahead.** Every feature takes an `as_of_ts` cutoff; LLM news
   is filtered to publications strictly before the decision time.
5. **All times in America/New_York.** Naive datetimes are rejected.
6. **Every FMP call is parquet-cached** under `data/cache/{endpoint}/`.

## Stage 1 gate

To advance to Stage 2, the rule-based backtest must clear:

- Sharpe &ge; 0.8 (out-of-sample 6 months)
- Hit rate &ge; 42%
- Max drawdown &ge; -25%
- Trades/day in [0.5, 5.0]
- Universe at any historical date &ne; current S&P 500 list

Tests live in `tests/test_stage1_gate.py`. The performance-related tests
auto-skip until `reports/stage1_artifacts.parquet` exists; produce it with
`scripts/run_stage1_backtest.py`.

## Expected realistic outcome

Based on Zarattini SFI Paper 24-98 with the $25+ halal long-only universe
and realistic slippage:

| Metric        | Expected range |
|---------------|----------------|
| Hit rate      | 45-52%         |
| Avg trade R   | +0.05 to +0.15 |
| Sharpe        | 1.0 to 2.0     |
| Max drawdown  | 15-25%         |
| Trades/day    | 1 to 5         |

If your backtest yields materially better numbers (Sharpe > 2.5, hit rate
> 60%), assume a look-ahead bug and audit before declaring success.

## Caveats

- The full 2-year intraday pull for ~300 names is **slow** (hours) and
  consumes FMP rate-limit. Run overnight; everything is cached.
- US market holidays are not honoured by the synthetic session generator;
  the backtester relies on FMP returning no rows on closed days.
- Slippage and commissions are configurable; the defaults are intentionally
  conservative compared to the Zarattini paper (which ignored slippage).
- Short-borrow availability, LULD halts, and after-hours news are not
  modelled in Stage 1.
