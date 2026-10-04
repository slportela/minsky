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
    assert "merchant" in text
    assert "amount_usd" in text
    assert "when" in text
    assert "candidates" in text
