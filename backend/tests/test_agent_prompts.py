"""Jinja2 prompt loader for the agent."""

from minsky_api.agent.prompts import render


def test_extract_prompt_loads_without_request_ids():
    text = render("agent.extract.j2")
    assert "out_of_scope" in text
    assert "transaction_id" in text


def test_speak_prompt_states_the_premises():
    text = render("agent.speak.j2")
    assert "allowed" in text
    assert "facts" in text
    assert "language" in text
    assert "claims_card_blocked" in text
    assert "merchant" in text
    assert "amount" in text
    assert "when" in text
    assert "candidates" in text


def test_the_extractor_is_told_what_day_it_is_so_relative_dates_can_resolve():
    """ "Un cargo de ayer" came back with no date because the model never knew the date. It now gets the system's
    `today` as a message of its own; the instructions stay the same text."""
    import asyncio
    from typing import Any

    from minsky_api.agent.extract import DisputeDetails, extract_dispute_details
    from minsky_api.config import get_settings
    from minsky_api.llm.client import LLMResult

    seen: dict[str, Any] = {}

    class _Recorder:
        async def respond(self, instructions: str, messages: list[dict[str, str]], **kwargs: Any) -> LLMResult[Any]:
            seen.update(instructions=instructions, messages=messages)
            parsed = DisputeDetails()
            return LLMResult(text="", parsed=parsed, model="m", input_tokens=0, output_tokens=0, latency_ms=0.0)

    asyncio.run(extract_dispute_details(_Recorder(), "quiero reclamar un cargo de ayer"))  # type: ignore[arg-type]
    today = get_settings().today.isoformat()
    assert seen["messages"] == [
        {"role": "user", "content": f"Fecha de hoy: {today}"},
        {"role": "user", "content": "quiero reclamar un cargo de ayer"},
    ]
    assert today not in seen["instructions"] and "ayer" in seen["instructions"]
