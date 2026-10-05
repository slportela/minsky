import asyncio
import json
import re
from decimal import Decimal

import httpx2
import pytest
from openai import AsyncOpenAI, ContentFilterFinishReasonError, LengthFinishReasonError
from pydantic import BaseModel, SecretStr, ValidationError

from minsky_api.agent.extract import DisputeDetails
from minsky_api.config import Settings
from minsky_api.llm.client import (
    LLM,
    LLMNotConfiguredError,
    ModelMismatchError,
    ModelOutputError,
    ToolSpec,
    model_matches,
)

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


def test_dispute_amount_schema_is_provider_compatible_and_preserves_cents():
    sent: list[dict] = []
    details = DisputeDetails(amount=Decimal("25.37"), merchant="Cafe")
    llm = _llm(_response(details.model_dump_json()), sent)
    result = asyncio.run(llm.respond("Extract.", [{"role": "user", "content": "Cafe 25.37"}], schema=DisputeDetails))
    assert result.parsed is not None
    assert result.parsed.amount == Decimal("25.37")
    amount_schema = sent[0]["text"]["format"]["schema"]["properties"]["amount"]
    assert "(?" not in json.dumps(amount_schema)
    string_branch = next(branch for branch in amount_schema["anyOf"] if branch.get("type") == "string")
    assert re.fullmatch(string_branch["pattern"], "25.37")
    assert re.fullmatch(string_branch["pattern"], "25.00 USD") is None


def test_a_reply_that_does_not_fit_the_schema_is_a_model_failure_not_a_bad_request():
    # pydantic.ValidationError is a ValueError, which the chat route answers as the customer's 400
    llm = _llm(_response('{"amount": "not-a-number"}'), [])
    with pytest.raises(ModelOutputError):
        asyncio.run(llm.respond("Extract.", [{"role": "user", "content": "x"}], schema=DisputeDetails))


@pytest.mark.parametrize(
    "error_cls",
    [LengthFinishReasonError, ContentFilterFinishReasonError],
    ids=["length", "content_filter"],
)
def test_truncated_or_filtered_parse_is_a_model_output_error(error_cls, monkeypatch):
    class _Completion:
        usage = None

    async def boom(**_kwargs):
        raise error_cls(completion=_Completion())

    llm = LLM(SETTINGS, client=AsyncOpenAI(api_key="test", base_url="https://llm.invalid/v1"))
    monkeypatch.setattr(llm.client.responses, "parse", boom)
    with pytest.raises(ModelOutputError):
        asyncio.run(llm.respond("Extract.", [{"role": "user", "content": "x"}], schema=DisputeDetails))


def test_dispute_amount_still_rejects_non_numeric_values():
    with pytest.raises(ValidationError):
        DisputeDetails.model_validate({"amount": "not-a-number"})


@pytest.mark.parametrize("key", [None, "", "   "])
def test_a_missing_or_blank_api_key_is_not_configured(key):
    # compose sets MINSKY_LLM_API_KEY="" when it is unset: that must also say "not configured"
    with pytest.raises(LLMNotConfiguredError):
        LLM(Settings(llm_api_key=SecretStr(key) if key is not None else None))


def test_negative_retries_are_rejected():
    with pytest.raises(ValidationError):
        Settings(llm_max_retries=-1)


def _tool_response(*, status: str = "completed", model: str = "gpt-6-luna-2026-09-22") -> dict:
    body = _response("", model=model)
    body["status"] = status
    body["output"] = [
        {
            "type": "function_call",
            "id": "fc_1",
            "call_id": "call_1",
            "name": "query_transactions",
            "arguments": '{"sql": "SELECT 1"}',
            "status": "completed",
        }
    ]
    return body


_SPEC = [
    ToolSpec(
        name="query_transactions",
        description="Run a query.",
        parameters={"type": "object", "properties": {"sql": {"type": "string"}}, "required": ["sql"]},
    )
]


def test_step_returns_the_tool_call_and_sends_the_tools_without_parallel_calls():
    sent: list[dict] = []
    items = [{"role": "user", "content": "hola"}]
    step = asyncio.run(_llm(_tool_response(), sent).step("Sé breve.", items, tools=_SPEC))
    assert [(c.call_id, c.name, c.arguments) for c in step.tool_calls] == [
        ("call_1", "query_transactions", '{"sql": "SELECT 1"}')
    ]
    assert sent[0]["tools"][0]["name"] == "query_transactions"
    assert sent[0]["parallel_tool_calls"] is False
    assert sent[0]["store"] is False
    assert sent[0]["input"] == items


