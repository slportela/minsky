"""Structured extraction of dispute details from the customer's message (LLM, schema-forced)."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, WithJsonSchema

from minsky_api.agent.prompts import render
from minsky_api.config import get_settings
from minsky_api.llm.client import LLM


class DisputeDetails(BaseModel):
    model_config = ConfigDict(extra="forbid")

    out_of_scope: bool = False
    merchant: str | None = Field(default=None, max_length=100)
    # Decimal's generated regex uses lookaround, which Structured Outputs rejects. Keep Decimal
    # validation and precision locally while advertising supported number/string branches remotely.
    amount: (
        Annotated[
            Decimal,
            WithJsonSchema(
                {"anyOf": [{"type": "number"}, {"type": "string", "pattern": r"^[+-]?[0-9]+([.][0-9]+)?$"}]}
            ),
        ]
        | None
    ) = None
    # The currency only when the customer says it; "pesos" alone is not one (MXN, COP and ARS are all pesos).
    currency: Literal["USD", "COP", "ARS", "MXN"] | None = None
    # True only when the customer is loose about the amount ("more or less 80"). None, not False, so a later
    # message that does not repeat it does not undo it when the details are merged.
    approximate: bool | None = None
    date_from: date | None = None
    date_to: date | None = None
    # "today" is 0, "yesterday" 1, "N days ago" N. The model does not know today's date; code resolves it.
    days_ago: int | None = None
    # What kind of movement and, for purchases, which category. Both only narrow a search.
    transaction_type: Literal["Purchase", "Withdrawal", "Transfer", "Payment", "Deposit", "Adjustment"] | None = None
    category: Literal["Food", "Services", "Transport", "Entertainment", "Health", "Other"] | None = None
    customer_says_not_me: bool = False
    transaction_id: str | None = Field(default=None, max_length=64)
    reset_search: bool = False


async def extract_dispute_details(llm: LLM, text: str) -> DisputeDetails:
    # The model does not know what day it is: without this "ayer" or "el lunes" come back as no date at all. The date
    # is the system's `today` (the policy windows use the same one) and goes in its own message, so the instructions
    # stay the same text and cacheable.
    today = get_settings().today.isoformat()
    result = await llm.respond(
        render("agent.extract.j2"),
        [
            {"role": "user", "content": f"Fecha de hoy: {today}"},
            {"role": "user", "content": text},
        ],
        schema=DisputeDetails,
        reasoning_effort="low",
        max_output_tokens=256,
    )
    if result.parsed is None:
        raise RuntimeError("extract_dispute_details: model returned no parsed schema")
    return result.parsed


_MAX_DAYS_AGO = 366


def resolve_relative_dates(details: DisputeDetails, today: date) -> DisputeDetails:
    """Turn "yesterday" into a calendar day with the system's own today; written dates win over relative ones.

    A days_ago outside 0..366 is not a plausible reading of the message: it is dropped, and the search goes on
    without a date (the customer is asked for more detail if nothing else narrows it).
    """
    if details.days_ago is None:
        return details
    update: dict[str, object] = {"days_ago": None}
    if 0 <= details.days_ago <= _MAX_DAYS_AGO and details.date_from is None and details.date_to is None:
        day = today - timedelta(days=details.days_ago)
        update.update(date_from=day, date_to=day)
    return details.model_copy(update=update)
