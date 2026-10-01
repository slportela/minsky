"""Structured extraction of dispute details from the customer's message (LLM, schema-forced)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from minsky_api.agent.prompts import render
from minsky_api.llm.client import LLM


class DisputeDetails(BaseModel):
    model_config = ConfigDict(extra="forbid")

    out_of_scope: bool = False
    merchant: str | None = Field(default=None, max_length=100)
    amount: Decimal | None = None
    date_from: date | None = None
    date_to: date | None = None
    customer_says_not_me: bool = False
    transaction_id: str | None = Field(default=None, max_length=64)


async def extract_dispute_details(llm: LLM, text: str) -> DisputeDetails:
    result = await llm.respond(
        render("agent.extract.j2"),
        [{"role": "user", "content": text}],
        schema=DisputeDetails,
        reasoning_effort="low",
        max_output_tokens=256,
    )
    if result.parsed is None:
        raise RuntimeError("extract_dispute_details: model returned no parsed schema")
    return result.parsed
