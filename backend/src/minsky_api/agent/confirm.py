"""Classify a pending yes/no reply. The model names the decision; code performs the action."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from minsky_api.agent.prompts import render
from minsky_api.llm.client import LLM

Decision = Literal["yes", "no", "unclear"]


class Confirmation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Decision


async def classify_confirmation(llm: LLM, text: str) -> Confirmation:
    """yes, no, or unclear. A missing schema is unclear: the caller must not act."""
    result = await llm.respond(
        render("agent.confirm.j2"),
        [{"role": "user", "content": text}],
        schema=Confirmation,
        reasoning_effort="low",
        max_output_tokens=64,
    )
    parsed = result.parsed
    if not isinstance(parsed, Confirmation):
        return Confirmation(decision="unclear")
    return parsed
