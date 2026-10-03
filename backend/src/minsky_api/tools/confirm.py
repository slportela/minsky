"""Classify a reply to a confirmation the system already sent.

The tool calls the model API and returns the decision. It does not open a dispute or block a card.
"""

from __future__ import annotations

from minsky_api.agent.confirm import classify_confirmation
from minsky_api.llm.client import LLM
from minsky_api.tools.bank import _audit, _require_customer
from minsky_api.tools.context import ToolContext
from minsky_api.tools.schemas import ClassifyReplyArgs, ClassifyReplyResult

_TOOL = "classify_reply"


async def classify_reply(ctx: ToolContext, args: ClassifyReplyArgs, llm: LLM) -> ClassifyReplyResult:
    audit_args = {"question_len": len(args.question), "text_len": len(args.text)}
    _require_customer(ctx, tool=_TOOL, args=audit_args)
    decision = (await classify_confirmation(llm, question=args.question, text=args.text)).decision
    _audit(ctx, tool=_TOOL, args=audit_args, outcome="ok", reason=decision)
    return ClassifyReplyResult(decision=decision)
