"""The summary a person reads when a case is escalated in agentic mode (docs/agentic_dispute_agent.md, section 5).

Two parts, kept apart so a reader can tell what was verified from what was narrated:
- verified: read by code from the bank and the policy (customer profile, the transaction, the rule, what the
  customer was told). create_handoff adds the transaction's own facts again from bank.*.
- narrated: one paragraph a model writes from the conversation, labeled as unverified. It runs through the same
  number-grounding check as the replies; if the model fails or invents a figure, the customer's own words are
  attached instead, and the source field says so.
"""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from minsky_api.agent.degraded import is_model_failure
from minsky_api.agent.prompts import render
from minsky_api.agent.speak import ungrounded_number
from minsky_api.agent.state import ConversationState
from minsky_api.llm.client import LLM
from minsky_api.tools.bank import get_transaction
from minsky_api.tools.context import ToolContext
from minsky_api.tools.errors import ToolDenied
from minsky_api.tools.history import get_customer_profile
from minsky_api.tools.schemas import GetTransactionArgs

_MAX_TRANSCRIPT_MESSAGES = 30
_MAX_MESSAGE_CHARS = 600
_EXCERPT_CHARS = 800
_NARRATIVE_NOTE = "customer-reported and assistant-written, not verified by the bank"


class _NarrativeOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=1200)


def _transcript(state: ConversationState) -> list[dict[str, str]]:
    return [
        {"role": "customer" if role == "user" else "assistant", "text": text[:_MAX_MESSAGE_CHARS]}
        for role, text in state.messages[-_MAX_TRANSCRIPT_MESSAGES:]
    ]


def _customer_excerpt(state: ConversationState) -> str:
    said = " | ".join(text for role, text in state.messages if role == "user")
    return "Customer's own words: " + said[:_EXCERPT_CHARS]


async def _narrative(llm: LLM, state: ConversationState, verified: dict[str, Any]) -> dict[str, str]:
    transcript = _transcript(state)
    payload = {"transcript": transcript, "verified": verified}
    source: Literal["model", "transcript_excerpt"] = "model"
    try:
        result = await llm.respond(
            render("agent.summary.j2"),
            [{"role": "user", "content": json.dumps(payload, ensure_ascii=False, default=str)}],
            schema=_NarrativeOut,
            reasoning_effort="low",
            max_output_tokens=500,
        )
        parsed = result.parsed
        if not isinstance(parsed, _NarrativeOut):
            raise RuntimeError("summary: model returned no parsed schema")
        invented = ungrounded_number(parsed.text, payload)
        if invented is not None:
            raise RuntimeError(f"summary: ungrounded number {invented}")
        text = parsed.text
    except Exception as exc:
        # A person takes this case either way: a model failure or an invented figure never blocks the handoff.
        if not (isinstance(exc, RuntimeError) or is_model_failure(exc)):
            raise
        source = "transcript_excerpt"
        text = _customer_excerpt(state)
    return {"text": text, "source": source, "note": _NARRATIVE_NOTE}


async def build_handoff_facts(
    ctx: ToolContext,
    state: ConversationState,
    llm: LLM,
    *,
    rule_id: str | None,
) -> dict[str, Any]:
    """The `facts` of the handoff: what a person needs without reading the whole conversation."""
    profile = (await get_customer_profile(ctx)).profile.model_dump(mode="json", exclude_none=True)
    transaction: dict[str, Any] | None = None
    if state.selected_txn_id:
        try:
            txn = (await get_transaction(ctx, GetTransactionArgs(transaction_id=state.selected_txn_id))).transaction
        except ToolDenied:
            txn = None
        transaction = txn.model_dump(mode="json", exclude_none=True) if txn else None
    verified: dict[str, Any] = {
        "customer": profile,
        "transaction": transaction,
        "denial": {
            "rule_id": rule_id,
            "route": state.route,
            "told_to_customer": state.denial_text,
            "existing_dispute_id": state.existing_dispute_id,
        }
        if rule_id or state.denial_text
        else None,
        "search": {
            "queries_run": state.queries_run,
            "transactions_rejected_by_customer": list(state.rejected_txn_ids),
            "customer_turns": state.turn_count,
        },
    }
    verified = {k: v for k, v in verified.items() if v is not None}
    first_message = next((text for role, text in state.messages if role == "user"), None)
    facts: dict[str, Any] = {
        "transaction_id": state.selected_txn_id,
        "route": state.route,
        "customer_request": first_message,
        "customer_says_not_me": state.customer_says_not_me or None,
        "language": state.language,
        "verified_context": verified,
        "narrative": await _narrative(llm, state, verified),
    }
    return {key: value for key, value in facts.items() if value is not None}
