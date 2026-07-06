"""Research memory. Every tested idea -- winner or loser -- is appended to
an immutable ledger. Failed tests are evidence, not embarrassments."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from src.config import CFG

LEDGER = CFG.paths.experiments / "ledger.jsonl"
IDEAS_MD = CFG.paths.experiments / "ideas_log.md"

TEMPLATE = """
## {exp_id} — {idea}
- **Idea:** {idea}
- **Reason:** {reason}
- **Data needed:** {data_needed}
- **Test method:** {test_method}
- **Result:** {result}
- **Accepted or rejected:** {verdict}
- **Why:** {why}
- **Logged:** {ts}
"""


def log_experiment(idea: str, reason: str, data_needed: str, test_method: str,
                   result: str, verdict: str, why: str, extra: dict | None = None) -> str:
    exp_id = f"EXP-{datetime.now(timezone.utc):%Y%m%d}-{uuid.uuid4().hex[:6]}"
    rec = {"exp_id": exp_id, "ts": datetime.now(timezone.utc).isoformat(),
           "idea": idea, "reason": reason, "data_needed": data_needed,
           "test_method": test_method, "result": result, "verdict": verdict,
           "why": why, "extra": extra or {}}
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with open(LEDGER, "a") as f:
        f.write(json.dumps(rec, default=str) + "\n")
    with open(IDEAS_MD, "a") as f:
        f.write(TEMPLATE.format(**{**rec, "ts": rec["ts"]}))
    if verdict.lower().startswith("reject"):
        fp = CFG.paths.failed_ideas / f"{exp_id}.md"
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(TEMPLATE.format(**{**rec, "ts": rec["ts"]}))
    return exp_id


def load_ledger() -> list[dict]:
    if not LEDGER.exists():
        return []
    return [json.loads(line) for line in LEDGER.read_text().splitlines() if line.strip()]
