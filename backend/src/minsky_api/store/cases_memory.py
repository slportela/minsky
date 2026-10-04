"""Process-local cases.* backend: unit tests, offline evals and runs without Postgres.

The Postgres implementation (cases_sql.SqlCasesBackend) has the same methods; tools depend on the
CasesBackend protocol (store.cases), so they do not know which one is behind them.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from threading import Lock
from typing import Any
from uuid import uuid4


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class DisputeRecord:
    dispute_id: str
    customer_id: str
    transaction_id: str
    reason: str
    status: str
    created_at: datetime


@dataclass(frozen=True)
class HandoffRecord:
    handoff_id: str
    customer_id: str
    reason: str
    rule_id: str | None
    facts: dict[str, Any]
    actions: tuple[str, ...]
    created_at: datetime


@dataclass(frozen=True)
class CardBlockRecord:
    product_id: str
    customer_id: str
    status: str
    blocked_at: datetime


@dataclass(frozen=True)
class CaseRecord:
    """One unit of back-office work: an automatically opened dispute or a handoff, with its triage."""

    case_id: str  # the dispute_id or handoff_id it tracks
    kind: str  # policy.triage.CaseKind
    customer_id: str
    rule_id: str | None
    reason: str
    priority: str  # policy.triage.Priority
    queue: str  # policy.triage.Queue
    triage_reason: str
    due_at: datetime
    status: str  # "new" | "in_progress" | "resolved"
    summary: str
    facts: dict[str, Any]
    actions: tuple[str, ...]
    open_questions: tuple[str, ...]
    expected_resolution_days: float | None
    created_at: datetime
    updated_at: datetime
    assigned_to: str | None = None
    resolution_note: str | None = None


CASE_STATUSES = ("new", "in_progress", "resolved")


class CaseTransitionError(ValueError):
    """A claim or resolve that the case's current state does not allow (the console shows the reason)."""


def sort_cases(records: list[CaseRecord]) -> tuple[CaseRecord, ...]:
    """Open work first, then by priority, then the earliest due time."""
    from minsky_api.policy.triage import PRIORITY_RANK, Priority

    return tuple(
        sorted(
            records,
            key=lambda r: (r.status == "resolved", PRIORITY_RANK[Priority(r.priority)], r.due_at, r.created_at),
        )
    )


def claimed(record: CaseRecord, agent_id: str) -> CaseRecord:
    if record.status == "resolved":
        raise CaseTransitionError("case_resolved")
    if record.status == "in_progress" and record.assigned_to != agent_id:
        raise CaseTransitionError("case_claimed_by_another_agent")
    return replace(record, status="in_progress", assigned_to=agent_id, updated_at=_now())


def resolved(record: CaseRecord, agent_id: str, note: str) -> CaseRecord:
    if record.status != "in_progress" or record.assigned_to != agent_id:
        raise CaseTransitionError("claim_the_case_first")
    return replace(record, status="resolved", resolution_note=note, updated_at=_now())


@dataclass(frozen=True)
class AuditRecord:
    tool: str
    session_id: str
    customer_id: str | None
    args_digest: str
    outcome: str
    reason: str | None
    at: datetime


