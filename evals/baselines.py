"""Baseline systems that run behind the same chat route, tools and graders as the real orchestrator.

`always_escalate` is today's process: every contact goes to a human agent, who does the intake.
It uses the real create_handoff tool (session-checked, audited), so its handoffs are graded the
same way; it just never decides anything itself.
"""

from __future__ import annotations

from minsky_api.agent import orchestrator
from minsky_api.agent.language import LanguageDetector
from minsky_api.agent.state import ConversationState, Phase
from minsky_api.identity.session import SessionState
from minsky_api.llm.client import LLM
from minsky_api.tools.context import ToolContext
from minsky_api.tools.schemas import CreateHandoffArgs


async def always_escalate(
    state: ConversationState,
    text: str,
    ctx: ToolContext,
    llm: LLM,
    detector: LanguageDetector | None = None,
) -> tuple[ConversationState, str]:
    if ctx.session.state != SessionState.VALID:
        raise PermissionError("session must be valid")
    if not ctx.session.customer_id or ctx.session.customer_id != state.customer_id:
        raise PermissionError("conversation_customer_mismatch")
    stripped = text.strip()
    state.turn_count += 1
    state.messages.append(("user", stripped))
    if state.phase == Phase.DONE:
        reply = "Un asesor ya tiene tu caso y te contactará."
        state.acts.append("handoff")
    else:
        # Module attribute on purpose: the eval runner instruments orchestrator.create_handoff.
        result = await orchestrator.create_handoff(
            ctx,
            CreateHandoffArgs(
                idempotency_key=f"{state.conversation_id}:baseline",
                reason="baseline_all_to_agent",
                rule_id=None,
                facts={"customer_request": stripped},
                actions=(),
            ),
        )
        state.phase = Phase.DONE
        state.acts.append("handoff")
        reply = f"Te paso con un asesor. Tu referencia es {result.handoff.handoff_id}."
    state.messages.append(("agent", reply))
    return state, reply
