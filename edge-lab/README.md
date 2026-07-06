# edge-lab

A truth-first research system that searches for a **one-trade-per-day,
long-only, intraday US-stock edge** using Massive.com (ex-Polygon) data.

The system is built to *find no edge* when there isn't one. It separates
in-sample fitting from out-of-sample testing, models real trading costs
pessimistically, refuses to look ahead, and reports "No valid edge found
yet" unless a strict, multi-part gate is passed on untouched data.

> **Status right now:** no real market data has been evaluated. The build
> and validation happened inside a sandbox with no network access to
> Massive, so every number produced so far is from **synthetic random-walk
> data** used to verify the machinery. See `README_TRUTH.md`. To get real
> results, run the four commands under "Real data workflow" on your own
> machine.

## Install

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env      # then paste your real keys into .env
```

Never commit `.env`. Keys belong only in that file (git-ignored).

## Real data workflow

```bash
# 1. Download flat files and build the point-in-time daily panel
python scripts/build_dataset.py --start 2023-01-01 --end 2025-12-31

# 2. Prove there is no lookahead in the features (writes reports/LEAKAGE_AUDIT.md)
python scripts/audit_for_leakage.py

# 3. Run the full research cycle (walk-forward, stress, edge gate, reports)
python scripts/run_research.py --provenance "REAL Massive flat files 2023-2025"

# 4. Produce a one-trade pick for a historical date (replays only prior data)
python scripts/generate_daily_pick.py --date 2025-09-15
```

Step 3 prints an honest verdict. On data with no real edge, that verdict
is **"NO VALID EDGE FOUND YET"** with the list of failed rules.

## Verify the plumbing without any data (synthetic)

```bash
python scripts/make_synthetic_data.py   # random-walk data in the repo layout
python scripts/smoke_synthetic.py       # full pipeline end-to-end
pytest -q                               # 26 unit tests
```

The smoke test is designed so that if the edge gate ever declares an edge
on random data, it exits non-zero — a false positive is treated as a bug.

## What the system will and won't do

- It will not tune on the test set. Parameters are chosen on a validation
  window; the test window is scored once with the frozen winner.
- It will not present a losing or unproven strategy as an edge. When forced
  to trade every day, forced picks are labeled **FORCED TRADE WARNING** and
  reported separately from filtered results.
- It will not let an LLM choose trades. The optional LLM advisor only
  proposes hypotheses and audits assumptions; every idea must be coded and
  walk-forward tested, and is logged with zero evidentiary weight until then.
- Live same-day picks are **not implemented**: flat files are end-of-day, so
  a live feed (Massive REST/WebSocket) is required. That client is an honest
  stub rather than invented endpoints — see `README_TRUTH.md`.

## Layout

```
src/
  config.py                 costs, sizing, sessions, walk-forward, weights
  data/                     loader, universe, point-in-time features, labels, leakage checks
  backtest/                 pessimistic execution simulator, metrics, backtester
  models/                   dynamic TP/SL, EV model, win-prob model, ranker
  research/                 strategy search, walk-forward, overfit battery, ledger, LLM advisor
  selector/                 candidate gating + one-trade-per-day selector
  reports/                  markdown report generation
scripts/                    build_dataset, run_research, run_walk_forward, generate_daily_pick,
                            audit_for_leakage, make_synthetic_data, smoke_synthetic
tests/                      26 tests: fills, no-lookahead, dynamic exits, metrics, selector
reports/                    generated analysis + EDGE_REPORT, FAILED_IDEAS, NEXT_STEPS
research/experiments/       ledger.jsonl + ideas_log.md (every idea, every result)
```

See `AGENTS.md` for the research protocol and the pre-publication checklist.
Not investment advice.