@dataclass
class InMemoryCasesBackend:
    """Process-local disputes, handoffs, card-block overlay, and append-only audit."""

    _lock: Lock = field(default_factory=Lock, repr=False)
    _disputes_by_id: dict[str, DisputeRecord] = field(default_factory=dict, repr=False)
    _disputes_by_txn: dict[tuple[str, str], str] = field(default_factory=dict, repr=False)
    _handoffs_by_id: dict[str, HandoffRecord] = field(default_factory=dict, repr=False)
    _handoffs_by_key: dict[tuple[str, str], str] = field(default_factory=dict, repr=False)
    _blocks_by_product: dict[str, CardBlockRecord] = field(default_factory=dict, repr=False)
    _audit: list[AuditRecord] = field(default_factory=list, repr=False)
    _cases_by_id: dict[str, CaseRecord] = field(default_factory=dict, repr=False)

    def create_dispute(self, *, customer_id: str, transaction_id: str, reason: str) -> DisputeRecord:
        key = (customer_id, transaction_id)
        with self._lock:
            existing_id = self._disputes_by_txn.get(key)
            if existing_id is not None:
                return self._disputes_by_id[existing_id]
            dispute_id = f"DSP-{uuid4().hex[:12]}"
            record = DisputeRecord(
                dispute_id=dispute_id,
                customer_id=customer_id,
                transaction_id=transaction_id,
                reason=reason,
                status="open",
                created_at=_now(),
            )
            self._disputes_by_id[dispute_id] = record
            self._disputes_by_txn[key] = dispute_id
            return record

    def get_dispute(self, dispute_id: str) -> DisputeRecord | None:
        with self._lock:
            return self._disputes_by_id.get(dispute_id)

    def get_dispute_by_transaction(self, *, customer_id: str, transaction_id: str) -> DisputeRecord | None:
        with self._lock:
            dispute_id = self._disputes_by_txn.get((customer_id, transaction_id))
            if dispute_id is None:
                return None
            return self._disputes_by_id[dispute_id]

    def create_handoff(
        self,
        *,
        customer_id: str,
        reason: str,
        rule_id: str | None,
        facts: dict[str, Any],
        actions: tuple[str, ...],
        idempotency_key: str | None = None,
    ) -> HandoffRecord:
        with self._lock:
            key = (customer_id, idempotency_key) if idempotency_key is not None else None
            if key is not None and key in self._handoffs_by_key:
                return self._handoffs_by_id[self._handoffs_by_key[key]]
            handoff_id = f"HO-{uuid4().hex[:12]}"
            record = HandoffRecord(
                handoff_id=handoff_id,
                customer_id=customer_id,
                reason=reason,
                rule_id=rule_id,
                facts=dict(facts),
                actions=actions,
                created_at=_now(),
            )
            self._handoffs_by_id[handoff_id] = record
            if key is not None:
                self._handoffs_by_key[key] = handoff_id
            return record

    def get_handoff(self, handoff_id: str) -> HandoffRecord | None:
        with self._lock:
            return self._handoffs_by_id.get(handoff_id)

    def block_card(self, *, customer_id: str, product_id: str) -> CardBlockRecord:
        with self._lock:
            existing = self._blocks_by_product.get(product_id)
            if existing is not None and existing.customer_id == customer_id:
                return existing
            record = CardBlockRecord(
                product_id=product_id,
                customer_id=customer_id,
                status="Blocked",
                blocked_at=_now(),
            )
            self._blocks_by_product[product_id] = record
            return record

    def get_card_block(self, product_id: str) -> CardBlockRecord | None:
        with self._lock:
            return self._blocks_by_product.get(product_id)

    def append_audit(
        self,
        *,
        tool: str,
        session_id: str,
        customer_id: str | None,
        args_digest: str,
        outcome: str,
        reason: str | None = None,
    ) -> AuditRecord:
        record = AuditRecord(
            tool=tool,
            session_id=session_id,
            customer_id=customer_id,
            args_digest=args_digest,
            outcome=outcome,
            reason=reason,
            at=_now(),
        )
        with self._lock:
            self._audit.append(record)
        return record

    def list_audit(self) -> tuple[AuditRecord, ...]:
        with self._lock:
            return tuple(self._audit)

    def list_audit_for_customer(self, customer_id: str) -> tuple[AuditRecord, ...]:
        with self._lock:
            return tuple(a for a in self._audit if a.customer_id == customer_id)

    def enqueue_case(self, record: CaseRecord) -> CaseRecord:
        """Idempotent on case_id: a retried write returns the case already queued."""
        with self._lock:
            return self._cases_by_id.setdefault(record.case_id, record)

    def get_case(self, case_id: str) -> CaseRecord | None:
        with self._lock:
            return self._cases_by_id.get(case_id)

    def list_cases(self, *, status: str | None = None, queue: str | None = None) -> tuple[CaseRecord, ...]:
        with self._lock:
            records = [
                r
                for r in self._cases_by_id.values()
                if (status is None or r.status == status) and (queue is None or r.queue == queue)
            ]
        return sort_cases(records)

    def claim_case(self, case_id: str, agent_id: str) -> CaseRecord | None:
        with self._lock:
            record = self._cases_by_id.get(case_id)
            if record is None:
                return None
            self._cases_by_id[case_id] = claimed(record, agent_id)
            return self._cases_by_id[case_id]

    def resolve_case(self, case_id: str, agent_id: str, note: str) -> CaseRecord | None:
        with self._lock:
            record = self._cases_by_id.get(case_id)
            if record is None:
                return None
            self._cases_by_id[case_id] = resolved(record, agent_id, note)
            return self._cases_by_id[case_id]
