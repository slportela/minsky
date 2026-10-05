"""Triage rules, one by one: queue, priority and due time."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from minsky_api.policy.triage import CaseKind, Priority, Queue, triage

NOW = datetime(2026, 6, 18, 9, 0, tzinfo=UTC)


def _t(**kwargs):
    base = {"kind": CaseKind.HANDOFF, "rule_id": None, "amount_usd": None, "card_blocked": False, "created_at": NOW}
    base.update(kwargs)
    return triage(**base)


def test_fraud_is_critical_in_the_fraud_queue_within_four_hours():
    decision = _t(rule_id="D06-possible-fraud", amount_usd=25.0)
    assert (decision.priority, decision.queue) == (Priority.CRITICAL, Queue.FRAUD)
    assert decision.due_at == NOW + timedelta(hours=4)


def test_a_blocked_card_is_critical_even_without_the_rule():
    assert _t(card_blocked=True).priority == Priority.CRITICAL


def test_amount_above_the_limit_is_high():
    decision = _t(rule_id="D07-above-auto-limit", amount_usd=5063.28)
    assert (decision.priority, decision.queue) == (Priority.HIGH, Queue.DISPUTES)
    assert decision.due_at == NOW + timedelta(days=2)


def test_repeat_complainer_and_other_policy_handoffs_are_medium():
    assert _t(rule_id="D08-repeat-complainer", amount_usd=40.0).priority == Priority.MEDIUM
    assert _t(rule_id="D05-outside-window", amount_usd=40.0).queue == Queue.DISPUTES


def test_handoffs_without_a_rule_go_to_the_general_queue():
    decision = _t(rule_id=None)
    assert (decision.priority, decision.queue) == (Priority.MEDIUM, Queue.GENERAL)


def test_automatic_dispute_is_low_with_ten_days():
    decision = _t(kind=CaseKind.DISPUTE, rule_id="D09-eligible", amount_usd=25.37)
    assert (decision.priority, decision.queue) == (Priority.LOW, Queue.DISPUTES)
    assert decision.due_at == NOW + timedelta(days=10)
