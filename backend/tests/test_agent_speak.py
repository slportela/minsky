"""The model chooses an allowed next step and writes the customer text."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from minsky_api.agent.speak import action_claims, compose_speech, ungrounded_number
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


def test_compose_speech_rejects_a_confirm_that_drops_facts():
    with pytest.raises(RuntimeError, match="drops"):
        asyncio.run(
            compose_speech(
                RecordingLLM("confirm_txn", "¿Es este cargo?"),  # type: ignore[arg-type]
                language="es",
                allowed=("confirm_txn",),
                facts={"transaction_id": "T1", "merchant": "Cafe", "amount_usd": "25.00", "when": "2026-06-10"},
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
        ("Sua contestação foi registrada", "dispute_exists"),
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


def test_an_open_dispute_is_supported_by_the_existing_reference():
    text = "Ya hay un reclamo abierto para ese cargo con la referencia DSP-abc123."
    speech = asyncio.run(
        compose_speech(
            RecordingLLM("inform", text),  # type: ignore[arg-type]
            language="es",
            allowed=("inform",),
            facts={"existing_dispute_id": "DSP-abc123"},
        )
    )
    assert speech.text == text
    with pytest.raises(RuntimeError, match="unverified dispute_exists"):
        asyncio.run(
            compose_speech(
                RecordingLLM("inform", "Tu reclamo quedó abierto."),  # type: ignore[arg-type]
                language="es",
                allowed=("inform",),
                facts={"reason": "x"},
            )
        )


@pytest.mark.parametrize(
    ("text", "claim"),
    [
        ("Tu tarjeta ya no podrá usarse.", "card_blocked"),
        ("Tu tarjeta quedó desactivada.", "card_blocked"),
        ("Seu cartão foi cancelado.", "card_blocked"),
        ("Tu reclamo está en trámite.", "dispute_exists"),
        ("Tu número de reclamo es el siguiente.", "dispute_exists"),
        ("Te devolveremos el dinero.", "refund"),
        ("Vas a recibir tu dinero pronto.", "refund"),
        ("Você vai receber o seu dinheiro de volta.", "refund"),
        ("Te paso con un asesor.", "handoff"),
        ("Un especialista tomará tu caso.", "handoff"),
    ],
)
def test_adversarial_paraphrases_are_claims(text: str, claim: str):
    assert claim in action_claims(text)


def test_team_follow_up_after_an_open_dispute_is_not_a_handoff_claim():
    assert action_claims("Listo, tu reclamo DSP-1 quedó abierto. Nuestro equipo lo revisará.") == {"dispute_exists"}


def test_numbers_must_come_from_the_facts():
    facts = {"merchant": "Cafe", "amount": "25.00 USD", "when": "10 de junio de 2026", "reason": "más de 120 días"}
    ok = "Encontré este cargo: Cafe, 25.00 USD, 10 de junio de 2026. Tiene más de 120 días. Ref. HO-9c1a47831750."
    assert ungrounded_number(ok, facts) is None
    assert ungrounded_number("Cafe, 25 USD.", facts) is None  # 25.00 and 25 are the same amount
    assert ungrounded_number("Se resolverá en 15 días.", facts) == "15"
    assert ungrounded_number("Te devolvemos 30.00 USD.", facts) == "30.00"
    with pytest.raises(RuntimeError, match="ungrounded number 15"):
        asyncio.run(
            compose_speech(
                RecordingLLM("inform", "Tu caso se resolverá en 15 días."),  # type: ignore[arg-type]
                language="es",
                allowed=("inform",),
                facts={"reason": "x"},
            )
        )


# The phrasings from the #37 review, each claiming an action that no fact supports.
REVIEW_PHRASES = [
    "He abierto tu reclamo.",
    "Te he devuelto el dinero.",
    "O valor foi estornado.",
    "Tu reclamo está en marcha.",
    "He abierto el reclamo.",
    "Hemos abierto una disputa.",
    "Ya quedó registrado tu reclamo.",
    "Tu tarjeta ya no se puede usar.",
    "Te hemos reembolsado el dinero.",
    "El dinero volverá a tu cuenta.",
    "Un asesor te contactará hoy.",
    "Fiz a abertura da reclamação.",
]


@pytest.mark.parametrize("text", REVIEW_PHRASES)
def test_review_phrases_are_refused_without_supporting_facts(text: str):
    with pytest.raises(RuntimeError, match="unverified"):
        asyncio.run(
            compose_speech(
                RecordingLLM("inform", text),  # type: ignore[arg-type]
                language="es",
                allowed=("inform",),
                facts={},
            )
        )


def test_new_opening_needs_the_new_dispute_not_the_existing_one():
    with pytest.raises(RuntimeError, match="unverified dispute_opened"):
        asyncio.run(
            compose_speech(
                RecordingLLM("inform", "He abierto tu reclamo DSP-abc."),  # type: ignore[arg-type]
                language="es",
                allowed=("inform",),
                facts={"existing_dispute_id": "DSP-abc"},
            )
        )


def test_d02_reversal_is_supported_only_by_the_reversal_fact():
    text = "Ese cargo ya fue revertido y el dinero volvió a tu cuenta."
    ok = asyncio.run(
        compose_speech(
            RecordingLLM("inform", text),  # type: ignore[arg-type]
            language="es",
            allowed=("inform",),
            facts={"charge_reversed": True},
        )
    )
    assert ok.text == text
    with pytest.raises(RuntimeError, match="unverified refund"):
        asyncio.run(
            compose_speech(
                RecordingLLM("inform", text),  # type: ignore[arg-type]
                language="es",
                allowed=("inform",),
                facts={},
            )
        )


def test_review_reproductions_of_invented_facts_are_refused():
    facts = {"merchant": "Cafe", "amount": "25.00 USD", "when": "10 de junio de 2026"}
    fee = "Cafe, 25.00 USD, 10 de junio de 2026. ¿Es este? Te cobraremos 900 USD de comisión por el trámite."
    assert ungrounded_number(fee, facts) == "900"
    assert ungrounded_number("El trámite estará resuelto el 2026-06-20.", facts) == "2026-06-20"
    assert ungrounded_number("Se cobrará un 5% de comisión.", facts) == "5"
    assert ungrounded_number("Tu cargo del 2026-06-10.", facts) is None  # the same date as the fact


_CANDIDATES = "1. Cafe, 25.00 USD, 10 de junio de 2026\n2. Cafe, 30.00 USD, 10 de junio de 2026"


@pytest.mark.parametrize(
    "text",
    [
        # Replies the live model wrote (2026-10-04, dispute-clarify-retain-merchant-es) that the guard used to
        # refuse with "reply drops candidates": same facts, different punctuation, or no list at all.
        (
            "¿Cuál de estos cargos no reconoces? "
            "1. Cafe, 25.00 USD, 10 de junio de 2026; 2. Cafe, 30.00 USD, 10 de junio de 2026."
        ),
        (
            "¿Cuál de estos cargos no reconoces: "
            "1. Cafe, 25.00 USD, 10 de junio de 2026; o 2. Cafe, 30.00 USD, 10 de junio de 2026?"
        ),
        (
            "Veo dos cargos de Cafe el 10 de junio de 2026: uno de 25.00 USD y otro de 30.00 USD. "
            "¿Cuál de los dos no reconoces?"
        ),
    ],
)
def test_clarify_is_not_refused_for_how_it_punctuates_the_options(text: str):
    speech = asyncio.run(
        compose_speech(
            RecordingLLM("clarify", text),  # type: ignore[arg-type]
            language="es",
            allowed=("clarify",),
            facts={"candidates": _CANDIDATES},
        )
    )
    assert speech.act == "clarify"


def test_clarify_still_refuses_an_invented_number():
    """Dropping the verbatim-list requirement must not let a clarification add figures the facts lack."""
    with pytest.raises(RuntimeError, match="ungrounded number"):
        asyncio.run(
            compose_speech(
                RecordingLLM("clarify", "¿Cuál de estos? Te cobraremos 900 USD de comisión."),  # type: ignore[arg-type]
                language="es",
                allowed=("clarify",),
                facts={"candidates": _CANDIDATES},
            )
        )


def test_compose_speech_rejects_a_confident_reply_in_the_other_language():
    """From #27 (Arturo Collazo Gil): a Spanish reply to a Portuguese customer used to be accepted."""
    for language, text in (
        ("pt", "T1: necesito el comercio y el monto."),
        ("es", "T1: preciso do comércio e do valor."),
    ):
        with pytest.raises(RuntimeError, match="language"):
            asyncio.run(
                compose_speech(
                    RecordingLLM("clarify", text),  # type: ignore[arg-type]
                    language=language,
                    allowed=("clarify",),
                    facts={"transaction_id": "T1"},
                )
            )


def test_compose_speech_accepts_a_reply_in_the_right_language():
    for language, text in (
        ("pt", "T1: preciso do comércio e do valor."),
        ("es", "T1: necesito el comercio y el monto."),
    ):
        speech = asyncio.run(
            compose_speech(
                RecordingLLM("clarify", text),  # type: ignore[arg-type]
                language=language,
                allowed=("clarify",),
                facts={"transaction_id": "T1"},
            )
        )
        assert speech.text == text
