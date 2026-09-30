"""Unit tests for InMemoryCasesBackend (disputes, handoffs, blocks, audit)."""

from __future__ import annotations

from minsky_api.store.cases_memory import InMemoryCasesBackend


def test_dispute_idempotent_on_customer_and_transaction():
    cases = InMemoryCasesBackend()
    first = cases.create_dispute(customer_id="C1", transaction_id="T1", reason="unrecognized")
    second = cases.create_dispute(customer_id="C1", transaction_id="T1", reason="duplicate")
    assert first.dispute_id == second.dispute_id
    assert first.reason == "unrecognized"
    assert cases.get_dispute(first.dispute_id) == first
    assert cases.get_dispute_by_transaction(customer_id="C1", transaction_id="T1") == first


def test_handoff_create_and_read_back():
    cases = InMemoryCasesBackend()
    created = cases.create_handoff(
        customer_id="C1",
        reason="fraud",
        rule_id="D06-possible-fraud",
        facts={"transaction_id": "T1"},
        actions=("offer_block",),
    )
    assert cases.get_handoff(created.handoff_id) == created


def test_card_block_overlay_and_audit():
    cases = InMemoryCasesBackend()
    block = cases.block_card(customer_id="C1", product_id="P1")
    assert cases.get_card_block("P1") == block
    assert block.status == "Blocked"
    again = cases.block_card(customer_id="C1", product_id="P1")
    assert again.product_id == block.product_id
    cases.append_audit(
        tool="block_card",
        session_id="s1",
        customer_id="C1",
        args_digest="abc",
        outcome="ok",
    )
    assert len(cases.list_audit()) == 1
