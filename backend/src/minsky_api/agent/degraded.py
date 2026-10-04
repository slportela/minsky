"""Degraded mode: when the model is unavailable, hand off with context and tell the customer.

docs/architecture.md, principle 5: "Degrade to a human, never to a guess." The reply states only what
is true after the fact: a handoff exists (create_handoff reads it back) and its reference. It claims no
other action, because the failed turn may have written nothing or something; the customer already holds
any reference from earlier in the conversation.

The handoff carries identifiers and the failure class, never the customer's free text.
"""

from __future__ import annotations

import re
from copy import deepcopy

from openai import OpenAIError

from minsky_api.agent.state import ConversationState, Phase
from minsky_api.llm.client import ModelMismatchError, ModelOutputError
from minsky_api.tools.bank import create_handoff
from minsky_api.tools.context import ToolContext
from minsky_api.tools.schemas import CreateHandoffArgs

# Provider errors (timeout, connection, rate limit, 5xx), a wrong model id, and an unusable reply.
# Configuration errors (LLMNotConfiguredError) stay a 503: that is a deployment fault, not an outage.
MODEL_FAILURES: tuple[type[Exception], ...] = (OpenAIError, ModelMismatchError, ModelOutputError)

REASON = "assistant_unavailable"

# Portuguese-only signals (no Spanish word uses ã, õ or ç). Spanish is the default.
_PORTUGUESE = re.compile(r"[ãõç]|\b(?:você|voce|não|nao|obrigad[oa]|quero|minha|meu|tenho|fatura|olá)\b", re.IGNORECASE)

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


def language_of(texts: list[str]) -> str:
    """ "pt" when any customer message carries a Portuguese-only signal, else "es"."""
    return "pt" if any(_PORTUGUESE.search(text) for text in texts) else "es"


def outage_reply(language: str, handoff_id: str) -> str:
    return _REPLY[language].format(handoff_id=handoff_id)


async def hand_off_on_outage(
    ctx: ToolContext, before: ConversationState, user_text: str, failure: Exception
) -> tuple[ConversationState, str]:
    """Create the handoff and return the conversation as it stands after the failed turn.

    `before` is the snapshot taken before the turn: the failed turn may have changed the live state
    halfway, and only the snapshot is consistent with what the customer has been told so far.
    """
    language = language_of([text for role, text in before.messages if role == "user"] + [user_text])
    result = await create_handoff(
        ctx,
        CreateHandoffArgs(
            idempotency_key=f"{before.conversation_id}:{before.turn_count + 1}:{REASON}",
            reason=REASON,
            facts={
                "transaction_id": before.selected_txn_id,
                "phase": before.phase.value,
                "failure": type(failure).__name__,
                "language": language,
            },
        ),
    )
    reply = outage_reply(language, result.handoff.handoff_id)
    after = deepcopy(before)
    after.turn_count += 1
    after.phase = Phase.DONE
    after.messages.append(("user", user_text))
    after.messages.append(("agent", reply))
    return after, reply
