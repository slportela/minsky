"""Degraded mode: when the model is unavailable, hand off with context and tell the customer.

docs/architecture.md, principle 5: "Degrade to a human, never to a guess." The reply states only what
is true after the fact: a handoff exists (create_handoff reads it back) and its reference. It claims no
other action, because the failed turn may have written nothing or something; the customer already holds
any reference from earlier in the conversation.

The handoff carries identifiers and the failure class, never the customer's free text.
"""

from __future__ import annotations

from copy import deepcopy

from openai import OpenAIError

from minsky_api.agent.language import default_language_detector
from minsky_api.agent.speak import SpeechError
from minsky_api.agent.state import ConversationState, Phase
from minsky_api.llm.client import ModelMismatchError, ModelOutputError
from minsky_api.tools.bank import create_handoff
from minsky_api.tools.context import ToolContext
from minsky_api.tools.schemas import CreateHandoffArgs

# Provider errors, wrong model id, unusable structured reply, and speech grounding failures.
# Configuration errors (LLMNotConfiguredError) stay a 503: that is a deployment fault, not an outage.
MODEL_FAILURES: tuple[type[Exception], ...] = (
    OpenAIError,
    ModelMismatchError,
    ModelOutputError,
    SpeechError,
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


def outage_reply(language: str, handoff_id: str) -> str:
    return _REPLY.get(language, _REPLY["es"]).format(handoff_id=handoff_id)


async def hand_off_on_outage(
    ctx: ToolContext,
    before: ConversationState,
    user_text: str,
    failure: Exception,
    *,
    language: str | None = None,
) -> tuple[ConversationState, str]:
    """Create the handoff and return the conversation as it stands after the failed turn.

    `before` is the snapshot taken before the turn: the failed turn may have changed the live state
    halfway, and only the snapshot is consistent with what the customer has been told so far.
    `language` is the language already detected on the live state (before the turn it is often unset).
    """
    lang = language or before.language or default_language_detector().detect(user_text)
    result = await create_handoff(
        ctx,
        CreateHandoffArgs(
            idempotency_key=f"{before.conversation_id}:{before.turn_count + 1}:{REASON}",
            reason=REASON,
            facts={
                "transaction_id": before.selected_txn_id,
                "phase": before.phase.value,
                "failure": type(failure).__name__,
                "language": lang,
            },
        ),
    )
    reply = outage_reply(lang, result.handoff.handoff_id)
    after = deepcopy(before)
    after.turn_count += 1
    after.phase = Phase.DONE
    after.language = lang
    after.messages.append(("user", user_text))
    after.messages.append(("agent", reply))
    return after, reply
