import asyncio
import json

import httpx2
import pytest
from openai import AsyncOpenAI
from pydantic import BaseModel, SecretStr, ValidationError

from minsky_api.config import Settings
from minsky_api.llm.client import LLM, LLMNotConfiguredError, ModelMismatchError, model_matches

SETTINGS = Settings(llm_api_key=SecretStr("test-key"), llm_model="gpt-6-luna", llm_max_retries=0)


def _response(text: str, model: str = "gpt-6-luna-2026-09-22") -> dict:
    return {
        "id": "resp_1",
        "object": "response",
        "created_at": 0,
        "status": "completed",
        "model": model,
        "output": [
            {
                "type": "message",
                "id": "msg_1",
                "status": "completed",
                "role": "assistant",
                "content": [{"type": "output_text", "text": text, "annotations": []}],
            }
        ],
        "parallel_tool_calls": False,
        "tool_choice": "auto",
        "tools": [],
        "usage": {
            "input_tokens": 12,
            "output_tokens": 3,
            "total_tokens": 15,
            "input_tokens_details": {"cached_tokens": 0},
            "output_tokens_details": {"reasoning_tokens": 0},
        },
    }


def _llm(reply: dict, sent: list[dict]) -> LLM:
    def handler(request: httpx2.Request) -> httpx2.Response:
        sent.append(json.loads(request.content))
        return httpx2.Response(200, json=reply)

    client = AsyncOpenAI(
        api_key="test-key",
        base_url="http://provider.test/v1",
        max_retries=0,
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
    )
    return LLM(SETTINGS, client)


def test_sends_the_pinned_model_and_returns_text_and_usage():
    sent: list[dict] = []
    result = asyncio.run(_llm(_response("hola"), sent).respond("Sé breve.", [{"role": "user", "content": "hola"}]))
    assert result.text == "hola"
    assert (result.input_tokens, result.output_tokens) == (12, 3)
    assert sent[0]["model"] == "gpt-6-luna"
    assert sent[0]["store"] is False
    assert sent[0]["reasoning"] == {"effort": "low"}


@pytest.mark.parametrize("returned", ["gpt-5-mini", "gpt-6-luna-mini", "gpt-6-lunar"])
def test_a_different_or_sibling_model_in_the_response_is_an_error(returned):
    llm = _llm(_response("hola", model=returned), [])
    with pytest.raises(ModelMismatchError):
        asyncio.run(llm.respond("x", [{"role": "user", "content": "hola"}]))


@pytest.mark.parametrize(
    "pinned,returned,ok",
    [
        ("gpt-6-luna", "gpt-6-luna", True),
        ("gpt-6-luna", "gpt-6-luna-2026-09-22", True),  # dated snapshot of the pinned model
        ("gpt-6-luna", "gpt-6-luna-mini", False),  # sibling sharing the prefix
        ("gpt-6-luna", "gpt-6-luna-2026-09-22-mini", False),
        ("us.openai.gpt-6-luna", "openai.gpt-6-luna", True),  # Bedrock inference profile
        ("us.openai.gpt-6-luna", "openai.gpt-6-sol", False),
    ],
)
def test_model_matches_is_exact_up_to_a_dated_snapshot(pinned, returned, ok):
    assert model_matches(pinned, returned) is ok


def test_structured_output_is_parsed_into_the_schema():
    class Reason(BaseModel):
        reason: str
        confident: bool

    sent: list[dict] = []
    reply = _response(json.dumps({"reason": "unrecognized", "confident": True}))
    llm = _llm(reply, sent)
    result = asyncio.run(llm.respond("Classify.", [{"role": "user", "content": "no reconozco"}], schema=Reason))
    assert result.parsed == Reason(reason="unrecognized", confident=True)
    assert sent[0]["text"]["format"]["type"] == "json_schema"


@pytest.mark.parametrize("key", [None, "", "   "])
def test_a_missing_or_blank_api_key_is_not_configured(key):
    # compose sets MINSKY_LLM_API_KEY="" when it is unset: that must also say "not configured"
    with pytest.raises(LLMNotConfiguredError):
        LLM(Settings(llm_api_key=SecretStr(key) if key is not None else None))


def test_negative_retries_are_rejected():
    with pytest.raises(ValidationError):
        Settings(llm_max_retries=-1)
