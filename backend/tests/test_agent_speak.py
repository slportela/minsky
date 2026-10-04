"""The model chooses an allowed next step and writes the customer text."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from minsky_api.agent.speak import compose_speech
from minsky_api.llm.client import LLMResult


class RecordingLLM:
    def __init__(self, act: str, text: str) -> None:
        self.act = act
        self.text = text

    async def respond(self, instructions: str, messages: list[dict[str, str]], **kwargs: Any) -> LLMResult[Any]:
        schema = kwargs["schema"]
        parsed = schema(act=self.act, text=self.text)
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


def test_compose_speech_rejects_a_block_sentence():
    with pytest.raises(RuntimeError, match="unverified"):
        asyncio.run(
            compose_speech(
                RecordingLLM("handoff", "Bloqueé la tarjeta. HO-abc"),  # type: ignore[arg-type]
                language="es",
                allowed=("handoff",),
                facts={"handoff_id": "HO-abc"},
            )
        )
    with pytest.raises(RuntimeError, match="unverified"):
        asyncio.run(
            compose_speech(
                RecordingLLM("inform", "Bloqueei o cartão."),  # type: ignore[arg-type]
                language="pt",
                allowed=("inform",),
                facts={},
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


def test_compose_speech_rejects_a_completed_open_even_when_the_id_is_present():
    with pytest.raises(RuntimeError, match="unverified"):
        asyncio.run(
            compose_speech(
                RecordingLLM("inform", "Quedó abierto el reclamo DSP-abc."),  # type: ignore[arg-type]
                language="es",
                allowed=("inform",),
                facts={"dispute_id": "DSP-abc"},
            )
        )


def test_compose_speech_rejects_paraphrased_refunds_and_blocks():
    samples = (
        ("es", "T1: el importe ya fue reembolsado."),
        ("pt", "T1: seu dinheiro já foi devolvido."),
        ("es", "T1: hemos bloqueado tu tarjeta."),
    )
    for language, text in samples:
        with pytest.raises(RuntimeError, match="unverified"):
            asyncio.run(
                compose_speech(
                    RecordingLLM("clarify", text),  # type: ignore[arg-type]
                    language=language,
                    allowed=("clarify",),
                    facts={"transaction_id": "T1"},
                )
            )


def test_compose_speech_rejects_a_confident_reply_in_the_other_language():
    with pytest.raises(RuntimeError, match="language"):
        asyncio.run(
            compose_speech(
                RecordingLLM("clarify", "T1: necesito el comercio y el monto."),  # type: ignore[arg-type]
                language="pt",
                allowed=("clarify",),
                facts={"transaction_id": "T1"},
            )
        )
    with pytest.raises(RuntimeError, match="language"):
        asyncio.run(
            compose_speech(
                RecordingLLM("clarify", "T1: preciso do comércio e do valor."),  # type: ignore[arg-type]
                language="es",
                allowed=("clarify",),
                facts={"transaction_id": "T1"},
            )
        )


def test_compose_speech_keeps_an_offer_to_block():
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
