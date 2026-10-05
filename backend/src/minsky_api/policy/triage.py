"""Triage: which back-office queue a case goes to, how urgent it is, and when it is due.

The bank's data shows no triage today: critical and low-priority disputes take the same ~15.6 days and
breach the SLA at the same ~20 % (docs/disputes_findings.md). Every case the system opens or hands off
gets a priority, a queue and a due time from these rules, so the console can order the work.

Pure functions, no I/O, like policy.disputes. The targets are a SYNTHETIC policy written for the
hackathon (docs/dispute_policy.md, "Triage"); the data has no SLA deadline to derive them from.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum


class Priority(StrEnum):
    # Values match complaints.priority, so bank.resolution_benchmarks can be looked up by priority.
    CRITICAL = "Critical"
    HIGH = "High"
    MEDIUM = "Medium"
    LOW = "Low"


class Queue(StrEnum):
    FRAUD = "fraud"
    DISPUTES = "disputes"
    GENERAL = "general"  # out of scope, clarification limit, customer asked for a person


class CaseKind(StrEnum):
    DISPUTE = "dispute"  # opened automatically (rule D09): the back office investigates and decides
    HANDOFF = "handoff"  # a person must take the conversation's case


@dataclass(frozen=True)
class TriageConfig:
    critical_target: timedelta = timedelta(hours=4)
    high_target: timedelta = timedelta(days=2)
    medium_target: timedelta = timedelta(days=5)
    low_target: timedelta = timedelta(days=10)
    high_amount_usd: float = 500.0  # same threshold as the dispute policy's automatic limit (D07)


@dataclass(frozen=True)
class TriageDecision:
    priority: Priority
    queue: Queue
    due_at: datetime
    reason: str  # why this priority, cited in the console


DEFAULT_TRIAGE = TriageConfig()

# Sort key for the console: lower comes first.
PRIORITY_RANK = {Priority.CRITICAL: 0, Priority.HIGH: 1, Priority.MEDIUM: 2, Priority.LOW: 3}


def _target(priority: Priority, config: TriageConfig) -> timedelta:
    return {
        Priority.CRITICAL: config.critical_target,
        Priority.HIGH: config.high_target,
        Priority.MEDIUM: config.medium_target,
        Priority.LOW: config.low_target,
    }[priority]


def triage(
    *,
    kind: CaseKind,
    rule_id: str | None,
    amount_usd: float | None,
    card_blocked: bool,
    created_at: datetime,
    config: TriageConfig = DEFAULT_TRIAGE,
) -> TriageDecision:
    """First match wins, from the most to the least urgent."""
    if (rule_id or "").startswith("D06") or card_blocked:
        priority, queue, reason = Priority.CRITICAL, Queue.FRAUD, "possible fraud: the card may be compromised"
    elif amount_usd is not None and amount_usd > config.high_amount_usd:
        priority, queue, reason = Priority.HIGH, Queue.DISPUTES, f"amount above USD {config.high_amount_usd:,.0f}"
    elif kind == CaseKind.HANDOFF and (rule_id or "").startswith("D08"):
        priority, queue, reason = Priority.MEDIUM, Queue.DISPUTES, "repeat complainer"
    elif kind == CaseKind.HANDOFF and rule_id is not None and rule_id.startswith("D"):
        priority, queue, reason = Priority.MEDIUM, Queue.DISPUTES, "the policy sends this case to an agent"
    elif kind == CaseKind.HANDOFF:
        priority, queue, reason = Priority.MEDIUM, Queue.GENERAL, "the assistant could not complete the request"
    else:
        priority, queue, reason = Priority.LOW, Queue.DISPUTES, "eligible dispute opened automatically"
    return TriageDecision(priority=priority, queue=queue, due_at=created_at + _target(priority, config), reason=reason)
