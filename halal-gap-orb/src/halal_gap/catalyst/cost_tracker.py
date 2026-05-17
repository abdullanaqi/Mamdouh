"""Daily LLM spend tracker, JSON-persisted under data/cache/llm_cost.json.

Hard cap is config llm.max_daily_spend_usd. When the cap is reached, callers
should treat the classifier as unavailable for the remainder of the NY trading
day rather than silently returning bogus results.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from threading import Lock

from halal_gap.utils.config import cache_root, settings


_LOCK = Lock()


# OpenAI list pricing in USD per 1k tokens. Update when pricing changes.
PRICING_PER_1K = {
    "gpt-4o-mini": {"in": 0.00015, "out": 0.0006},
    "gpt-4o":      {"in": 0.005,   "out": 0.015},
    "gpt-4.1-mini": {"in": 0.0004, "out": 0.0016},
    "gpt-4.1":     {"in": 0.003,   "out": 0.012},
}


def estimate_cost(model: str, in_tokens: int, out_tokens: int) -> float:
    """Convert (in_tokens, out_tokens) into USD using the static price table.

    Unknown models fall back to gpt-4o-mini pricing to stay conservative.
    """
    p = PRICING_PER_1K.get(model, PRICING_PER_1K["gpt-4o-mini"])
    return (in_tokens / 1000.0) * p["in"] + (out_tokens / 1000.0) * p["out"]


def _ledger_path() -> Path:
    return cache_root() / "llm_cost.json"


def _load() -> dict[str, float]:
    p = _ledger_path()
    if not p.exists():
        return {}
    try:
        with p.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return {k: float(v) for k, v in data.items()}
    except (OSError, json.JSONDecodeError):
        return {}


def _save(d: dict[str, float]) -> None:
    p = _ledger_path()
    with p.open("w", encoding="utf-8") as f:
        json.dump(d, f, indent=2, sort_keys=True)


def spent_today(d: date | None = None) -> float:
    """Total spend in USD on `d` (defaults to today)."""
    d = d or date.today()
    return _load().get(d.isoformat(), 0.0)


def can_spend(d: date | None = None) -> bool:
    """True iff the daily cap has not yet been reached."""
    cap = float(settings()["llm"]["max_daily_spend_usd"])
    return spent_today(d) < cap


def record_spend(amount_usd: float, d: date | None = None) -> float:
    """Add `amount_usd` to the day's ledger and return the new running total."""
    d = d or date.today()
    with _LOCK:
        ledger = _load()
        cur = ledger.get(d.isoformat(), 0.0) + max(0.0, amount_usd)
        ledger[d.isoformat()] = cur
        _save(ledger)
        return cur


def reset(d: date | None = None) -> None:
    """Wipe a specific day's entry. Test-only escape hatch."""
    d = d or date.today()
    with _LOCK:
        ledger = _load()
        ledger.pop(d.isoformat(), None)
        _save(ledger)
