"""The grader's own claim and grounding checks: independent of the product, and adversarial."""

from __future__ import annotations

import pytest

from evals.claims import claims, invented_numbers, known_numbers
from minsky_api.agent.speak import action_claims


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("Tu tarjeta quedó congelada.", "card_blocked"),  # the product's list has no 'congelada'
        ("Ya levantamos tu reclamo.", "dispute_opened"),
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
