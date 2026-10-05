"""The confirmation classifier: a cut-off reply and the token budget. Consent itself is agent.consent."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx2
import pytest
from openai import AsyncOpenAI
from pydantic import SecretStr

from minsky_api.agent.confirm import classify_confirmation
from minsky_api.config import Settings
from minsky_api.llm.client import LLM


class _CutOff:
    """LLM.respond wraps a cut-off schema parse as ModelOutputError; confirm must stay unclear."""

    async def respond(self, *args: Any, **kwargs: Any) -> Any:
        from minsky_api.llm.client import ModelOutputError

        raise ModelOutputError("the model reply does not fit the requested schema")


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


def test_a_cut_off_through_llm_respond_is_unclear_not_an_outage():
    """LLM.respond wraps schema ValidationError as ModelOutputError; confirm must stay unclear."""

    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            200,
            json={
                "id": "resp_1",
                "object": "response",
                "created_at": 0,
                "status": "completed",
                "model": "gpt-6-luna",
                "output": [
                    {
                        "type": "message",
                        "id": "msg_1",
                        "status": "completed",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": '{"decision":"', "annotations": []}],
                    }
                ],
                "parallel_tool_calls": False,
                "tool_choice": "auto",
                "tools": [],
                "usage": {
                    "input_tokens": 1,
                    "output_tokens": 1,
                    "total_tokens": 2,
                    "input_tokens_details": {"cached_tokens": 0},
                    "output_tokens_details": {"reasoning_tokens": 0},
                },
            },
        )

    client = AsyncOpenAI(
        api_key="test-key",
        base_url="http://provider.test/v1",
        max_retries=0,
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
    )
    llm = LLM(Settings(llm_api_key=SecretStr("test-key"), llm_model="gpt-6-luna", llm_max_retries=0), client)
    result = asyncio.run(classify_confirmation(llm, question="¿Bloqueo?", text="sí"))
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
