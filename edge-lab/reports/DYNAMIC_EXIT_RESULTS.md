# Dynamic exit results
_Generated 2026-07-06 04:23 UTC · Data: SYNTHETIC random-walk data (scripts/make_synthetic_data.py) — plumbing check only_

> **WARNING: SYNTHETIC DATA.** These numbers verify code plumbing only. They are NOT evidence of any market edge.


How TP is chosen: quantile (q40) of model-predicted MFE, bounded to 0.5–3.0x ATR.

How SL is chosen: quantile (q75) of model-predicted |MAE|, bounded to 0.4–2.0x ATR.

Why: TP sits where favorable excursions actually land often enough to be hit; SL sits outside typical noise. P(win)/EV are then estimated by replaying the levels on historical analogs, with ties counted as losses.

| variant | trades | win rate | expectancy | max DD |
|---|---|---|---|---|
| dynamic_quantile | 9 | 0.3333 | -0.00499 | -0.1249 |
| atr_1.5_1.0 | 9 | 0.3333 | -0.00499 | -0.1249 |
| atr_1.0_0.7 | 9 | 0.3333 | -0.00889 | -0.1051 |
| trail_1pct | 9 | 0.1111 | -0.00547 | -0.0483 |
| breakeven_after_1pct | 9 | 0.1111 | -0.00805 | -0.1162 |
