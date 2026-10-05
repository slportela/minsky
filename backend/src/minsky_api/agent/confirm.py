"""Classify a pending yes/no reply. The model names the decision; code performs the action."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from minsky_api.agent.prompts import render
from minsky_api.llm.client import LLM

Decision = Literal["yes", "no", "unclear"]

# The classifier reasons before it answers and that counts toward the limit: 64 cut the JSON off mid-value in
# 2 of 180 live calls. Only the tokens used are billed, so the headroom costs nothing.
_MAX_OUTPUT_TOKENS = 256


class Confirmation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Decision


async def classify_confirmation(llm: LLM, *, question: str, text: str) -> Confirmation:
    """yes, no, or unclear. A missing or cut-off schema is unclear: the caller must not act."""
    try:
        result = await llm.respond(
            render("agent.confirm.j2"),
            [
                {"role": "assistant", "content": question},
                {"role": "user", "content": text},
            ],
            schema=Confirmation,
            reasoning_effort="low",
            max_output_tokens=_MAX_OUTPUT_TOKENS,
        )
    except ValidationError:
        # The reply was cut off mid-JSON, so there is no decision. Only this: an API error still propagates.
        return Confirmation(decision="unclear")
    parsed = result.parsed
    if not isinstance(parsed, Confirmation):
        return Confirmation(decision="unclear")
    return parsed
