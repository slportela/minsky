"""Jinja2 prompt loader for the agent."""

from datetime import datetime
from decimal import Decimal

from minsky_api.agent.prompts import render
from minsky_api.agent.replies import ask_clarify_many, ask_confirm_txn, policy_inform
from minsky_api.tools.schemas import TransactionView


def test_extract_prompt_loads_without_request_ids():
    text = render("agent.extract.j2")
    assert "out_of_scope" in text
    assert "transaction_id" in text


def test_confirm_txn_renders_facts():
    txn = TransactionView(
        transaction_id="T1",
        product_id="P1",
        transaction_date=datetime(2026, 6, 10, 9, 0),
        amount_usd=Decimal("25.00"),
        merchant_name="Cafe",
    )
    text = ask_confirm_txn(txn)
    assert "T1" in text and "Cafe" in text and "25.00" in text


def test_clarify_many_lists_candidates():
    txns = [
        TransactionView(transaction_id="T1", product_id="P1", amount_usd=Decimal("1"), merchant_name="A"),
        TransactionView(transaction_id="T2", product_id="P1", amount_usd=Decimal("2"), merchant_name="B"),
    ]
    text = ask_clarify_many(txns)
    assert "1. A" in text and "2. B" in text


def test_policy_inform_known_rule():
    assert "rechazado" in policy_inform(rule_id="D01-declined-not-charged").lower()
