"""One-trade-per-day selector.

For a given date and a frozen strategy: generate candidates (pre-decision
data only), gate, rank, pick one, and write a full pick report including
why alternatives were rejected. If nothing has positive EV, the least-bad
candidate is shown under an explicit FORCED TRADE WARNING and is never
presented as a proven edge.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import pandas as pd

from src.config import CFG
from src.data.feature_builder import PointInTimeView
from src.data.massive_loader import load_minute_day
from src.selector.candidate_ranker import gate_candidate, order_candidates
from src.utils.logging import get_logger

log = get_logger(__name__)


@dataclass
class DailyPick:
    date: str
    selected: dict | None
    forced: bool
    rejected: list = field(default_factory=list)
    no_candidates: bool = False

    def to_markdown(self) -> str:
        if self.no_candidates:
            return (f"# Daily pick — {self.date}\n\nNo candidates passed the "
                    "gates today. No trade is a valid output.\n")
        c = self.selected
        conf = _confidence(c)
        lines = [f"# Daily pick — {self.date}\n"]
        if self.forced:
            lines += ["**FORCED TRADE WARNING:**",
                      "No candidate passed the positive-EV threshold.",
                      "This is the least-bad candidate, not a proven edge.\n"]
        lines += [
            f"- Date: {self.date}",
            f"- Selected ticker: {c['ticker']}",
            f"- Entry time: {pd.Timestamp(c['decision_ts']).strftime('%H:%M ET')}",
            f"- Entry price (last known, est.): {c['last_price']:.2f}",
            f"- TP: {c['last_price'] * (1 + c['tp_pct']):.2f}  (+{c['tp_pct']:.2%})",
            f"- SL: {c['last_price'] * (1 - c['sl_pct']):.2f}  (-{c['sl_pct']:.2%})",
            f"- Expected value (after modeled costs): {c['ev']:+.2%}",
            f"- Win probability (historical analogs): {_fmt_p(c.get('p_win'))}",
            f"- Liquidity score (20d median $vol): {_fmt_usd(c.get('med_dollar_vol_20d_prev'))}",
            f"- Risk score (ATR% prev): {_fmt_p(c.get('atr_pct_prev'))}",
            f"- Reason selected: {_reason(c)}",
            f"- Exit logic: {c.get('exit_reasoning', 'n/a')}",
            f"- Confidence: {conf}",
            f"- Known weaknesses: {_weaknesses(c, self.forced)}",
            "",
            "## Rejected alternatives",
        ]
        if not self.rejected:
            lines.append("- none (no other candidates)")
        for r in self.rejected:
            lines.append(f"- {r['ticker']} @ {pd.Timestamp(r['decision_ts']).strftime('%H:%M')}: "
                         f"score={r.get('score', float('nan')):.3f}, EV={_fmt_p(r.get('ev'))} — {r['why_rejected']}")
        lines.append("\n*Selection is mechanical (data + frozen models). "
                     "No LLM chose this trade.*\n")
        return "\n".join(lines)


def _fmt_p(x):
    return f"{x:.1%}" if isinstance(x, (int, float)) and not math.isnan(x) else "n/a"


def _fmt_usd(x):
    return f"${x:,.0f}" if isinstance(x, (int, float)) and not math.isnan(x) else "n/a"


def _reason(c: dict) -> str:
    bits = []
    g = c.get("gap_pct")
    if isinstance(g, float) and not math.isnan(g):
        bits.append(f"gap {g:+.1%}")
    rv = c.get("rvol")
    if isinstance(rv, float) and not math.isnan(rv):
        bits.append(f"rvol {rv:.1f}x")
    if c.get("orb_breakout") == 1.0:
        bits.append("opening-range breakout")
    dv = c.get("dist_vwap")
    if isinstance(dv, float) and not math.isnan(dv) and dv > 0:
        bits.append("above session VWAP")
    n = c.get("ev_evidence_n")
    if n:
        bits.append(f"EV from {n} historical analogs")
    return ", ".join(bits) or "highest composite score among gated candidates"


def _weaknesses(c: dict, forced: bool) -> str:
    w = ["slippage/spread are modeled assumptions, not quotes",
         "flat-file data is unadjusted for corporate actions"]
    if forced:
        w.insert(0, "negative/zero expected value — forced pick")
    n = c.get("ev_evidence_n")
    if n and n < 200:
        w.append(f"thin evidence base ({n} analogs)")
    if isinstance(c.get("atr_pct_prev"), float) and c["atr_pct_prev"] > 0.06:
        w.append("high-volatility name; tail risk elevated")
    return "; ".join(w)


def _confidence(c: dict) -> str:
    n = c.get("ev_evidence_n") or 0
    p = c.get("p_win")
    ev = c.get("ev") or 0
    score = 0.0
    score += min(n / 500.0, 1.0) * 0.5
    if isinstance(p, float) and not math.isnan(p):
        score += max(0.0, min((p - 0.5) / 0.2, 1.0)) * 0.25
    score += max(0.0, min(ev / 0.02, 1.0)) * 0.25
    label = "low" if score < 0.35 else ("medium" if score < 0.65 else "high")
    return f"{label} ({score:.2f})"


def select_for_date(strategy, d, daily_panel: pd.DataFrame,
                    minute_loader=load_minute_day, write: bool = True) -> DailyPick:
    mdf = minute_loader(d)
    if mdf is None or len(mdf) == 0:
        pick = DailyPick(date=str(pd.Timestamp(d).date()), selected=None,
                         forced=False, no_candidates=True)
        return _maybe_write(pick, write)
    view = PointInTimeView.build(d, mdf, daily_panel)
    cands = strategy.candidates_for_day(view)
    gated = []
    for c in cands:
        ok, reasons = gate_candidate(c)
        c["_gate_ok"], c["_gate_reasons"] = ok, reasons
        gated.append(c)
    ranked = order_candidates(gated)
    passing = [c for c in ranked if c["_gate_ok"] and (c.get("ev") or 0) > 0]

    if passing:
        chosen, forced = passing[0], False
    elif ranked:
        chosen, forced = ranked[0], True
    else:
        pick = DailyPick(date=str(pd.Timestamp(d).date()), selected=None,
                         forced=False, no_candidates=True)
        return _maybe_write(pick, write)

    rejected = []
    for c in ranked:
        if c is chosen or len(rejected) >= 3:
            continue
        why = "; ".join(c["_gate_reasons"]) if c["_gate_reasons"] else "lower composite score than selection"
        if (c.get("ev") or 0) <= 0:
            why = "non-positive EV; " + why
        rejected.append({**c, "why_rejected": why})

    pick = DailyPick(date=str(pd.Timestamp(d).date()), selected=chosen,
                     forced=forced, rejected=rejected)
    return _maybe_write(pick, write)


def _maybe_write(pick: DailyPick, write: bool) -> DailyPick:
    if write:
        CFG.paths.daily_picks.mkdir(parents=True, exist_ok=True)
        (CFG.paths.daily_picks / f"{pick.date}.md").write_text(pick.to_markdown())
        log.info("daily pick written for %s (forced=%s)", pick.date, pick.forced)
    return pick
