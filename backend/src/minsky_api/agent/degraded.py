"""Degraded mode: when the model is unavailable, hand off with context and tell the customer.

docs/architecture.md, principle 5: "Degrade to a human, never to a guess." The reply states only what
is true after the fact: a handoff exists (create_handoff reads it back) and its reference, plus any
write from the failed turn that is still visible in the store (for example an opened dispute).

The handoff carries identifiers, prior acts, and the failure class, never the customer's free text.

Speech grounding: compose_speech raises SpeechError. Paths that catch RuntimeError and send a code
template (clarify, inform, DONE follow-up, _speak_verified) never reach this module. Paths that let
SpeechError escape after the bounded _speak retries (e.g. confirm_txn) become a verified handoff here.
"""

from __future__ import annotations

from copy import deepcopy

from openai import APIConnectionError, APIStatusError, RateLimitError

from minsky_api.agent.language import default_language_detector
from minsky_api.agent.speak import SpeechError
from minsky_api.agent.state import ConversationState, Phase
from minsky_api.llm.client import ModelMismatchError, ModelOutputError
from minsky_api.tools.bank import create_handoff
from minsky_api.tools.context import ToolContext
from minsky_api.tools.schemas import CreateHandoffArgs

# Wrong model id, unusable structured reply, escaping speech grounding, and provider outages
# (connection / rate-limit / 5xx; APITimeoutError is an APIConnectionError). Auth and other 4xx
# stay a 503 without a handoff.
_HANDOFF_FAILURES: tuple[type[BaseException], ...] = (
    ModelMismatchError,
    ModelOutputError,
    SpeechError,
    APIConnectionError,
    RateLimitError,
)

REASON = "assistant_unavailable"

_REPLY = {
    "es": (
        "Tuve un problema técnico y no puedo seguir con tu solicitud en este momento. "
        "Pasé tu caso a un asesor para que lo revise con la información que ya tenemos "
        "(referencia {handoff_id}). No hace falta que repitas nada."
    ),
    "pt": (
        "Tive um problema técnico e não consigo continuar com a sua solicitação agora. "
        "Encaminhei o seu caso a um atendente, que vai analisá-lo com as informações que já temos "
        "(referência {handoff_id}). Não precisa repetir nada."
    ),
}

_REPLY_WITH_DISPUTE = {
    "es": (
        "Tuve un problema técnico y no puedo seguir con tu solicitud en este momento. "
        "Tu reclamo {dispute_id} ya quedó registrado. Pasé tu caso a un asesor "
        "(referencia {handoff_id}). No hace falta que repitas nada."
    ),
    "pt": (
        "Tive um problema técnico e não consigo continuar com a sua solicitação agora. "
        "Sua contestação {dispute_id} já foi registrada. Encaminhei o seu caso a um atendente "
        "(referência {handoff_id}). Não precisa repetir nada."
    ),
}


def is_model_failure(exc: BaseException) -> bool:
    """True when the turn should hand off rather than return a bare 503."""
    if isinstance(exc, _HANDOFF_FAILURES):
        return True
    return isinstance(exc, APIStatusError) and exc.status_code >= 500


def outage_reply(language: str, handoff_id: str, *, dispute_id: str | None = None) -> str:
    if dispute_id:
        return _REPLY_WITH_DISPUTE.get(language, _REPLY_WITH_DISPUTE["es"]).format(
            handoff_id=handoff_id, dispute_id=dispute_id
        )
    return _REPLY.get(language, _REPLY["es"]).format(handoff_id=handoff_id)


async def hand_off_on_outage(
    ctx: ToolContext,
    live: ConversationState,
    user_text: str,
    failure: Exception,
    *,
    language: str | None = None,
) -> tuple[ConversationState, str]:
    """Create the handoff from the live (possibly half-finished) turn and close the conversation.

    `live` is the state after the failed turn mutated it: side effects already committed (selected
    transaction, opened dispute, prior acts) must reach the advisor. The idempotency key matches
    orchestrator handoffs for this turn so a second outage path cannot enqueue a duplicate case.
    """
    after = deepcopy(live)
    stripped = user_text.strip()
    if not after.messages or after.messages[-1][0] != "user":
        after.turn_count += 1
        after.messages.append(("user", stripped))
    lang = language or after.language or default_language_detector().detect(stripped)
    after.language = lang

    dispute_id: str | None = None
    actions: list[str] = []
    if after.selected_txn_id and ctx.session.customer_id:
        existing = ctx.cases.get_dispute_by_transaction(
            customer_id=ctx.session.customer_id,
            transaction_id=after.selected_txn_id,
        )
        if existing is not None:
            dispute_id = existing.dispute_id
            actions.append(f"dispute_opened:{dispute_id}")

    result = await create_handoff(
        ctx,
        CreateHandoffArgs(
            # Same key shape as orchestrator._handoff: one handoff per conversation turn.
            idempotency_key=f"{after.conversation_id}:{after.turn_count}",
            reason=REASON,
            rule_id=after.rule_id,
            facts={
                "transaction_id": after.selected_txn_id,
                "phase": after.phase.value,
                "failure": type(failure).__name__,
                "language": lang,
                "acts": list(after.acts),
                "route": after.route,
                "dispute_id": dispute_id,
            },
            actions=tuple(actions),
        ),
    )
    reply = outage_reply(lang, result.handoff.handoff_id, dispute_id=dispute_id)
    after.phase = Phase.DONE
    after.pending_question = None
    after.acts.append("handoff")
    after.messages.append(("agent", reply))
    return after, reply
