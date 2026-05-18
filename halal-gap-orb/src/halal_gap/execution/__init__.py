"""Stage 4: paper-trading loop with broker, kill switch, and ledger."""
from halal_gap.execution.broker import Broker, FilledPosition, PaperBroker, results_to_frame
from halal_gap.execution.kill_switch import KillSwitch, KillSwitchDecision
from halal_gap.execution.ledger import (
    append_equity,
    append_trades,
    daily_summary,
    load_equity,
    load_trades,
)
from halal_gap.execution.runner import DailyOrchestrator, DayReport

__all__ = [
    "Broker",
    "DailyOrchestrator",
    "DayReport",
    "FilledPosition",
    "KillSwitch",
    "KillSwitchDecision",
    "PaperBroker",
    "append_equity",
    "append_trades",
    "daily_summary",
    "load_equity",
    "load_trades",
    "results_to_frame",
]
