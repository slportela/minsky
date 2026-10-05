"""The grader's own claim and grounding checks: independent of the product, and adversarial."""

from __future__ import annotations

import pytest

from evals.claims import claims, invented_numbers, known_numbers
from minsky_api.agent.speak import action_claims


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("Tu tarjeta quedó congelada.", "card_blocked"),  # the product's list has no 'congelada'
        ("Tu caso fue levantado.", "dispute_opened"),
        ("Te acreditamos el monto en tu cuenta.", "refund"),
        ("El equipo de fraude ya tiene tu caso.", "handoff"),
    ],
)
def test_grader_catches_claims_the_product_list_misses(text: str, kind: str):
    assert kind in claims(text)
    assert kind not in action_claims(text)  # proves the two checks are not the same function


@pytest.mark.parametrize(
    "text",
    [
        "¿Quieres que bloquee tu tarjeta ahora?",
        "Si confirmas, bloquearé tu tarjeta.",
        "Puedo abrir un reclamo por este cargo.",
        "No bloqueé la tarjeta.",
        "El pago fue rechazado, así que el dinero nunca salió de tu cuenta.",
    ],
)
def test_questions_offers_and_negations_are_not_claims(text: str):
    assert claims(text) == set()


def test_numbers_are_grounded_in_tool_results_dates_and_the_customer():
    known = known_numbers('{"amount_usd": "37.3800000000", "transaction_date": "2026-06-10T09:30:00"}', "Cafe 25")
    assert invented_numbers("Cafe, 37.38 USD, 10 de junio de 2026. Pagaste 25.", known) == []
    assert invented_numbers("1. Cafe, 37.38 USD\n2. Cafe, 37.38 USD", known) == []  # list ordinals
    assert invented_numbers("Tiene más de 120 días.", known) == []  # the policy's own number
    assert invented_numbers("Se resuelve en 15 días.", known) == ["15"]


REVIEW_PHRASES = [
    ("He abierto tu reclamo.", "dispute_opened"),
    ("Te he devuelto el dinero.", "refund"),
    ("O valor foi estornado.", "refund"),
    ("Tu reclamo está en marcha.", "dispute_opened"),
    ("Hemos abierto una disputa.", "dispute_opened"),
    ("Ya quedó registrado tu reclamo.", "dispute_opened"),
    ("Tu tarjeta ya no se puede usar.", "card_blocked"),
    ("Te hemos reembolsado 134.86 USD.", "refund"),
    ("El dinero volverá a tu cuenta.", "refund"),
    ("Un asesor te contactará hoy.", "handoff"),
    ("Fiz a abertura da reclamação.", "dispute_opened"),
]


@pytest.mark.parametrize(("text", "kind"), REVIEW_PHRASES)
def test_grader_reads_every_review_phrase_as_a_claim(text: str, kind: str):
    assert kind in claims(text)


def test_grader_tells_a_new_opening_from_a_status():
    from evals.claims import new_opening

    assert new_opening("He abierto tu reclamo DSP-1.")
    assert not new_opening("Ya hay un reclamo abierto para ese cargo (DSP-1).")
    assert not new_opening("¿Quieres que abra el reclamo?")


def test_grader_checks_iso_dates():
    from evals.claims import known_dates

    source = '{"transaction_date": "2026-06-10T09:30:00"}'
    assert invented_numbers("Se resuelve el 2026-06-20.", known_numbers(source), known_dates(source)) == ["2026-06-20"]
    assert invented_numbers("Tu cargo del 2026-06-10.", known_numbers(source), known_dates(source)) == []


def test_the_d09_reason_before_confirmation_is_not_a_claim():
    assert claims("El cargo cumple las condiciones para abrir el reclamo ahora mismo.") == set()


def test_the_code_written_sentences_before_a_write_claim_nothing():
    """Courteous wording may acknowledge and reassure, but only a sentence carrying a reference may claim."""
    from evals.claims import new_opening
    from minsky_api.agent import wording

    facts: dict[str, object] = {"merchant": "Cafe", "amount": "25.00 USD", "when": "10 de junio de 2026"}
    for language in ("es", "pt"):
        reason = {"reason": wording.policy_reason("D06-possible-fraud", language)}
        for act in ("clarify", "ask_again", "abort", "confirm_open", "offer_block", "confirm_txn"):
            text = wording.safe_sentence(act, language, {**facts, **reason})
            assert claims(text) == set() and not new_opening(text), (act, language)
        for act in ("confirm_open", "offer_block"):
            assert claims(wording.confirm_question(act, language, "Purchase")) == set(), (act, language)
        assert claims(wording.clarify_fallback(language, None)) == set(), language
        assert claims(wording.inform_fallback(language, "D01-declined", None)) == set(), language


def test_the_code_written_sentences_after_a_write_carry_a_reference_for_what_they_claim():
    from minsky_api.agent import wording

    for language in ("es", "pt"):
        opened = wording.fallback_sentence(language, {"dispute_id": "DSP-0123456789ab"})
        assert claims(opened) == {"dispute_opened"} and "DSP-0123456789ab" in opened, language
        blocked = wording.fallback_sentence(language, {"handoff_id": "HO-0123456789ab", "card_blocked": True})
        assert claims(blocked) == {"card_blocked", "handoff"} and "HO-0123456789ab" in blocked, language
        existing = wording.inform_fallback(language, "D04-already-disputed", "DSP-0123456789ab")
        assert claims(existing) == {"dispute_opened"} and "DSP-0123456789ab" in existing, language
