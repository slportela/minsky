"""Code-owned customer wording: no rule ids, readable dates and amounts, complete fallbacks."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from minsky_api.agent.speak import action_claims
from minsky_api.agent.wording import fallback_sentence, human_amount, human_date, policy_reason


def test_dates_and_amounts_read_naturally():
    assert human_date(date(2026, 6, 10), "es") == "10 de junio de 2026"
    assert human_date(date(2026, 3, 1), "pt") == "1 de março de 2026"
    assert human_amount(Decimal("25.3700000000")) == "25.37 USD"


def test_every_rule_has_a_plain_reason_in_both_languages():
    for rule in ("D01", "D02", "D03", "D04", "D05", "D06", "D07", "D08", "D09"):
        for language in ("es", "pt"):
            reason = policy_reason(f"{rule}-anything", language)
            assert reason and rule not in reason


def test_fraud_reason_never_blames_the_customer():
    assert "alguien" in (policy_reason("D06-possible-fraud", "es") or "")


def test_fallback_is_a_sentence_with_the_reference_and_supported_claims_only():
    text = fallback_sentence("es", {"handoff_id": "HO-1", "card_blocked": True, "reason": "x"})
    assert "HO-1" in text and "bloqueada" in text and "D06" not in text
    assert action_claims(text) <= {"card_blocked", "handoff"}
    pt = fallback_sentence("pt", {"dispute_id": "DSP-9"})
    assert "DSP-9" in pt and pt.endswith(".")
