"""Jinja2 prompt loader for the agent."""

import re
from datetime import datetime
from decimal import Decimal

import pytest

from minsky_api.agent.prompts import render
from minsky_api.agent.replies import already_done, ask_clarify_many, ask_confirm_txn, policy_inform, txn_label
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


def test_confirm_txn_transfer_no_merchant():
    txn = TransactionView(
        transaction_id="TRX-123",
        product_id="P1",
        transaction_date=datetime(2025, 3, 19, 10, 0),
        transaction_type="Transfer",
        amount_usd=Decimal("243.88"),
        merchant_name=None,
    )
    text = ask_confirm_txn(txn)
    assert "transferencia" in text
    assert "TRX-123" in text
    assert "243.88" in text
    assert "comercio desconocido" not in text
    assert "cargo" not in text.lower()


def test_confirm_txn_withdrawal_no_merchant():
    txn = TransactionView(
        transaction_id="TRX-456",
        product_id="P1",
        transaction_date=datetime(2025, 5, 1, 10, 0),
        transaction_type="Withdrawal",
        amount_usd=Decimal("100.00"),
        merchant_name=None,
    )
    text = ask_confirm_txn(txn)
    assert "retiro" in text
    assert "comercio desconocido" not in text


def test_confirm_txn_payment_no_merchant():
    txn = TransactionView(
        transaction_id="TRX-789",
        product_id="P1",
        transaction_type="Payment",
        amount_usd=Decimal("50.00"),
        merchant_name=None,
    )
    text = ask_confirm_txn(txn)
    assert "pago" in text
    assert "comercio desconocido" not in text


def test_confirm_txn_purchase_with_merchant():
    txn = TransactionView(
        transaction_id="TRX-SHOP",
        product_id="P1",
        transaction_type="Purchase",
        amount_usd=Decimal("75.00"),
        merchant_name="WALMART",
    )
    text = ask_confirm_txn(txn)
    assert "compra" in text
    assert "WALMART" in text


def test_confirm_txn_null_type_null_merchant():
    txn = TransactionView(
        transaction_id="TRX-NULL",
        product_id="P1",
        transaction_type=None,
        amount_usd=Decimal("30.00"),
        merchant_name=None,
    )
    text = ask_confirm_txn(txn)
    assert "movimiento" in text
    assert "comercio desconocido" not in text


def test_confirm_txn_unknown_type_no_merchant():
    txn = TransactionView(
        transaction_id="TRX-UNK",
        product_id="P1",
        transaction_type="SomeOtherType",
        amount_usd=Decimal("20.00"),
        merchant_name=None,
    )
    text = ask_confirm_txn(txn)
    assert "movimiento" in text
    assert "comercio desconocido" not in text


def test_confirm_txn_keeps_confirmation_phrasing():
    txn = TransactionView(
        transaction_id="TRX-CONFIRM",
        product_id="P1",
        transaction_type="Transfer",
        amount_usd=Decimal("100.00"),
        merchant_name=None,
    )
    text = ask_confirm_txn(txn)
    assert "sí para confirmar" in text
    assert "no para cancelar" in text


@pytest.mark.parametrize(
    "transaction_type,expected_noun",
    [
        ("Transfer", "esta transferencia"),
        ("Withdrawal", "este retiro"),
        ("Payment", "este pago"),
        ("Purchase", "esta compra"),
        ("Deposit", "este depósito"),
        ("Adjustment", "este ajuste"),
        ("Unknown", "este movimiento"),
        (None, "este movimiento"),
    ],
)
def test_txn_label_mapping(transaction_type: str | None, expected_noun: str) -> None:
    assert txn_label(transaction_type) == expected_noun


def test_clarify_many_lists_candidates():
    txns = [
        TransactionView(transaction_id="T1", product_id="P1", amount_usd=Decimal("1"), merchant_name="A"),
        TransactionView(transaction_id="T2", product_id="P1", amount_usd=Decimal("2"), merchant_name="B"),
    ]
    text = ask_clarify_many(txns)
    assert "1. A" in text and "2. B" in text


def test_policy_inform_known_rule():
    assert "rechazado" in policy_inform(rule_id="D01-declined-not-charged").lower()


def test_already_done_d03_abstain_no_invented_date():
    text = already_done(rule_id="D03-pending-not-posted", route="abstain")
    assert "pendiente" in text
    # Must not claim an action happened
    assert "abrí" not in text
    assert "registré" not in text
    # Must not invent a posting date
    assert not re.search(r"\d{4}-\d{2}-\d{2}", text), "must not contain an invented date"
    # Way forward the system can honour: check again later, or start a new conversation.
    assert "más tarde" in text
    # Phase DONE answers every message with this reply, so it must not invite a keyword or a human
    # handoff that nothing would act on.
    assert "asesor" not in text.lower()
    assert "escribir" not in text.lower()


def test_already_done_other_flow_short_and_no_action_claim():
    text = already_done(rule_id="D01-declined-not-charged", route="inform")
    assert "terminó" in text
    # Must not claim an action happened
    assert "abrí" not in text


def test_already_done_no_args_gives_generic_reply():
    text = already_done()
    assert "terminó" in text
