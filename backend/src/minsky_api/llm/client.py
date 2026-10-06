"""One interface to the language model, over any OpenAI-compatible Responses API (ADR 0008).

The OpenAI API, Bedrock's `/openai/v1` endpoint and other compatible providers differ only in base URL,
model id and key, so switching provider is configuration. The client is async, so model calls never
block the API's event loop. The model id is pinned in settings and checked on every response (AGENTS:
the model that answered must be the model requested). Retries are bounded (the SDK's own, with backoff)
and every call has a timeout. Each result carries the token usage and latency that traces and eval
reports need.
"""

from __future__ import annotations

import re
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal

from openai import AsyncOpenAI, ContentFilterFinishReasonError, LengthFinishReasonError
from pydantic import BaseModel, ValidationError

from minsky_api.config import Settings, get_settings
from minsky_api.observability import start_span

ReasoningEffort = Literal["none", "low", "medium", "high", "xhigh", "max"]

# Bedrock inference profiles prefix the model id with a geography ("us.openai.gpt-6-luna"); responses
# name the model itself. Providers may also answer with a dated snapshot of it ("gpt-6-luna-2026-09-22").
_PROFILE_PREFIX = re.compile(r"^(us|eu|apac|global)\.")
_SNAPSHOT_SUFFIX = re.compile(r"-\d{4}-\d{2}-\d{2}")


def model_matches(pinned: str, returned: str) -> bool:
    """True if `returned` is the pinned model or a dated snapshot of it, never a sibling model."""
    base = _PROFILE_PREFIX.sub("", pinned)
    if returned == base:
        return True
    return returned.startswith(base) and _SNAPSHOT_SUFFIX.fullmatch(returned[len(base) :]) is not None


class ModelMismatchError(RuntimeError):
    """The provider answered with a different model than the pinned one."""


class ModelOutputError(RuntimeError):
    """The provider answered, but the reply does not fit the requested schema."""


class LLMNotConfiguredError(RuntimeError):
    """No usable API key: the system must degrade to a human, never guess (docs/architecture.md)."""


@dataclass(frozen=True)
class LLMResult[T: BaseModel]:
    text: str
    parsed: T | None
    model: str
    input_tokens: int
    output_tokens: int
    latency_ms: float


@dataclass(frozen=True)
class ToolSpec:
    """A function the model may call: JSON-schema parameters, no side effects of its own."""

    name: str
    description: str
    parameters: dict[str, Any]


@dataclass(frozen=True)
class ToolCall:
    call_id: str
    name: str
    arguments: str  # the JSON text the model produced; the caller parses and validates it


@dataclass(frozen=True)
class AgentStep:
    """One model turn in a tool loop: some text, some tool calls, or both."""

    text: str
    tool_calls: tuple[ToolCall, ...]
    model: str
    input_tokens: int
    output_tokens: int
    latency_ms: float


class LLM:
    def __init__(self, settings: Settings | None = None, client: AsyncOpenAI | None = None) -> None:
        self.settings = settings or get_settings()
        if client is None:
            key = self.settings.llm_api_key
            # compose passes MINSKY_LLM_API_KEY="" when it is unset: blank means not configured too
            if key is None or not key.get_secret_value().strip():
                raise LLMNotConfiguredError("MINSKY_LLM_API_KEY is not set")
            client = AsyncOpenAI(
                api_key=key.get_secret_value(),
                base_url=self.settings.llm_base_url,
                timeout=self.settings.llm_timeout_s,
                max_retries=self.settings.llm_max_retries,
            )
        self.client = client

    async def respond[T: BaseModel](
        self,
        instructions: str,
        messages: list[dict[str, str]],
        *,
        schema: type[T] | None = None,
        reasoning_effort: ReasoningEffort = "low",
        max_output_tokens: int = 1024,
    ) -> LLMResult[T]:
        """One model turn. With `schema`, the reply is parsed into it (structured output)."""
        with start_span("llm.respond", model=self.settings.llm_model):
            started = time.perf_counter()
            common = {
                "model": self.settings.llm_model,
                "instructions": instructions,
                "input": messages,
                "reasoning": {"effort": reasoning_effort},
                "max_output_tokens": max_output_tokens,
                "store": False,  # nothing kept on the provider's side beyond its own retention policy
            }
            if schema is not None:
                try:
                    response = await self.client.responses.parse(text_format=schema, **common)
                except (ValidationError, LengthFinishReasonError, ContentFilterFinishReasonError) as exc:
                    # Cut-off / filtered / invalid schema: degrade or re-ask, never a customer 400.
                    raise ModelOutputError("the model reply does not fit the requested schema") from exc
                parsed = response.output_parsed
            else:
                response = await self.client.responses.create(**common)
                parsed = None
            latency_ms = (time.perf_counter() - started) * 1000

            if not model_matches(self.settings.llm_model, response.model):
                raise ModelMismatchError(f"asked for {self.settings.llm_model}, got {response.model}")
            usage = response.usage
            return LLMResult(
                text=response.output_text,
                parsed=parsed,
                model=response.model,
                input_tokens=usage.input_tokens if usage else 0,
                output_tokens=usage.output_tokens if usage else 0,
                latency_ms=latency_ms,
            )

    async def step(
        self,
        instructions: str,
        items: list[dict[str, Any]],
        *,
        tools: Sequence[ToolSpec],
        reasoning_effort: ReasoningEffort = "low",
        max_output_tokens: int = 1024,
    ) -> AgentStep:
        """One turn of a tool-using agent.

        `items` is the conversation in Responses API input form: role messages plus the loop's own
        `function_call` and `function_call_output` items. They carry no provider ids and no reasoning
        items, which is valid because nothing is stored on the provider's side (`store=False`). One tool
        call at a time, so the caller answers each call in order.
        """
        with start_span("llm.step", model=self.settings.llm_model):
            started = time.perf_counter()
            kwargs: dict[str, Any] = {
                "model": self.settings.llm_model,
                "instructions": instructions,
                "input": items,
                "tools": [
                    {"type": "function", "name": t.name, "description": t.description, "parameters": t.parameters}
                    for t in tools
                ],
                "parallel_tool_calls": False,
                "reasoning": {"effort": reasoning_effort},
                "max_output_tokens": max_output_tokens,
                "store": False,
            }
            response = await self.client.responses.create(**kwargs)
            latency_ms = (time.perf_counter() - started) * 1000

            if not model_matches(self.settings.llm_model, response.model):
                raise ModelMismatchError(f"asked for {self.settings.llm_model}, got {response.model}")
            if response.status == "incomplete":
                # Cut off by the token limit or filtered: a half-written tool call or reply must not be used.
                raise ModelOutputError("the model reply was cut off")
            calls = tuple(
                ToolCall(call_id=item.call_id, name=item.name, arguments=item.arguments)
                for item in response.output
                if item.type == "function_call"
            )
            usage = response.usage
            return AgentStep(
                text=response.output_text,
                tool_calls=calls,
                model=response.model,
                input_tokens=usage.input_tokens if usage else 0,
                output_tokens=usage.output_tokens if usage else 0,
                latency_ms=latency_ms,
            )
