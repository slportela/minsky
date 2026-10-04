"""The model chooses an allowed next step and writes the customer text."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from minsky_api.agent.speak import action_claims, compose_speech, ungrounded_number
from minsky_api.llm.client import LLMResult


class RecordingLLM:
    def __init__(self, act: str, text: str, *, claims_card_blocked: bool = False) -> None:
        self.act = act
        self.text = text
        self.claims_card_blocked = claims_card_blocked

    async def respond(self, instructions: str, messages: list[dict[str, str]], **kwargs: Any) -> LLMResult[Any]:
        schema = kwargs["schema"]
        parsed = schema(act=self.act, text=self.text, claims_card_blocked=self.claims_card_blocked)
        return LLMResult(
            text=parsed.model_dump_json(),
            parsed=parsed,
            model="gpt-6-luna",
            input_tokens=1,
            output_tokens=1,
            latency_ms=1.0,
        )


def test_compose_speech_keeps_an_allowed_act_and_the_model_text():
    speech = asyncio.run(
        compose_speech(
            RecordingLLM("clarify", "¿Me das el comercio y el monto del cargo T1?"),  # type: ignore[arg-type]
            language="es",
            allowed=("clarify", "handoff"),
            facts={"transaction_id": "T1"},
        )
    )
    assert speech.act == "clarify"
    assert "T1" in speech.text


def test_compose_speech_rejects_an_act_outside_the_allowed_list():
    with pytest.raises(RuntimeError, match="not allowed"):
        asyncio.run(
            compose_speech(
                RecordingLLM("handoff", "Te derivo."),  # type: ignore[arg-type]
                language="es",
                allowed=("clarify",),
                facts={},
            )
        )


def test_compose_speech_rejects_an_unverified_card_block_claim():
    with pytest.raises(RuntimeError, match="unverified"):
        asyncio.run(
            compose_speech(
                RecordingLLM("handoff", "Bloqueé la tarjeta. HO-abc", claims_card_blocked=True),  # type: ignore[arg-type]
                language="es",
                allowed=("handoff",),
                facts={"handoff_id": "HO-abc", "card_blocked": False},
            )
        )


def test_compose_speech_rejects_a_block_sentence_when_the_flag_is_false():
    with pytest.raises(RuntimeError, match="unverified"):
        asyncio.run(
            compose_speech(
                RecordingLLM("inform", "Bloqueei o cartão.", claims_card_blocked=False),  # type: ignore[arg-type]
                language="pt",
                allowed=("inform",),
                facts={"card_blocked": False},
            )
        )


def test_compose_speech_rejects_an_open_or_refund_or_handoff_sentence_without_facts():
    for text in ("Ya abrí el reclamo.", "Reembolsé el monto.", "Te derivé con un especialista."):
        with pytest.raises(RuntimeError, match="unverified"):
            asyncio.run(
                compose_speech(
                    RecordingLLM("inform", text),  # type: ignore[arg-type]
                    language="es",
                    allowed=("inform",),
                    facts={},
                )
            )


def test_compose_speech_allows_a_verified_open_and_an_offer_to_block():
    opened = asyncio.run(
        compose_speech(
            RecordingLLM("inform", "Quedó abierto el reclamo DSP-abc."),  # type: ignore[arg-type]
            language="es",
            allowed=("inform",),
            facts={"dispute_id": "DSP-abc"},
        )
    )
    assert opened.text.startswith("Quedó abierto")
    offer = asyncio.run(
        compose_speech(
            RecordingLLM("offer_block", "Alguien podría estar usando tu tarjeta."),  # type: ignore[arg-type]
            language="es",
            allowed=("offer_block",),
            facts={"reason": "x"},
        )
    )
    assert offer.act == "offer_block"


def test_compose_speech_rejects_an_internal_rule_id():
    with pytest.raises(RuntimeError, match="rule id"):
        asyncio.run(
            compose_speech(
                RecordingLLM("offer_block", "Esto parece fraude (D06-possible-fraud)."),  # type: ignore[arg-type]
                language="es",
                allowed=("offer_block",),
                facts={"reason": "x"},
            )
        )


def test_compose_speech_rejects_a_confirm_or_clarify_that_drops_facts():
    with pytest.raises(RuntimeError, match="drops"):
        asyncio.run(
            compose_speech(
                RecordingLLM("confirm_txn", "¿Es este cargo?"),  # type: ignore[arg-type]
                language="es",
                allowed=("confirm_txn",),
                facts={"transaction_id": "T1", "merchant": "Cafe", "amount_usd": "25.00", "when": "2026-06-10"},
            )
        )
    with pytest.raises(RuntimeError, match="drops"):
        asyncio.run(
            compose_speech(
                RecordingLLM("clarify", "¿Cuál de estos?"),  # type: ignore[arg-type]
                language="es",
                allowed=("clarify",),
                facts={"candidates": "1. Cafe T1 | 2. Cafe Sur T2"},
            )
        )


@pytest.mark.parametrize(
    ("text", "claim"),
    [
        ("Tu tarjeta fue bloqueada", "card_blocked"),
        ("Hemos bloqueado tu tarjeta", "card_blocked"),
        ("Seu cartão foi bloqueado", "card_blocked"),
        ("Se abrió la disputa", "dispute_opened"),
        ("Ya registré tu reclamo", "dispute_opened"),
        ("Sua contestação foi registrada", "dispute_opened"),
        ("Te devolvimos el dinero", "refund"),
        ("Pasé tu caso a un especialista", "handoff"),
    ],
)
def test_completed_action_phrasings_are_claims(text: str, claim: str):
    assert claim in action_claims(text)


@pytest.mark.parametrize(
    "text",
    [
        "¿Quieres que bloquee tu tarjeta ahora?",
        "Puedo bloquear tu tarjeta si me confirmas.",
        "Si confirmas, tu tarjeta quedará bloqueada.",
        "Seu cartão ficará bloqueado se você confirmar.",
        "Puedo abrir un reclamo por este cargo.",
        "No bloqueé la tarjeta.",
    ],
)
def test_offers_and_negations_are_not_claims(text: str):
    assert action_claims(text) == frozenset()


def test_an_open_dispute_is_supported_by_the_existing_reference():
    text = "Ya hay un reclamo abierto para ese cargo con la referencia DSP-abc123."
    speech = asyncio.run(
        compose_speech(
            RecordingLLM("inform", text),  # type: ignore[arg-type]
            language="es",
            allowed=("inform",),
            facts={"existing_dispute_id": "DSP-abc123"},
        )
    )
    assert speech.text == text
    with pytest.raises(RuntimeError, match="unverified dispute_opened"):
        asyncio.run(
            compose_speech(
                RecordingLLM("inform", "Tu reclamo quedó abierto."),  # type: ignore[arg-type]
                language="es",
                allowed=("inform",),
                facts={"reason": "x"},
            )
        )


@pytest.mark.parametrize(
    ("text", "claim"),
    [
        ("Tu tarjeta ya no podrá usarse.", "card_blocked"),
        ("Tu tarjeta quedó desactivada.", "card_blocked"),
        ("Seu cartão foi cancelado.", "card_blocked"),
        ("Tu reclamo está en trámite.", "dispute_opened"),
        ("Tu número de reclamo es el siguiente.", "dispute_opened"),
        ("Te devolveremos el dinero.", "refund"),
        ("Vas a recibir tu dinero pronto.", "refund"),
        ("Você vai receber o seu dinheiro de volta.", "refund"),
        ("Te paso con un asesor.", "handoff"),
        ("Un especialista tomará tu caso.", "handoff"),
    ],
)
def test_adversarial_paraphrases_are_claims(text: str, claim: str):
    assert claim in action_claims(text)


def test_team_follow_up_after_an_open_dispute_is_not_a_handoff_claim():
    assert action_claims("Listo, tu reclamo DSP-1 quedó abierto. Nuestro equipo lo revisará.") == {"dispute_opened"}


def test_numbers_must_come_from_the_facts():
    facts = {"merchant": "Cafe", "amount": "25.00 USD", "when": "10 de junio de 2026", "reason": "más de 120 días"}
    ok = "Encontré este cargo: Cafe, 25.00 USD, 10 de junio de 2026. Tiene más de 120 días. Ref. HO-9c1a47831750."
    assert ungrounded_number(ok, facts) is None
    assert ungrounded_number("Cafe, 25 USD.", facts) is None  # 25.00 and 25 are the same amount
    assert ungrounded_number("Se resolverá en 15 días.", facts) == "15"
    assert ungrounded_number("Te devolvemos 30.00 USD.", facts) == "30.00"
    with pytest.raises(RuntimeError, match="ungrounded number 15"):
        asyncio.run(
            compose_speech(
                RecordingLLM("inform", "Tu caso se resolverá en 15 días."),  # type: ignore[arg-type]
                language="es",
                allowed=("inform",),
                facts={"reason": "x"},
            )
        )
