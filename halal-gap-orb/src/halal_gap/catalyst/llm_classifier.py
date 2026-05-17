"""OpenAI-backed catalyst classifier with disk cache and daily cost cap.

Cache key includes PROMPT_VERSION so re-running with a different prompt
invalidates the cache automatically.

When `OPENAI_API_KEY` is unset, the classifier falls back to a deterministic
heuristic so the rest of the pipeline (and tests) keep working in offline mode.
The fallback result is flagged with `skipped_reason="no_api_key"`.
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from halal_gap.catalyst.cost_tracker import (
    can_spend,
    estimate_cost,
    record_spend,
    spent_today,
)
from halal_gap.catalyst.models import (
    ClassifierOutput,
    ClassifierResult,
    NewsItem,
)
from halal_gap.utils.config import cache_root, settings
from halal_gap.utils.logging import log


PROMPT_VERSION = "v1"

SYSTEM_PROMPT = """You are a sell-side equity desk analyst classifying the
catalyst behind an unusual pre-market gap in a US-listed stock.

You receive: the ticker, the as-of timestamp, the observed gap percentage,
and a list of news headlines + bodies published BEFORE the as-of timestamp.

Return a strict JSON object with these fields:
  direction      : "bullish" | "bearish" | "neutral"
  strength       : integer 1-5  (1 = trivial reminder, 5 = major catalyst)
  confidence     : float 0.0-1.0
  catalyst_type  : "earnings" | "guidance" | "analyst" | "fda" | "mna" |
                   "contract" | "product" | "legal" | "macro" | "other" | "none"
  summary        : <=400 chars, plain English, no markdown

Rules:
- Be skeptical: vague reminders, anniversaries, or sector commentary => strength <= 2.
- Real earnings beats/misses, FDA decisions, M&A news, hard guidance =>
  strength 4-5 with confidence > 0.7.
- If no items materially explain the gap, return direction="neutral",
  strength=1, catalyst_type="none".
