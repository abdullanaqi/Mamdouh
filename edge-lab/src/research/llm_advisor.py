"""LLM research advisor (optional).

Uses the Anthropic API for exactly the roles the spec allows: hypothesis
generation, experiment design review, leakage-assumption audits, and
report drafting. IT NEVER SELECTS TRADES; every suggestion must be turned
into code and tested before it means anything.

API docs: https://docs.claude.com/en/api/overview
Model is configurable via ANTHROPIC_MODEL (default: claude-sonnet-4-6).
"""
from __future__ import annotations

from src.config import CFG
from src.research.experiment_runner import log_experiment

SYSTEM = (
    "You are a skeptical quantitative research reviewer. You never invent "
    "data, results, or performance numbers. You flag lookahead bias, "
    "survivorship bias, unrealistic fills, and multiple-testing risk. When "
    "evidence is missing you say so plainly."
)


def _client():
    try:
        import anthropic
    except ImportError as e:  # pragma: no cover
        raise ImportError("pip install anthropic to use the LLM advisor") from e
    if not CFG.llm.api_key:
        raise RuntimeError("Set ANTHROPIC_API_KEY in .env (never hardcode keys).")
    return anthropic.Anthropic(api_key=CFG.llm.api_key)


def ask(prompt: str) -> str:
    msg = _client().messages.create(
        model=CFG.llm.model,
        max_tokens=CFG.llm.max_tokens,
        system=SYSTEM,
        messages=[{"role": "user", "content": prompt}],
    )
    return "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")


def propose_hypotheses(research_context: str, n: int = 5) -> str:
    text = ask(
        f"Given this research context, propose {n} testable one-trade-per-day "
        f"long-only intraday hypotheses. For each give: Idea, Reason, Data "
        f"needed, Test method. Only features knowable before entry.\n\n"
        f"CONTEXT:\n{research_context}"
    )
    log_experiment(idea=f"LLM hypothesis batch ({n})", reason="idea generation",
                   data_needed="see batch", test_method="each must be coded and walk-forward tested",
                   result="NOT TESTED", verdict="pending", why="untested LLM suggestions carry zero evidentiary weight",
                   extra={"raw": text})
    return text


def audit_assumptions(report_text: str) -> str:
    return ask(
        "Audit the following research write-up for lookahead bias, leakage, "
        "unrealistic fills, hidden losing periods, survivorship bias, and "
        "overfitting. List concrete problems and what evidence would resolve "
        f"each.\n\n{report_text}"
    )
