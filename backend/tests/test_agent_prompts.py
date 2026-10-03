"""Jinja2 prompt loader for the agent."""

from datetime import datetime
from decimal import Decimal

import pytest

from minsky_api.agent.prompts import render
from minsky_api.agent.replies import (
    PHRASES,
    ask_clarify_many,
    ask_confirm_txn,
    handoff_done,
    phrases_for,
    policy_inform,
)
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


def test_handoff_injects_language_phrase_and_keeps_ids():
    spanish = handoff_done(handoff_id="HO-abc", rule_id="D07-above-auto-limit", language="es")
    portuguese = handoff_done(handoff_id="HO-abc", rule_id="D07-above-auto-limit", language="pt")
    assert "Te derivo con un asesor" in spanish
    assert "Te transfiro para um assessor" in portuguese
    assert "HO-abc" in spanish and "HO-abc" in portuguese
    assert "D07-above-auto-limit" in spanish and "D07-above-auto-limit" in portuguese


def test_third_language_renders_through_the_same_template(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setitem(PHRASES, "fr", {**PHRASES["es"], "handoff_intro": "Je vous transfère"})
    text = handoff_done(handoff_id="HO-1", language="fr")
    assert "Je vous transfère" in text and "HO-1" in text


def test_missing_phrase_key_raises(monkeypatch: pytest.MonkeyPatch):
    incomplete = {key: value for key, value in PHRASES["pt"].items() if key != "handoff_intro"}
    monkeypatch.setitem(PHRASES, "pt", incomplete)
    with pytest.raises(KeyError):
        phrases_for("pt")


def test_portuguese_catalog_uses_reclamacao_not_reclamo():
    assert all("reclamo" not in value.casefold() for value in PHRASES["pt"].values())
