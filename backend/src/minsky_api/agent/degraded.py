"""Degraded mode: when the model is unavailable, say the site is under maintenance and end the conversation.

The model cannot ask the customer anything, so the assistant does not send a case to a person on its own (a case
goes to a person only after the customer confirms the charge or accepts an agent). The reply is written by code:
the site is under maintenance, ask technical service, and, if the failed turn had already opened a dispute, that
dispute's reference, read back from the store. The conversation becomes terminal (`unavailable`).

Speech grounding: compose_speech raises SpeechError. Paths that catch RuntimeError and send a code
template (clarify, inform, _speak_verified) never reach this module. Paths that let SpeechError escape after the
bounded _speak retries (e.g. confirm_txn) end here too.
"""

from __future__ import annotations

from copy import deepcopy

from openai import APIConnectionError, APIStatusError, RateLimitError

from minsky_api.agent.language import default_language_detector
from minsky_api.agent.speak import SpeechError
from minsky_api.agent.state import ConversationState, Phase, Terminal
from minsky_api.agent.wording import terminal_reply
from minsky_api.llm.client import ModelMismatchError, ModelOutputError
from minsky_api.tools.context import ToolContext

# Wrong model id, unusable structured reply, escaping speech grounding, and provider outages
# (connection / rate-limit / 5xx; APITimeoutError is an APIConnectionError). Auth and other 4xx
# stay a 503 (a configuration fault, not something to tell the customer is maintenance).
_HANDOFF_FAILURES: tuple[type[BaseException], ...] = (
    ModelMismatchError,
    ModelOutputError,
    SpeechError,
    APIConnectionError,
    RateLimitError,
)


def is_model_failure(exc: BaseException) -> bool:
    """True when the turn should answer with the maintenance message rather than a bare 503."""
    if isinstance(exc, _HANDOFF_FAILURES):
        return True
    return isinstance(exc, APIStatusError) and exc.status_code >= 500


async def close_on_outage(
    ctx: ToolContext,
    live: ConversationState,
    user_text: str,
    *,
    language: str | None = None,
) -> tuple[ConversationState, str]:
    """End the conversation with the maintenance message. Creates no case.

    `live` is the state after the failed turn mutated it. A dispute the failed turn already opened is visible in
    the store and is reported by its reference; nothing else is claimed.
    """
    after = deepcopy(live)
    stripped = user_text.strip()
    if not after.messages or after.messages[-1][0] != "user":
        after.turn_count += 1
        after.messages.append(("user", stripped))
    lang = language or after.language or default_language_detector().detect(stripped)
    after.language = lang

    dispute_id: str | None = None
    if after.selected_txn_id and ctx.session.customer_id:
        existing = ctx.cases.get_dispute_by_transaction(
            customer_id=ctx.session.customer_id,
            transaction_id=after.selected_txn_id,
        )
        if existing is not None:
            dispute_id = existing.dispute_id

    after.phase = Phase.DONE
    after.pending_question = None
    after.terminal = Terminal("unavailable", dispute_id=dispute_id)
    after.acts.append("unavailable")
    reply = terminal_reply(lang, after.terminal)
    after.messages.append(("agent", reply))
    return after, reply
