"""Renders the required markdown reports from raw result dicts. Every
report carries a data-provenance banner so synthetic runs can never be
mistaken for real evidence."""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pandas as pd

from src.config import CFG


def _banner(provenance: str) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    warn = ""
    if "synthetic" in provenance.lower():
        warn = ("\n> **WARNING: SYNTHETIC DATA.** These numbers verify code "
                "plumbing only. They are NOT evidence of any market edge.\n")
    return f"_Generated {ts} · Data: {provenance}_\n{warn}\n"


def _card_md(card: dict) -> str:
    if not card:
        return "_no results_\n"
    lines = []
    for k, v in card.items():
        if isinstance(v, dict):
            lines.append(f"- **{k}:** `{json.dumps(v, default=str)}`")
        else:
            lines.append(f"- **{k}:** {v}")
    return "\n".join(lines) + "\n"


def monthly_md(monthly: pd.DataFrame) -> str:
    if monthly is None or len(monthly) == 0:
        return "_no monthly data_\n"
    out = ["| month | net_ret | n_trades |", "|---|---|---|"]
    for idx, row in monthly.iterrows():
        out.append(f"| {idx:%Y-%m} | {row['net_ret']:+.3%} | {int(row['n_trades'])} |")
    return "\n".join(out) + "\n"


def write_walk_forward_report(result: dict, provenance: str, name: str) -> None:
    p = CFG.paths.reports / "WALK_FORWARD_RESULTS.md"
    body = [f"# Walk-forward results — {name}", _banner(provenance)]
    body.append(f"Folds: {result.get('n_folds')} · configurations tried: {result.get('n_trials', 'n/a')}\n")
    for fl in result.get("fold_logs", []):
        body.append(f"## Fold {fl['fold']}")
        body.append(f"- chosen params: `{fl.get('chosen')}`")
        body.append(f"- validation expectancy: {fl.get('val_expectancy')}")
        body.append("### Test (out-of-sample for this fold)")
        body.append(_card_md(fl.get("test", {})))
    body.append("## Aggregate out-of-sample (all fold test windows)")
    body.append(_card_md(result.get("oos_card", {})))
    p.write_text("\n".join(body))


def write_one_trade_per_day_report(filtered_card: dict, forced_card: dict,
                                   monthly: pd.DataFrame, provenance: str) -> None:
    p = CFG.paths.reports / "ONE_TRADE_PER_DAY_RESULTS.md"
    body = ["# One-trade-per-day results", _banner(provenance),
            "## Filtered mode (trade only when EV > 0)", _card_md(filtered_card),
            "## Forced mode (exactly one trade every day, least-bad allowed)",
            _card_md(forced_card),
            "Forced-mode results are reported separately and never averaged "
            "into the filtered results.\n",
            "## Monthly breakdown (filtered)", monthly_md(monthly)]
    p.write_text("\n".join(body))


def write_dynamic_exit_report(rows: list[dict], provenance: str) -> None:
    p = CFG.paths.reports / "DYNAMIC_EXIT_RESULTS.md"
    body = ["# Dynamic exit results", _banner(provenance),
            "How TP is chosen: quantile (q40) of model-predicted MFE, bounded "
            "to 0.5–3.0x ATR.\n",
            "How SL is chosen: quantile (q75) of model-predicted |MAE|, bounded "
            "to 0.4–2.0x ATR.\n",
            "Why: TP sits where favorable excursions actually land often "
            "enough to be hit; SL sits outside typical noise. P(win)/EV are "
            "then estimated by replaying the levels on historical analogs, "
            "with ties counted as losses.\n",
            "| variant | trades | win rate | expectancy | max DD |",
            "|---|---|---|---|---|"]
    for r in rows:
        c = r.get("card", {})
        body.append(f"| {r['name']} | {c.get('total_trades', 0)} | "
                    f"{c.get('win_rate', 'n/a')} | {c.get('expectancy', 'n/a')} | "
                    f"{c.get('max_drawdown', 'n/a')} |")
    p.write_text("\n".join(body) + "\n")


def write_leakage_audit(audit: dict, suspicious: pd.DataFrame, provenance: str) -> None:
    p = CFG.paths.reports / "LEAKAGE_AUDIT.md"
    body = ["# Leakage audit", _banner(provenance),
            "## Structural future-invariance check",
            f"- tickers checked: {audit.get('checked_tickers')}",
            f"- decision times: {audit.get('decision_times')}",
            f"- clean: **{audit.get('clean')}**",
            f"- leaking features: `{audit.get('leaks')}`\n",
            "## Suspicious-correlation scan (|corr with same-day outcome| ≥ 0.90)"]
    body.append(suspicious.to_markdown(index=False) if len(suspicious) else "_none flagged_")
    body.append("\n## What this audit does NOT prove\n"
                "- It cannot detect leakage introduced upstream in raw data.\n"
                "- It cannot detect survivorship bias by itself.\n"
                "- Passing is necessary, not sufficient.\n")
    p.write_text("\n".join(body))
