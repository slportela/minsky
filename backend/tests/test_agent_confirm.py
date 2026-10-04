"""The confirmation classifier: a bare "no", a cut-off reply, and the token budget."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from minsky_api.agent.confirm import Confirmation, classify_confirmation, is_bare_no


@pytest.mark.parametrize("text", ["no", "No", "NO", "no.", "No!", "  no  ", "¡no!", "não", "Não.", "nao", "NAO"])
def test_a_plain_no_is_bare(text: str):
    assert is_bare_no(text)


@pytest.mark.parametrize(
    "text",
    ["", "sí", "no sé", "no, gracias", "no quiero", "mejor no", "sí, pero no", "¿no?", "non", "nope", "no no", "n o"],
)
def test_anything_more_than_a_plain_no_is_not_bare(text: str):
    assert not is_bare_no(text)


class _CutOff:
    """The provider's reply stopped mid-value, as seen live: responses.parse then raises a ValidationError."""

    async def respond(self, *args: Any, **kwargs: Any) -> Any:
        Confirmation.model_validate_json('{"decision":"')
        raise AssertionError("unreachable")


class _ApiDown:
    async def respond(self, *args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("provider unavailable")


class _Recording:
    def __init__(self) -> None:
        self.kwargs: dict[str, Any] = {}

    async def respond(self, *args: Any, **kwargs: Any) -> Any:
        self.kwargs = kwargs
        raise RuntimeError("stop after recording")


def test_a_cut_off_reply_is_unclear_not_an_error():
    result = asyncio.run(classify_confirmation(_CutOff(), question="¿Bloqueo?", text="sí"))  # type: ignore[arg-type]
    assert result.decision == "unclear"


def test_an_api_error_still_propagates():
    """Only a cut-off reply is turned into "unclear". A provider failure must stay visible."""
    with pytest.raises(RuntimeError, match="provider unavailable"):
        asyncio.run(classify_confirmation(_ApiDown(), question="¿Bloqueo?", text="sí"))  # type: ignore[arg-type]


def test_the_classifier_has_room_to_reason_before_it_answers():
    llm = _Recording()
    with pytest.raises(RuntimeError):
        asyncio.run(classify_confirmation(llm, question="¿Bloqueo?", text="sí"))  # type: ignore[arg-type]
    assert llm.kwargs["max_output_tokens"] >= 256  # 64 cut the JSON off in 2 of 180 live calls
