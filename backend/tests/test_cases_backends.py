"""The same cases.* contract for both backends: in-memory and SQL (SQLite here, Postgres in compose)."""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest

from minsky_api.store.cases import CasesBackend
from minsky_api.store.cases_memory import CaseRecord, CaseTransitionError, InMemoryCasesBackend
from minsky_api.store.cases_sql import SqlCasesBackend

# Set MINSKY_TEST_POSTGRES_URL (a throwaway database) to run the same contract against real Postgres.
_POSTGRES_URL = os.environ.get("MINSKY_TEST_POSTGRES_URL")


@pytest.fixture(params=["memory", "sql", *(["postgres"] if _POSTGRES_URL else [])])
def cases(request: pytest.FixtureRequest) -> Iterator[CasesBackend]:
    if request.param == "memory":
        yield InMemoryCasesBackend()
        return
    url = _POSTGRES_URL if request.param == "postgres" else "sqlite+pysqlite:///:memory:"
    assert url is not None
    backend = SqlCasesBackend.from_url(url)
    if request.param == "postgres":
        with backend._engine.begin() as conn:  # pyright: ignore[reportPrivateUsage]
            conn.exec_driver_sql("DROP SCHEMA IF EXISTS cases CASCADE")
    backend.ensure_schema()
    yield backend
    backend.dispose()


def _case(case_id: str, *, priority: str, due_in_hours: float, status: str = "new") -> CaseRecord:
    now = datetime(2026, 6, 18, 9, 0, tzinfo=UTC)
    return CaseRecord(
        case_id=case_id,
        kind="handoff",
        customer_id="C1",
        rule_id=None,
        reason="r",
        priority=priority,
        queue="disputes",
        triage_reason="t",
        due_at=now + timedelta(hours=due_in_hours),
        status=status,
        summary="s",
        facts={"merchant": "Cafe"},
        actions=("card_blocked:P1",),
        open_questions=("q?",),
        expected_resolution_days=15.0,
        created_at=now,
        updated_at=now,
    )


def test_dispute_is_idempotent_per_customer_and_transaction(cases: CasesBackend):
    first = cases.create_dispute(customer_id="C1", transaction_id="T1", reason="unrecognized_charge")
    again = cases.create_dispute(customer_id="C1", transaction_id="T1", reason="other")
    assert again.dispute_id == first.dispute_id and again.reason == "unrecognized_charge"
    assert cases.get_dispute(first.dispute_id) == first
    assert cases.get_dispute_by_transaction(customer_id="C1", transaction_id="T1") == first
    assert cases.get_dispute_by_transaction(customer_id="C2", transaction_id="T1") is None


def test_handoff_is_idempotent_per_key_and_keeps_facts(cases: CasesBackend):
    first = cases.create_handoff(
        customer_id="C1", reason="fraud", rule_id="D06", facts={"a": 1}, actions=("x",), idempotency_key="k"
    )
    again = cases.create_handoff(
        customer_id="C1", reason="fraud", rule_id="D06", facts={"a": 2}, actions=(), idempotency_key="k"
    )
    assert again == first
    assert cases.get_handoff(first.handoff_id) == first
    other = cases.create_handoff(customer_id="C1", reason="r", rule_id=None, facts={}, actions=())
    assert other.handoff_id != first.handoff_id


def test_card_block_and_audit(cases: CasesBackend):
    block = cases.block_card(customer_id="C1", product_id="P1")
    assert cases.get_card_block("P1") == block and block.status == "Blocked"
    cases.append_audit(tool="block_card", session_id="s", customer_id="C1", args_digest="d", outcome="ok")
    cases.append_audit(tool="get_transactions", session_id="s", customer_id="C2", args_digest="d", outcome="ok")
    assert len(cases.list_audit()) == 2
    assert [a.tool for a in cases.list_audit_for_customer("C1")] == ["block_card"]


def test_queue_orders_open_work_by_priority_then_due_time(cases: CasesBackend):
    cases.enqueue_case(_case("LOW", priority="Low", due_in_hours=1))
    cases.enqueue_case(_case("HIGH-LATE", priority="High", due_in_hours=48))
    cases.enqueue_case(_case("HIGH-SOON", priority="High", due_in_hours=2))
    cases.enqueue_case(_case("CRIT", priority="Critical", due_in_hours=4))
    cases.enqueue_case(_case("DONE", priority="Critical", due_in_hours=0, status="resolved"))
    assert [c.case_id for c in cases.list_cases()] == ["CRIT", "HIGH-SOON", "HIGH-LATE", "LOW", "DONE"]
    assert [c.case_id for c in cases.list_cases(status="new")] == ["CRIT", "HIGH-SOON", "HIGH-LATE", "LOW"]


def test_enqueue_is_idempotent(cases: CasesBackend):
    first = cases.enqueue_case(_case("A", priority="Low", due_in_hours=1))
    again = cases.enqueue_case(_case("A", priority="Critical", due_in_hours=1))
    assert again.priority == first.priority == "Low"
    assert cases.get_case("A") == first


def test_claim_then_resolve_and_the_denials(cases: CasesBackend):
    cases.enqueue_case(_case("A", priority="High", due_in_hours=1))
    with pytest.raises(CaseTransitionError, match="claim_the_case_first"):
        cases.resolve_case("A", "agent-1", "done")
    claimed = cases.claim_case("A", "agent-1")
    assert claimed is not None and claimed.status == "in_progress" and claimed.assigned_to == "agent-1"
    with pytest.raises(CaseTransitionError, match="another_agent"):
        cases.claim_case("A", "agent-2")
    with pytest.raises(CaseTransitionError, match="claim_the_case_first"):
        cases.resolve_case("A", "agent-2", "done")
    done = cases.resolve_case("A", "agent-1", "Merchant confirmed the overcharge")
    assert done is not None and done.status == "resolved" and done.resolution_note
    with pytest.raises(CaseTransitionError, match="case_resolved"):
        cases.claim_case("A", "agent-1")
    assert cases.claim_case("MISSING", "agent-1") is None
