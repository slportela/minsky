"""classify_reply checks the session, audits, and calls the model API. It does not act."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from minsky_api.identity import SessionState, ToolSession
from minsky_api.llm.client import LLMResult
from minsky_api.store.cases_memory import InMemoryCasesBackend
from minsky_api.tools.confirm import classify_reply
from minsky_api.tools.context import ToolContext
from minsky_api.tools.errors import ToolDenied
from minsky_api.tools.schemas import ClassifyReplyArgs


class RecordingLLM:
    def __init__(self, decision: str | None) -> None:
        self.decision = decision
        self.messages: list[dict[str, str]] | None = None

    async def respond(self, instructions: str, messages: list[dict[str, str]], **kwargs: Any) -> LLMResult[Any]:
        self.messages = messages
        schema = kwargs["schema"]
        parsed = None if self.decision is None else schema(decision=self.decision)
        return LLMResult(
            text="" if parsed is None else parsed.model_dump_json(),
            parsed=parsed,
            model="gpt-6-luna",
            input_tokens=1,
            output_tokens=1,
            latency_ms=1.0,
        )


class FailingRecordingLLM:
    def __init__(self) -> None:
        self.messages: list[dict[str, str]] | None = None

    async def respond(self, instructions: str, messages: list[dict[str, str]], **kwargs: Any) -> LLMResult[Any]:
        self.messages = messages
        raise RuntimeError("confirm api outage")


def _ctx(*, valid: bool = True) -> ToolContext:
    session = ToolSession(
        session_id="s1",
        state=SessionState.VALID if valid else SessionState.EXPIRED,
        customer_id="C1",
    )
    return ToolContext(session=session, db=None, cases=InMemoryCasesBackend())  # type: ignore[arg-type]


def test_invalid_session_is_denied_and_the_api_is_not_called():
    ctx = _ctx(valid=False)
    llm = RecordingLLM("yes")
    args = ClassifyReplyArgs(question="¿Es este?", text="sí")
    with pytest.raises(ToolDenied):
        asyncio.run(classify_reply(ctx, args, llm))  # type: ignore[arg-type]
    assert llm.messages is None
    assert any(row.tool == "classify_reply" and row.outcome == "denied" for row in ctx.cases.list_audit())


def test_api_error_propagates_and_does_not_authorize():
    ctx = _ctx()
    llm = FailingRecordingLLM()
    with pytest.raises(RuntimeError, match="confirm api outage"):
        asyncio.run(classify_reply(ctx, ClassifyReplyArgs(question="¿Es este?", text="sí"), llm))  # type: ignore[arg-type]
    assert llm.messages == [
        {"role": "assistant", "content": "¿Es este?"},
        {"role": "user", "content": "sí"},
    ]
    assert not any(row.tool == "classify_reply" and row.outcome == "ok" for row in ctx.cases.list_audit())


def test_reply_is_classified_against_the_stored_question():
    ctx = _ctx()
    llm = RecordingLLM("no")
    result = asyncio.run(
        classify_reply(ctx, ClassifyReplyArgs(question="¿Abro el reclamo?", text="mejor no"), llm)  # type: ignore[arg-type]
    )
    assert result.decision == "no"
    assert llm.messages == [
        {"role": "assistant", "content": "¿Abro el reclamo?"},
        {"role": "user", "content": "mejor no"},
    ]
    assert any(
        row.tool == "classify_reply" and row.outcome == "ok" and row.reason == "no" for row in ctx.cases.list_audit()
    )


def test_unparsed_schema_is_unclear_and_does_not_authorize():
    ctx = _ctx()
    llm = RecordingLLM(None)
    result = asyncio.run(classify_reply(ctx, ClassifyReplyArgs(question="¿Es este?", text="sí"), llm))  # type: ignore[arg-type]
    assert result.decision == "unclear"
    assert any(row.tool == "classify_reply" and row.reason == "unclear" for row in ctx.cases.list_audit())


@pytest.mark.parametrize("text", ["no", "No.", "NO!", "  no  ", "¡no!", "não", "Não.", "nao"])
def test_a_bare_no_is_a_decline_and_the_model_is_not_asked(text: str):
    ctx = _ctx()
    llm = RecordingLLM("yes")  # the model would have said yes: it must not get the chance
    args = ClassifyReplyArgs(question="Si no hiciste esta compra, alguien podría usar tu tarjeta. ¿Bloqueo?", text=text)
    result = asyncio.run(classify_reply(ctx, args, llm))  # type: ignore[arg-type]
    assert result.decision == "no"
    assert llm.messages is None
    assert any(
        row.tool == "classify_reply" and row.outcome == "ok" and row.reason == "no" for row in ctx.cases.list_audit()
    )


@pytest.mark.parametrize("text", ["no, gracias", "no sé", "mejor no", "sí, pero no", "¿no?", "no quiero"])
def test_a_reply_that_says_more_than_no_still_goes_to_the_model(text: str):
    ctx = _ctx()
    llm = RecordingLLM("unclear")
    result = asyncio.run(classify_reply(ctx, ClassifyReplyArgs(question="¿Bloqueo tu tarjeta?", text=text), llm))  # type: ignore[arg-type]
    assert result.decision == "unclear"
    assert llm.messages is not None


def test_the_session_check_comes_before_the_bare_no_shortcut():
    ctx = _ctx(valid=False)
    with pytest.raises(ToolDenied):
        asyncio.run(classify_reply(ctx, ClassifyReplyArgs(question="¿Es este?", text="no"), RecordingLLM("no")))  # type: ignore[arg-type]
    assert any(row.tool == "classify_reply" and row.outcome == "denied" for row in ctx.cases.list_audit())
    assert not any(row.tool == "classify_reply" and row.outcome == "ok" for row in ctx.cases.list_audit())
