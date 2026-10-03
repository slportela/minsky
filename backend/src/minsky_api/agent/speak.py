"""One model call chooses the next conversational step and writes the customer text.

Code passes the allowed acts and the verified facts. An act outside that list, or a card-block
claim without a verified block, is refused before anything is sent.
"""

from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from minsky_api.agent.prompts import render
from minsky_api.llm.client import LLM

Act = Literal[
    "clarify",
    "confirm_txn",
    "confirm_open",
    "offer_block",
    "inform",
    "refuse",
    "handoff",
    "abort",
    "ask_again",
]


class Speech(BaseModel):
    model_config = ConfigDict(extra="forbid")

    act: Act
    text: str = Field(min_length=1)
    claims_card_blocked: bool = False


async def compose_speech(
    llm: LLM,
    *,
    language: str,
    allowed: tuple[str, ...],
    facts: dict[str, object],
) -> Speech:
    """The model's act and wording. Raises when the act is not allowed or the block claim is unverified."""
    if not allowed:
        raise RuntimeError("compose_speech: no allowed act")
    payload = json.dumps(
        {"language": language, "allowed": list(allowed), "facts": facts},
        ensure_ascii=False,
        default=str,
        sort_keys=True,
    )
    result = await llm.respond(
        render("agent.speak.j2"),
        [{"role": "user", "content": payload}],
        schema=Speech,
        reasoning_effort="low",
        max_output_tokens=400,
    )
    parsed = result.parsed
    if not isinstance(parsed, Speech):
        raise RuntimeError("compose_speech: model returned no parsed schema")
    if parsed.act not in allowed:
        raise RuntimeError(f"compose_speech: act {parsed.act} is not allowed")
    if parsed.claims_card_blocked and facts.get("card_blocked") is not True:
        raise RuntimeError("compose_speech: unverified card block")
    return parsed
