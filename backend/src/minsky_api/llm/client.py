"""One interface to the language model, over any OpenAI-compatible Responses API (ADR 0008).

The OpenAI API, Bedrock's `/openai/v1` endpoint and other compatible providers differ only in base URL,
model id and key, so switching provider is configuration. The model id is pinned in settings and checked
on every response (AGENTS: the model that answered must be the model requested). Retries are bounded
(the SDK's own, with backoff) and every call has a timeout. Each result carries the token usage and
latency that traces and eval reports need.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Literal

from openai import OpenAI
from pydantic import BaseModel

from minsky_api.config import Settings, get_settings

ReasoningEffort = Literal["none", "low", "medium", "high", "xhigh", "max"]


class ModelMismatchError(RuntimeError):
    """The provider answered with a different model than the pinned one."""


class LLMNotConfiguredError(RuntimeError):
    """No API key: the system must degrade to a human, never guess (docs/architecture.md)."""


@dataclass(frozen=True)
class LLMResult[T: BaseModel]:
    text: str
    parsed: T | None
    model: str
    input_tokens: int
    output_tokens: int
    latency_ms: float


class LLM:
    def __init__(self, settings: Settings | None = None, client: OpenAI | None = None) -> None:
        self.settings = settings or get_settings()
        if client is None:
            if self.settings.llm_api_key is None:
                raise LLMNotConfiguredError("MINSKY_LLM_API_KEY is not set")
            client = OpenAI(
                api_key=self.settings.llm_api_key.get_secret_value(),
                base_url=self.settings.llm_base_url,
                timeout=self.settings.llm_timeout_s,
                max_retries=self.settings.llm_max_retries,
            )
        self.client = client

    def respond[T: BaseModel](
        self,
        instructions: str,
        messages: list[dict[str, str]],
        *,
        schema: type[T] | None = None,
        reasoning_effort: ReasoningEffort = "low",
        max_output_tokens: int = 1024,
    ) -> LLMResult[T]:
        """One model turn. With `schema`, the reply is parsed into it (structured output)."""
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
            response = self.client.responses.parse(text_format=schema, **common)
            parsed = response.output_parsed
        else:
            response = self.client.responses.create(**common)
            parsed = None
        latency_ms = (time.perf_counter() - started) * 1000

        # providers return a dated snapshot of the pinned id (e.g. "gpt-6-luna-2026-09-22")
        if not response.model.startswith(self.settings.llm_model.removeprefix("us.").removeprefix("global.")):
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