- Do not invent facts. Stick to what the supplied items say."""


def _safe_model(model: str) -> str:
    """Defensive: ensure we never call a model not in our pricing table."""
    from halal_gap.catalyst.cost_tracker import PRICING_PER_1K
    return model if model in PRICING_PER_1K else "gpt-4o-mini"


def _cache_dir() -> Path:
    d = cache_root() / "catalyst"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _cache_key(symbol: str, items: list[NewsItem]) -> str:
    """Stable hash over symbol + prompt version + (title, published_at) of items."""
    payload = {
        "v": PROMPT_VERSION,
        "symbol": symbol.upper(),
        "items": [
            {"t": it.title, "ts": it.published_at.isoformat(), "src": it.source}
            for it in items
        ],
    }
    blob = json.dumps(payload, sort_keys=True).encode()
    return hashlib.sha256(blob).hexdigest()[:24]


def _cache_read(key: str) -> ClassifierResult | None:
    p = _cache_dir() / f"{key}.json"
    if not p.exists():
        return None
    try:
        with p.open("r", encoding="utf-8") as f:
            return ClassifierResult.model_validate_json(f.read())
    except Exception as exc:  # noqa: BLE001
        log.warning(f"catalyst cache read failed {p}: {exc}")
        return None


def _cache_write(key: str, result: ClassifierResult) -> None:
    p = _cache_dir() / f"{key}.json"
    with p.open("w", encoding="utf-8") as f:
        f.write(result.model_dump_json(indent=2))


def _heuristic_fallback(
    symbol: str, as_of: datetime, items: list[NewsItem]
) -> ClassifierOutput:
    """Keyword-based fallback when no API key is configured.

    Deliberately weak: returns neutral/low-strength unless obvious tokens
    appear in titles. Never use these in a real performance evaluation.
    """
    bullish_kw = ("beat", "raises guidance", "upgrade", "fda approval", "acquires")
    bearish_kw = ("miss", "cuts guidance", "downgrade", "investigation", "recall", "lawsuit")
    text = " ".join(it.title.lower() for it in items)
    if any(k in text for k in bullish_kw):
        return ClassifierOutput(
            direction="bullish",
            strength=3,
            confidence=0.4,
            catalyst_type="other",
            summary="fallback heuristic: bullish keywords detected",
        )
    if any(k in text for k in bearish_kw):
        return ClassifierOutput(
            direction="bearish",
            strength=3,
            confidence=0.4,
            catalyst_type="other",
            summary="fallback heuristic: bearish keywords detected",
        )
    return ClassifierOutput(
        direction="neutral",
        strength=1,
        confidence=0.3,
        catalyst_type="none" if not items else "other",
        summary="fallback heuristic: no clear directional signal",
    )


def _build_user_prompt(
    symbol: str, as_of: datetime, gap_pct: float, items: list[NewsItem]
) -> str:
    lines = [
        f"Symbol: {symbol}",
        f"As-of (NY): {as_of.isoformat()}",
        f"Pre-market gap: {gap_pct*100:+.2f}%",
        f"News items ({len(items)}):",
    ]
    for idx, it in enumerate(items, 1):
        lines.append(
            f"{idx}. [{it.published_at.strftime('%H:%M')}|{it.source}] {it.title}"
        )
        if it.body and it.body.lower() != it.title.lower():
            lines.append(f"   {it.body[:500]}")
    return "\n".join(lines)


def _openai_classify(
    symbol: str,
    as_of: datetime,
    gap_pct: float,
    items: list[NewsItem],
    model: str,
) -> tuple[ClassifierOutput, int, int]:
    """One OpenAI call. Returns (parsed, in_tokens, out_tokens)."""
    from openai import OpenAI  # local import: optional dependency at runtime

    client = OpenAI()
    schema = {
        "name": "catalyst_classification",
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "direction": {"type": "string", "enum": ["bullish", "bearish", "neutral"]},
                "strength": {"type": "integer", "minimum": 1, "maximum": 5},
                "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                "catalyst_type": {
                    "type": "string",
                    "enum": [
                        "earnings", "guidance", "analyst", "fda", "mna",
                        "contract", "product", "legal", "macro", "other", "none",
                    ],
                },
                "summary": {"type": "string"},
            },
            "required": ["direction", "strength", "confidence", "catalyst_type", "summary"],
        },
        "strict": True,
    }
    user = _build_user_prompt(symbol, as_of, gap_pct, items)
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user},
        ],
        response_format={"type": "json_schema", "json_schema": schema},
        temperature=0.0,
    )
    content = resp.choices[0].message.content or "{}"
    parsed = ClassifierOutput.model_validate_json(content)
    usage = resp.usage
    in_tok = int(getattr(usage, "prompt_tokens", 0) or 0)
    out_tok = int(getattr(usage, "completion_tokens", 0) or 0)
    return parsed, in_tok, out_tok


def classify(
    symbol: str,
    as_of: datetime,
    gap_pct: float,
    items: list[NewsItem],
    *,
    model: str | None = None,
    force_refresh: bool = False,
) -> ClassifierResult:
    """Classify the catalyst behind `symbol`'s gap as of `as_of`.

    Order:
        1. If no items, return a 'neutral / none' result without spending tokens.
        2. Disk-cache lookup keyed by (symbol, prompt version, item titles+ts).
        3. Cost-cap gate: if today's budget exhausted, return a skipped result.
        4. If OPENAI_API_KEY unset, run the heuristic fallback (skipped_reason set).
        5. Otherwise call OpenAI, record cost, persist to cache.
    """
    model = _safe_model(model or settings()["llm"]["model"])

    if not items:
        return ClassifierResult(
            symbol=symbol,
            as_of=as_of,
            prompt_version=PROMPT_VERSION,
            item_count=0,
            output=ClassifierOutput(
                direction="neutral",
                strength=1,
                confidence=0.5,
                catalyst_type="none",
                summary="no news in lookback window",
            ),
            model=model,
            skipped_reason="no_news",
        )

    key = _cache_key(symbol, items)
    if not force_refresh:
        cached = _cache_read(key)
        if cached is not None:
            cached.cached = True
            return cached

    if not can_spend(as_of.date()):
        log.warning(
            f"LLM cost cap reached for {as_of.date()} (spent ${spent_today(as_of.date()):.2f})"
        )
        return ClassifierResult(
            symbol=symbol,
            as_of=as_of,
            prompt_version=PROMPT_VERSION,
            item_count=len(items),
            output=_heuristic_fallback(symbol, as_of, items),
            model=model,
            skipped_reason="cost_cap",
        )

    if not os.environ.get("OPENAI_API_KEY"):
        result = ClassifierResult(
            symbol=symbol,
            as_of=as_of,
            prompt_version=PROMPT_VERSION,
            item_count=len(items),
            output=_heuristic_fallback(symbol, as_of, items),
            model=model,
            skipped_reason="no_api_key",
        )
        _cache_write(key, result)
        return result

    try:
        parsed, in_tok, out_tok = _openai_classify(symbol, as_of, gap_pct, items, model)
    except Exception as exc:  # noqa: BLE001
        log.warning(f"OpenAI call failed for {symbol} @ {as_of}: {exc}")
        return ClassifierResult(
            symbol=symbol,
            as_of=as_of,
            prompt_version=PROMPT_VERSION,
            item_count=len(items),
            output=_heuristic_fallback(symbol, as_of, items),
            model=model,
            skipped_reason=f"api_error:{type(exc).__name__}",
        )

    cost = estimate_cost(model, in_tok, out_tok)
    record_spend(cost, d=as_of.date())
    result = ClassifierResult(
        symbol=symbol,
        as_of=as_of,
        prompt_version=PROMPT_VERSION,
        item_count=len(items),
        output=parsed,
        model=model,
        input_tokens=in_tok,
        output_tokens=out_tok,
        cost_usd=cost,
    )
    _cache_write(key, result)
    return result
