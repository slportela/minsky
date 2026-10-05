"""The cases.* interface the tools and the console depend on, whichever backend is behind it.

Two implementations: InMemoryCasesBackend (tests, offline evals) and SqlCasesBackend (Postgres, the
compose stack). Methods are synchronous: every call is one short indexed statement, and the async
tools call them inline (production would move them behind an async driver; docs/poc_to_prod.md).
"""

from __future__ import annotations

from typing import Any, Protocol

from minsky_api.store.cases_memory import (
    AuditRecord,
    CardBlockRecord,
    CaseRecord,
    DisputeRecord,
    HandoffRecord,
)


class CasesBackend(Protocol):
    def create_dispute(self, *, customer_id: str, transaction_id: str, reason: str) -> DisputeRecord: ...

    def get_dispute(self, dispute_id: str) -> DisputeRecord | None: ...

    def get_dispute_by_transaction(self, *, customer_id: str, transaction_id: str) -> DisputeRecord | None: ...

    def create_handoff(
        self,
        *,
        customer_id: str,
        reason: str,
        rule_id: str | None,
        facts: dict[str, Any],
        actions: tuple[str, ...],
        idempotency_key: str | None = None,
    ) -> HandoffRecord: ...

    def get_handoff(self, handoff_id: str) -> HandoffRecord | None: ...

    def block_card(self, *, customer_id: str, product_id: str) -> CardBlockRecord: ...

    def get_card_block(self, product_id: str) -> CardBlockRecord | None: ...

    def append_audit(
        self,
        *,
        tool: str,
        session_id: str,
        customer_id: str | None,
        args_digest: str,
        outcome: str,
        reason: str | None = None,
    ) -> AuditRecord: ...

    def list_audit(self) -> tuple[AuditRecord, ...]: ...

    def list_audit_for_customer(self, customer_id: str) -> tuple[AuditRecord, ...]: ...

    def enqueue_case(self, record: CaseRecord) -> CaseRecord: ...

    def get_case(self, case_id: str) -> CaseRecord | None: ...

    def list_cases(self, *, status: str | None = None, queue: str | None = None) -> tuple[CaseRecord, ...]: ...

    def claim_case(self, case_id: str, agent_id: str) -> CaseRecord | None: ...

    def resolve_case(self, case_id: str, agent_id: str, note: str) -> CaseRecord | None: ...
