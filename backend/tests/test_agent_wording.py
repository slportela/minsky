"""Code-owned customer wording: no rule ids, readable dates and amounts, complete fallbacks."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from minsky_api.agent.speak import action_claims
from minsky_api.agent.wording import (
    clarify_fallback,
    fallback_sentence,
    human_amount,
    human_date,
    policy_reason,
    safe_sentence,
    with_candidates,
    with_yes_no_hint,
)


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


_LIST = "1. Cafe, 25.00 USD, 10 de junio de 2026\n2. Cafe, 30.00 USD, 10 de junio de 2026"


def test_the_option_list_is_appended_when_the_model_did_not_name_the_options():
    text = "Veo dos cargos de Cafe: uno de 25.00 USD y otro de 30.00 USD. ¿Cuál no reconoces?"
    assert with_candidates(text, _LIST) == f"{text}\n{_LIST}"


def test_the_option_list_is_not_repeated_when_every_option_is_already_named_exactly():
    assert with_candidates(f"¿Cuál de estos?\n{_LIST}", _LIST) == f"¿Cuál de estos?\n{_LIST}"
    inline = "¿Cuál no reconoces? 1. Cafe, 25.00 USD, 10 de junio de 2026; 2. Cafe, 30.00 USD, 10 de junio de 2026."
    assert with_candidates(inline, _LIST) == inline


def test_one_missing_option_makes_code_append_the_whole_list():
    partial = "Veo este cargo: Cafe, 25.00 USD, 10 de junio de 2026. ¿Es ese?"
    assert with_candidates(partial, _LIST).endswith(_LIST)


def test_clarify_fallback_is_a_complete_question_in_both_languages():
    for language in ("es", "pt"):
        with_list = clarify_fallback(language, _LIST)
        assert with_list.endswith(_LIST) and "?" in with_list.split("\n")[0]
        no_list = clarify_fallback(language, None)
        assert "?" in no_list and "D0" not in no_list
    assert clarify_fallback("pt", None) != clarify_fallback("es", None)


_TXN: dict[str, object] = {"kind": "cargo", "merchant": "Cafe", "amount": "25.00 USD", "when": "10 de junio de 2026"}
_REASON: dict[str, object] = {"reason": "el cargo cumple las condiciones para abrir el reclamo ahora mismo"}


def test_every_act_that_can_be_refused_has_a_code_written_sentence_in_both_languages():
    facts = {**_TXN, **_REASON}
    for act in ("clarify", "ask_again", "abort", "confirm_open", "offer_block", "confirm_txn"):
        for language in ("es", "pt"):
            text = safe_sentence(act, language, facts)
            assert text.strip() and "D0" not in text, (act, language)
    assert safe_sentence("ask_again", "pt", {}) != safe_sentence("ask_again", "es", {})


def test_the_confirm_txn_sentence_names_the_transaction_and_asks_for_a_yes_or_no():
    es = safe_sentence("confirm_txn", "es", _TXN)
    assert "Cafe" in es and "25.00 USD" in es and "10 de junio de 2026" in es and es.endswith("Responde sí o no.")
    pt = safe_sentence("confirm_txn", "pt", _TXN)
    assert "Cafe" in pt and pt.endswith("Responda sim ou não.")
    assert "None" not in safe_sentence("confirm_txn", "es", {**_TXN, "when": None})


def test_the_question_acts_use_the_policy_reason_and_never_report_an_action():
    for act in ("confirm_open", "offer_block"):
        text = safe_sentence(act, "es", _REASON)
        assert text == "El cargo cumple las condiciones para abrir el reclamo ahora mismo."
        assert not action_claims(text)
    assert safe_sentence("abort", "es", {}) and not action_claims(safe_sentence("abort", "es", {}))


def test_an_act_with_no_sentence_is_a_bug_and_raises():
    import pytest

    with pytest.raises(ValueError, match="no code-written sentence"):
        safe_sentence("handoff", "es", {})


def test_the_transaction_question_always_tells_the_customer_how_to_answer():
    assert (
        with_yes_no_hint("¿Reconoces este cargo de Cafe?", "es") == "¿Reconoces este cargo de Cafe? Responde sí o no."
    )
    assert (
        with_yes_no_hint("Você reconhece esta cobrança?", "pt") == "Você reconhece esta cobrança? Responda sim ou não."
    )


def test_a_question_that_already_asks_for_a_yes_or_no_is_left_alone():
    for text in (
        "¿Es este el cargo? Responde sí o no.",
        "Você reconhece? Responda sim ou não.",
        "¿Es este? Dime si o no.",
    ):
        assert with_yes_no_hint(text, "es") == text
