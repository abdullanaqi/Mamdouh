"""Final gates + ordering for daily candidates, with explicit rejection
reasons so the selector can report why alternatives lost."""
from __future__ import annotations

import math

from src.config import CFG


def gate_candidate(c: dict) -> tuple[bool, list[str]]:
    reasons = []
    mdv = c.get("med_dollar_vol_20d_prev")
    if mdv is None or (isinstance(mdv, float) and math.isnan(mdv)) or mdv < CFG.universe.min_median_dollar_vol_20d:
        reasons.append("liquidity below gate (20d median dollar volume)")
    sp = c.get("spread_proxy_bps_prev")
    if isinstance(sp, float) and not math.isnan(sp) and sp > CFG.universe.max_spread_proxy_bps:
        reasons.append("spread proxy too wide")
    px = c.get("last_price", 0)
    if not (CFG.universe.min_price <= px <= CFG.universe.max_price):
        reasons.append("price outside allowed range")
    if c.get("suspect_split"):
        reasons.append("suspected unadjusted split in history (data risk)")
    ev = c.get("ev")
    if ev is None or (isinstance(ev, float) and math.isnan(ev)):
        reasons.append("no EV evidence")
    n = c.get("ev_evidence_n")
    if n is not None and n < 50:
        reasons.append(f"EV estimated from only {n} historical analogs")
    return (len(reasons) == 0, reasons)


def order_candidates(cands: list[dict]) -> list[dict]:
    return sorted(cands, key=lambda c: c.get("score", float("-inf")), reverse=True)
