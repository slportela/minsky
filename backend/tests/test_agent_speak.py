"""The model chooses an allowed next step and writes the customer text."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from minsky_api.agent.speak import action_claims, compose_speech
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
            RecordingLLM("offer_block", "¿Quieres que bloquee la tarjeta del cargo T1? Regla D06."),  # type: ignore[arg-type]
            language="es",
            allowed=("offer_block",),
            facts={"transaction_id": "T1", "rule_id": "D06"},
        )
    )
    assert offer.act == "offer_block"


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
