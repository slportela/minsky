"""Code-owned customer wording: no rule ids, readable dates and amounts, complete fallbacks."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from minsky_api.agent.speak import action_claims
from minsky_api.agent.wording import (
    clarify_fallback,
    confirm_txn_question,
    fallback_sentence,
    human_amount,
    human_date,
    policy_reason,
    safe_sentence,
    with_candidates,
    with_only_yes_no,
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


def _facts(kind: str = "cargo", **extra: object) -> dict[str, object]:
    return {"kind": kind, "merchant": "Clínica Médica", "amount": "234.61 USD", "when": "11 de junio de 2026", **extra}


def test_the_transaction_question_asks_which_charge_it_is_and_how_to_answer():
    assert confirm_txn_question("es", _facts()) == (
        "¿Es este el cargo al que te refieres: Clínica Médica, 234.61 USD, del 11 de junio de 2026? Responde sí o no."
    )
    assert confirm_txn_question("pt", _facts("cobrança", when="11 de junho de 2026")) == (
        "É esta a cobrança a que você se refere: Clínica Médica, 234.61 USD, em 11 de junho de 2026? "
        "Responda sim ou não."
    )


def test_the_transaction_question_never_asks_whether_the_customer_recognizes_it():
    """The old wording contradicted "no reconozco este cargo" and "el monto no es correcto"."""
    for language, kind in (("es", "cargo"), ("es", "transferencia"), ("pt", "cobrança"), ("pt", "saque")):
        text = confirm_txn_question(language, _facts(kind)).casefold()
        assert "reconoc" not in text and "reconhec" not in text
        assert "identific" not in text and "familiar" not in text


def test_the_transaction_question_agrees_in_gender_with_the_kind():
    assert confirm_txn_question("es", _facts("transferencia")).startswith(
        "¿Es esta la transferencia a la que te refieres"
    )
    assert confirm_txn_question("es", _facts("retiro")).startswith("¿Es este el retiro al que te refieres")
    assert confirm_txn_question("pt", _facts("transferência")).startswith("É esta a transferência a que você se refere")
    assert confirm_txn_question("pt", _facts("saque")).startswith("É este o saque a que você se refere")


def test_the_transaction_question_copes_with_missing_details_and_unknown_kind():
    only_amount = confirm_txn_question("es", {"kind": "pago", "merchant": None, "amount": "9.00 USD", "when": None})
    assert only_amount == "¿Es este el pago al que te refieres: 9.00 USD? Responde sí o no."
    bare = confirm_txn_question("es", {})
    assert bare == "¿Es este el cargo al que te refieres? Responde sí o no."  # no details, default noun


def test_the_code_written_fallback_for_the_transaction_question_is_the_same_question():
    facts = _facts()
    assert safe_sentence("confirm_txn", "es", facts) == confirm_txn_question("es", facts)
    assert safe_sentence("confirm_txn", "pt", facts) == confirm_txn_question("pt", facts)


_ONLY_ES = "En esta parte del proceso solo puedes responder «sí» o «no»."
_ONLY_PT = "Nesta parte do processo você só pode responder «sim» ou «não»."


def test_asking_again_always_says_that_only_yes_or_no_works_here():
    assert (
        with_only_yes_no("¿Es este el cargo de Cafe por 25.00 USD?", "es")
        == f"¿Es este el cargo de Cafe por 25.00 USD? {_ONLY_ES}"
    )
    assert with_only_yes_no("Esta é a cobrança do Cafe?", "pt") == f"Esta é a cobrança do Cafe? {_ONLY_PT}"


def test_a_closing_yes_or_no_instruction_is_replaced_not_repeated():
    for text in (
        "¿Es ese el cargo? Responde sí o no.",
        "¿Es ese el cargo? RESPONDE SI O NO",
        "¿Es ese el cargo?   responde sí o no!",
    ):
        assert with_only_yes_no(text, "es") == f"¿Es ese el cargo? {_ONLY_ES}"
    assert with_only_yes_no("Essa é a cobrança? Responda sim ou não.", "pt") == f"Essa é a cobrança? {_ONLY_PT}"


def test_the_notice_is_not_added_twice_and_a_middle_instruction_is_kept():
    once = with_only_yes_no("¿Es ese el cargo?", "es")
    assert with_only_yes_no(once, "es") == once
    assert once.count("solo puedes responder") == 1
    middle = "Responde sí o no cuando estés lista. ¿Es ese el cargo?"
    assert with_only_yes_no(middle, "es").startswith(middle)  # only a closing instruction is replaced


def test_an_empty_reply_becomes_just_the_notice():
    assert with_only_yes_no("", "es") == _ONLY_ES
    assert with_only_yes_no("   ", "pt") == _ONLY_PT


def test_the_ask_again_fallback_repeats_the_pending_question():
    pending = "¿Reconoces este cargo de Cafe por 25.00 USD? Responde sí o no."
    assert safe_sentence("ask_again", "es", {"pending_question": pending}) == f"No me quedó claro. {pending}"
    assert (
        safe_sentence("ask_again", "pt", {"pending_question": "Esta é a cobrança?"})
        == "Não ficou claro. Esta é a cobrança?"
    )
    assert safe_sentence("ask_again", "es", {}) == "No me quedó claro."
