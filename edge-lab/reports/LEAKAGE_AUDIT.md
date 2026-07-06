# Leakage audit
_Generated 2026-07-06 19:00 UTC · Data: REAL Massive flat files_


## Structural future-invariance check
- tickers checked: 25
- decision times: ['2024-07-03 09:45:00-04:00', '2024-07-03 11:00:00-04:00', '2024-07-03 14:30:00-04:00']
- clean: **True**
- leaking features: `{}`

## Suspicious-correlation scan (|corr with same-day outcome| ≥ 0.90)
_none flagged_

## What this audit does NOT prove
- It cannot detect leakage introduced upstream in raw data.
- It cannot detect survivorship bias by itself.
- Passing is necessary, not sufficient.
