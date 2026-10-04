"""The model chooses an allowed next step and writes the customer text."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from minsky_api.agent.speak import action_claims, compose_speech
from minsky_api.llm.client import LLMResult


class RecordingLLM:
    def __init__(self, act: str, text: str, *, claims_card_blocked: bool = False) -> None:
        self.act = act
        self.text = text
        self.claims_card_blocked = claims_card_blocked

    async def respond(self, instructions: str, messages: list[dict[str, str]], **kwargs: Any) -> LLMResult[Any]:
        schema = kwargs["schema"]
        parsed = schema(act=self.act, text=self.text, claims_card_blocked=self.claims_card_blocked)
        return LLMResult(
            text=parsed.model_dump_json(),
            parsed=parsed,
            model="gpt-6-luna",
            input_tokens=1,
            output_tokens=1,
            latency_ms=1.0,
        )


def test_compose_speech_keeps_an_allowed_act_and_the_model_text():
    speech = asyncio.run(
        compose_speech(
            RecordingLLM("clarify", "¿Me das el comercio y el monto del cargo T1?"),  # type: ignore[arg-type]
            language="es",
            allowed=("clarify", "handoff"),
            facts={"transaction_id": "T1"},
        )
    )
    assert speech.act == "clarify"
    assert "T1" in speech.text


def test_compose_speech_rejects_an_act_outside_the_allowed_list():
    with pytest.raises(RuntimeError, match="not allowed"):
        asyncio.run(
            compose_speech(
                RecordingLLM("handoff", "Te derivo."),  # type: ignore[arg-type]
                language="es",
                allowed=("clarify",),
                facts={},
            )
        )


def test_compose_speech_rejects_an_unverified_card_block_claim():
    with pytest.raises(RuntimeError, match="unverified"):
        asyncio.run(
            compose_speech(
                RecordingLLM("handoff", "Bloqueé la tarjeta. HO-abc", claims_card_blocked=True),  # type: ignore[arg-type]
                language="es",
                allowed=("handoff",),
                facts={"handoff_id": "HO-abc", "card_blocked": False},
            )
        )


def test_compose_speech_rejects_a_block_sentence_when_the_flag_is_false():
    with pytest.raises(RuntimeError, match="unverified"):
        asyncio.run(
            compose_speech(
                RecordingLLM("inform", "Bloqueei o cartão.", claims_card_blocked=False),  # type: ignore[arg-type]
                language="pt",
                allowed=("inform",),
                facts={"card_blocked": False},
            )
        )


def test_compose_speech_rejects_an_open_or_refund_or_handoff_sentence_without_facts():
    for text in ("Ya abrí el reclamo.", "Reembolsé el monto.", "Te derivé con un especialista."):
        with pytest.raises(RuntimeError, match="unverified"):
            asyncio.run(
                compose_speech(
                    RecordingLLM("inform", text),  # type: ignore[arg-type]
                    language="es",
                    allowed=("inform",),
                    facts={},
                )
            )


def test_compose_speech_allows_a_verified_open_and_an_offer_to_block():
    opened = asyncio.run(
        compose_speech(
            RecordingLLM("inform", "Quedó abierto el reclamo DSP-abc."),  # type: ignore[arg-type]
            language="es",
            allowed=("inform",),
            facts={"dispute_id": "DSP-abc"},
        )
    )
    assert opened.text.startswith("Quedó abierto")
    offer = asyncio.run(
        compose_speech(
            RecordingLLM("offer_block", "Alguien podría estar usando tu tarjeta."),  # type: ignore[arg-type]
            language="es",
            allowed=("offer_block",),
            facts={"reason": "x"},
        )
    )
    assert offer.act == "offer_block"


def test_compose_speech_rejects_an_internal_rule_id():
    with pytest.raises(RuntimeError, match="rule id"):
        asyncio.run(
            compose_speech(
                RecordingLLM("offer_block", "Esto parece fraude (D06-possible-fraud)."),  # type: ignore[arg-type]
                language="es",
                allowed=("offer_block",),
                facts={"reason": "x"},
            )
        )


def test_compose_speech_rejects_a_confirm_or_clarify_that_drops_facts():
    with pytest.raises(RuntimeError, match="drops"):
        asyncio.run(
            compose_speech(
                RecordingLLM("confirm_txn", "¿Es este cargo?"),  # type: ignore[arg-type]
                language="es",
                allowed=("confirm_txn",),
                facts={"transaction_id": "T1", "merchant": "Cafe", "amount_usd": "25.00", "when": "2026-06-10"},
            )
        )
    with pytest.raises(RuntimeError, match="drops"):
        asyncio.run(
            compose_speech(
                RecordingLLM("clarify", "¿Cuál de estos?"),  # type: ignore[arg-type]
                language="es",
                allowed=("clarify",),
                facts={"candidates": "1. Cafe T1 | 2. Cafe Sur T2"},
            )
        )


@pytest.mark.parametrize(
    ("text", "claim"),
    [
        ("Tu tarjeta fue bloqueada", "card_blocked"),
        ("Hemos bloqueado tu tarjeta", "card_blocked"),
        ("Seu cartão foi bloqueado", "card_blocked"),
        ("Se abrió la disputa", "dispute_opened"),
        ("Ya registré tu reclamo", "dispute_opened"),
        ("Sua contestação foi registrada", "dispute_opened"),
        ("Te devolvimos el dinero", "refund"),
        ("Pasé tu caso a un especialista", "handoff"),
    ],
)
def test_completed_action_phrasings_are_claims(text: str, claim: str):
    assert claim in action_claims(text)


@pytest.mark.parametrize(
    "text",
    [
        "¿Quieres que bloquee tu tarjeta ahora?",
        "Puedo bloquear tu tarjeta si me confirmas.",
        "Si confirmas, tu tarjeta quedará bloqueada.",
        "Seu cartão ficará bloqueado se você confirmar.",
        "Puedo abrir un reclamo por este cargo.",
        "No bloqueé la tarjeta.",
    ],
)
def test_offers_and_negations_are_not_claims(text: str):
    assert action_claims(text) == frozenset()


# --- completed-action claims, invented figures and the already-open case (review of #37) -------------------

import json  # noqa: E402
from pathlib import Path  # noqa: E402

PHRASES = json.loads((Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "claim_phrases.json").read_text())


@pytest.mark.parametrize("item", PHRASES["claim"], ids=lambda item: item["text"][:42])
def test_the_guard_sees_completed_and_promised_actions(item):
    assert item["kind"] in action_claims(item["text"])


@pytest.mark.parametrize("item", PHRASES["ok"], ids=lambda item: item["text"][:42])
def test_the_guard_leaves_offers_questions_negations_and_bank_facts_alone(item):
    assert not action_claims(item["text"])


def _speak(act: str, text: str, facts: dict[str, Any], allowed: tuple[str, ...] | None = None) -> Any:
    return asyncio.run(
        compose_speech(
            RecordingLLM(act, text),  # type: ignore[arg-type]
            language="es",
            allowed=allowed or (act,),
            facts=facts,
        )
    )


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("He abierto tu reclamo.", "dispute_opened"),
        ("Te he devuelto el dinero.", "refund"),
        ("Tu reclamo está en marcha.", "dispute_state"),
        ("Un asesor te contactará hoy.", "handoff"),
        ("He bloqueado tu tarjeta.", "card_blocked"),
    ],
)
def test_an_action_the_facts_do_not_support_is_refused(text, expected):
    with pytest.raises(RuntimeError, match=f"unverified {expected}"):
        _speak("inform", text, {})


CHARGE = {"merchant": "Cafe Central", "amount": "25.00 USD", "when": "10 de junio de 2026"}
CHARGE_TEXT = "Encontré este cargo: Cafe Central, 25.00 USD, 10 de junio de 2026. ¿Es este el que quieres disputar?"


def test_a_reply_that_keeps_the_facts_and_adds_a_fee_is_refused():
    text = CHARGE_TEXT + " Te cobraremos 900 USD de comisión por el trámite."
    with pytest.raises(RuntimeError, match="figure"):
        _speak("confirm_txn", text, CHARGE)


def test_an_invented_date_or_deadline_is_refused():
    with pytest.raises(RuntimeError, match="figure"):
        _speak("confirm_txn", CHARGE_TEXT + " El trámite estará resuelto el 20 de junio de 2026.", CHARGE)
    with pytest.raises(RuntimeError, match="figure"):
        _speak("confirm_txn", CHARGE_TEXT + " Un especialista responderá en 48 horas.", CHARGE)


def test_the_facts_verbatim_are_accepted_and_a_changed_amount_is_not():
    assert _speak("confirm_txn", CHARGE_TEXT, CHARGE).act == "confirm_txn"
    with pytest.raises(RuntimeError, match="drops amount"):
        _speak("confirm_txn", CHARGE_TEXT.replace("25.00", "250.00"), CHARGE)


def test_numbers_are_compared_by_value_not_by_spelling():
    from minsky_api.agent.speak import _numbers

    assert _numbers("25,00") == _numbers("25.00") == _numbers("25")
    assert _numbers("1,234.50") == {"1234.5"}
    assert _numbers("1.234,50") == {"1234.5"}
    assert _numbers("10/06/2026") == {"10", "6", "2026"}
    assert _numbers("10.06.2026") == {"10", "6", "2026"}


def test_list_markers_are_not_figures():
    assert _speak("inform", "Esto es lo que sigue:\n1. Revisaremos el cargo.\n2. Te avisaremos.", {}).act == "inform"


def test_an_already_open_dispute_can_be_described_with_its_reference():
    facts = {"existing_dispute_id": "DSP-0123456789ab", "reason": "ya hay un reclamo abierto para ese cargo"}
    text = "Tu reclamo DSP-0123456789ab ya está abierto; un especialista lo está revisando."
    assert _speak("inform", text, facts).act == "inform"


def test_an_already_open_dispute_is_not_reported_as_opened_now():
    facts = {"existing_dispute_id": "DSP-0123456789ab", "reason": "ya hay un reclamo abierto para ese cargo"}
    with pytest.raises(RuntimeError, match="unverified dispute_opened"):
        _speak("inform", "He abierto tu reclamo DSP-0123456789ab.", facts)


def test_restating_the_policy_reason_needs_no_reference():
    facts = {"reason": "ya hay un reclamo abierto para ese cargo"}
    assert _speak("inform", "Ya hay un reclamo abierto para ese cargo.", facts).act == "inform"


def test_a_handoff_promise_is_allowed_with_its_reference():
    facts = {"handoff_id": "HO-0123456789ab"}
    text = "Un asesor te contactará. Tu referencia es HO-0123456789ab."
    assert _speak("handoff", text, facts).act == "handoff"
