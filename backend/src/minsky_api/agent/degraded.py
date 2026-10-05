"""Degraded mode: when the model is unavailable, hand off with context and tell the customer.

docs/architecture.md, principle 5: "Degrade to a human, never to a guess." The reply states only what
is true after the fact: a handoff exists (create_handoff reads it back) and its reference. It claims no
other action, because the failed turn may have written nothing or something; the customer already holds
any reference from earlier in the conversation.

The handoff carries identifiers and the failure class, never the customer's free text.

Speech grounding: compose_speech raises SpeechError. Paths that catch RuntimeError and send a code
template (clarify, inform, DONE follow-up, _speak_verified) never reach this module. Paths that let
SpeechError escape after the bounded _speak retries (e.g. confirm_txn) become a verified handoff here.
"""

from __future__ import annotations

from copy import deepcopy

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    InternalServerError,
    RateLimitError,
)

from minsky_api.agent.language import default_language_detector
from minsky_api.agent.speak import SpeechError
from minsky_api.agent.state import ConversationState, Phase
from minsky_api.llm.client import ModelMismatchError, ModelOutputError
from minsky_api.tools.bank import create_handoff
from minsky_api.tools.context import ToolContext
from minsky_api.tools.schemas import CreateHandoffArgs

# Wrong model id, unusable structured reply, and speech grounding that escaped local templates.
# Provider outages are matched by is_model_failure (timeout / connection / rate-limit / 5xx only).
# Auth, bad request, and other 4xx stay a 503 without inventing a handoff (deployment / config fault).
MODEL_FAILURES: tuple[type[Exception], ...] = (
    ModelMismatchError,
    ModelOutputError,
    SpeechError,
)

_PROVIDER_OUTAGE: tuple[type[Exception], ...] = (
    APITimeoutError,
    APIConnectionError,
    RateLimitError,
    InternalServerError,
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


def is_model_failure(exc: BaseException) -> bool:
    """True when the turn should hand off: model/schema/speech failures and provider outages."""
    if isinstance(exc, MODEL_FAILURES):
        return True
    if isinstance(exc, _PROVIDER_OUTAGE):
        return True
    return isinstance(exc, APIStatusError) and exc.status_code >= 500


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
    after.acts.append("handoff")
    after.messages.append(("user", user_text))
    after.messages.append(("agent", reply))
    return after, reply