def test_step_returns_plain_text_when_the_model_does_not_call_a_tool():
    step = asyncio.run(
        _llm(_response("¿Cuál fue el comercio?"), []).step("x", [{"role": "user", "content": "hola"}], tools=_SPEC)
    )
    assert step.text == "¿Cuál fue el comercio?"
    assert step.tool_calls == ()


def test_step_refuses_a_reply_that_was_cut_off():
    with pytest.raises(ModelOutputError):
        asyncio.run(
            _llm(_tool_response(status="incomplete"), []).step("x", [{"role": "user", "content": "hola"}], tools=_SPEC)
        )


def test_step_checks_the_model_that_answered():
    with pytest.raises(ModelMismatchError):
        asyncio.run(
            _llm(_tool_response(model="gpt-5-mini"), []).step("x", [{"role": "user", "content": "hola"}], tools=_SPEC)
        )


# ---------------------------------------------------------------- llm smoke: the tool round trip


def _llm_sequence(replies: list[dict], sent: list[dict]) -> LLM:
    queue = list(replies)

    def handler(request: httpx2.Request) -> httpx2.Response:
        sent.append(json.loads(request.content))
        return httpx2.Response(200, json=queue.pop(0))

    client = AsyncOpenAI(
        api_key="test-key",
        base_url="http://provider.test/v1",
        max_retries=0,
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
    )
    return LLM(SETTINGS, client)


def _call_reply(arguments: str = '{"city": "Lima"}') -> dict:
    body = _tool_response()
    body["output"][0]["name"] = "lookup_temperature"
    body["output"][0]["arguments"] = arguments
    return body


def test_the_tool_round_trip_sends_the_result_back_without_provider_ids():
    from minsky_api.llm.smoke import tool_round_trip

    sent: list[dict] = []
    llm = _llm_sequence([_call_reply(), _response("Ahora hay 22 grados en Lima.")], sent)
    lines = asyncio.run(tool_round_trip(llm))
    assert "step 1: 1 tool call(s)" in lines[0] and "step 2: 0 tool call(s)" in "\n".join(lines)
    second = sent[1]["input"]
    assert [i.get("type") for i in second[1:]] == ["function_call", "function_call_output"]
    call, output = second[1], second[2]
    assert call["call_id"] == output["call_id"] == "call_1"
    assert "id" not in call and "id" not in output  # nothing is stored provider-side: no ids to dangle


@pytest.mark.parametrize(
    ("replies", "message"),
    [
        ([_response("Hace calor.")], "did not call the tool"),
        ([_call_reply("not json")], "not JSON"),
        ([_call_reply(), _response("No sé la temperatura.")], "does not use the tool result"),
        ([_call_reply(), _call_reply()], "called the tool again"),
    ],
)
def test_the_tool_round_trip_names_the_stage_that_failed(replies, message):
    from minsky_api.llm.smoke import SmokeFailure, tool_round_trip

    with pytest.raises(SmokeFailure, match=message) as failure:
        asyncio.run(tool_round_trip(_llm_sequence(replies, [])))
    assert failure.value.lines  # what happened before the failure is kept for the report


def test_a_provider_refusal_of_the_round_trip_prints_a_hint_about_reasoning_items(monkeypatch, capsys):
    from minsky_api.llm import smoke

    def handler(request: httpx2.Request) -> httpx2.Response:
        if b"function_call_output" in request.content:
            error = {"error": {"message": "Item 'fc_1' was provided without its required 'reasoning' item."}}
            return httpx2.Response(400, json=error)
        return httpx2.Response(200, json=_call_reply() if b"tools" in request.content else _response("hola"))

    client = AsyncOpenAI(
        api_key="test-key",
        base_url="http://provider.test/v1",
        max_retries=0,
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
    )
    monkeypatch.setattr(smoke, "get_settings", lambda: SETTINGS)
    monkeypatch.setattr(smoke, "LLM", lambda settings: LLM(settings, client))
    assert asyncio.run(smoke.run(tools=True)) == 1
    out = capsys.readouterr().out
    assert "FAILED the provider refused" in out and "reasoning" in out and "hint:" in out
    assert "test-key" not in out  # the key is never printed


def test_the_plain_smoke_does_not_run_the_tool_round_trip(monkeypatch, capsys):
    from minsky_api.llm import smoke

    sent: list[dict] = []
    llm = _llm_sequence([_response("Soy gpt-6-luna.")], sent)
    monkeypatch.setattr(smoke, "get_settings", lambda: SETTINGS)
    monkeypatch.setattr(smoke, "LLM", lambda settings: llm)
    assert asyncio.run(smoke.run(tools=False)) == 0
    assert len(sent) == 1  # one call: no tool round trip
