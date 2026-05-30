# Halal Premarket/Open ML Trading System — audit, fixes & 2026 backtest

This project takes the original single-file trading script (`src/system_asis.py`),
**documents the bugs that made the live Telegram log look like a 100% win-rate
money printer**, fixes them, and runs an honest walk-forward backtest over
**2026 YTD (Jan 2 – May 29)** on real minute data.

## TL;DR of the audit

The live log (e.g. 2026-05-29: 11/11 wins, ~+43%) was an **accounting artifact**,
not edge. Verified against real 1-minute tape:

| Bug | Effect | Fix (`backtest.py` "fixed" mode + live engine) |
|-----|--------|-----|
| **Entry booked at the 09:30:00 opening tick**, not the actual fill ~09:32 after the 2-bar confirmation | "Buys the bottom tick" of the opening spike; books the pop as profit | Entry = price of the 09:32 bar (post-confirmation), + slippage |
| **Exit booked at the exact TP/SL level**, TP checked first, no slippage | Gap-throughs recorded as best-case fills; every PnL == TP% to the cent | Exit at first real bar **touch**, SL-first within a bar, − slippage |
| **Re-entries reuse the stale opening price** | Re-buys a name at its 9:30 price while it trades 8% higher (OKTA 10:01) | Fresh price at the re-entry minute |
| **Train/serve skew**: `pm_pct, pm_momentum, pm_vol_surge` hardcoded to constants live | 3/11 features dead at inference → model predicts ≈0%/50% (noise) | `score(skew=…)` reproduces the bug for "asis"; "fixed" feeds real premarket features |
| **`ET_OFFSET_H = 4` hardcoded** | Wrong UTC window Nov–Mar (EST), mislabeled dates | `utc_ns()` uses `ZoneInfo("America/New_York")` everywhere |

See `src/system_asis.py` for the original (unchanged), `src/system_fixed.py` for
the corrected real-time engine, and `src/backtest.py` for both replays.

### `system_fixed.py` — the corrected live engine

All five bugs above are patched in place (search the file for `FIX(...)`):

- **`FIX(DST)`** — `_et_to_utc()` (ZoneInfo) replaces every `+ ET_OFFSET_H` hour
  offset in `bars()`, `price()`, and `_trading_dates()`. Winter sessions are no
  longer shifted by an hour. Unit-checked: 09:30 ET → 13:30 UTC (EDT) / 14:30 UTC (EST).
- **`FIX(skew)`** — `_premarket_live()` computes real `pm_pct / pm_momentum /
  pm_vol_surge` at inference, so the model sees the same features it trained on
  (no more 0/0/1 dead inputs).
- **`FIX(fills)`** — entries pay `SLIPPAGE` (10 bps) on the touched price; exits
  book the **actual crossing tick** minus slippage (not the idealized TP/SL level);
  EOD closes pay slippage too.
- **`FIX(reporting)`** — daily win-rate is `pnl>0` fraction, not TP-count (which
  is what manufactured the "100%" line).
- **`FIX(thread-safety)`** — the WS thread no longer reads `open_pos`; the
  re-entry slot count is read under `pos_lock`.

## Data & credentials

- **Minute/day bars**: Massive (= Polygon.io rebrand) **flat-file S3** bucket
  `files.massive.com/flatfiles/us_stocks_sip/{minute,day}_aggs_v1`. Loaded by
  `src/flatfiles.py` into `data/market_data.duckdb` (~91M minute rows, 102 days).
- **Halal universe**: built by `src/universe.py` from **EODHD** via Shariah
  sector/industry exclusion (mirrors `halal-gap-orb/config/halal_exclusions.yaml`).
  ~6.5k names screened; ~2.9k actually have US minute data each day.
- **News features are zeroed** in the backtest (the provided FMP key is a dead
  legacy plan, and flat-file keys carry no REST/news access). The model is
  near-noise regardless, so this has negligible effect.

All secrets live in a **git-ignored `.env`** (see `.env.example`). The keys
shared during development were exposed in chat and **should be rotated.**

## Reproduce

```bash
pip install massive duckdb scikit-learn pandas numpy requests python-dotenv boto3
cp .env.example .env      # fill in credentials
python src/universe.py    # -> results/halal_stocks.json
python src/flatfiles.py 2026-01-01 2026-05-29   # -> data/market_data.duckdb
python src/backtest.py    # -> results/backtest_{asis,fixed}.csv + summary
python src/summary.py     # side-by-side stats + head-to-head
```

## Methodology (backtest)

- Walk-forward: model retrains at the start of each month on all prior feature
  days (expanding window); first 25 days are warm-up.
- Daily flow mirrors the live system: gap-scanner ranks halal day-bars by
  `pct × log(volume)` (price ≥ $5), top 40 → features → model score → top 20
  watchlist → enter up to `MAX_POSITIONS=2` after a 2-bar up-confirmation →
  TP/SL bracket → re-enter freed slots → force-close 15:55 ET.
- **"asis"** and **"fixed"** see identical signals; only execution accounting
  differs, isolating the impact of the bugs.

## Caveats

- Bracket fills use 1-minute OHLC touch, not tick data — fixed mode assumes
  SL-first within a bar (conservative) + 10 bps slippage/side. Real fills vary.
- The halal universe is an EODHD-derived approximation, broader than the
  original `halal_stocks.json` (which was not in the repo).
