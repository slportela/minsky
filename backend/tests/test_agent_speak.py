"""The model chooses an allowed next step and writes the customer text."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from minsky_api.agent.speak import compose_speech
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
            RecordingLLM("clarify", "¿Me das el comercio y el monto?"),  # type: ignore[arg-type]
            language="es",
            allowed=("clarify", "handoff"),
            facts={"transaction_id": "T1"},
        )
    )
    assert speech.act == "clarify"
    assert speech.text == "¿Me das el comercio y el monto?"


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
                RecordingLLM("handoff", "Bloqueé la tarjeta.", claims_card_blocked=True),  # type: ignore[arg-type]
                language="es",
                allowed=("handoff",),
                facts={"card_blocked": False},
            )
        )
