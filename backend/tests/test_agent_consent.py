"""Explicit yes and no: the only consent that can authorize a write. A reply that says more is not consent."""

from __future__ import annotations

import pytest

from minsky_api.agent.consent import explicit_no, explicit_yes


@pytest.mark.parametrize("text", ["sí", "Sí", "si", "SI", "sim", "Sim.", "yes", "  sí  ", "sí!", "sí,"])
def test_a_plain_yes_is_explicit(text: str):
    assert explicit_yes(text)
    assert not explicit_no(text)


@pytest.mark.parametrize("text", ["no", "No", "NO", "no.", "No!", "no,", "  no  ", "não", "Não.", "nao", "NAO"])
def test_a_plain_no_is_explicit(text: str):
    assert explicit_no(text)
    assert not explicit_yes(text)


@pytest.mark.parametrize(
    "text",
    [
        "",
        "tal vez",
        "sí, es ese",
        "sí, pero no",
        "no, gracias",
        "no sé",
        "mejor no",
        "claro",
        "ok",
        "perfecto",
        "nope",
        "n o",
    ],
)
def test_anything_more_than_a_plain_yes_or_no_is_neither(text: str):
    """A longer reply is the model's to classify, as evidence. It never authorizes a write by itself."""
    assert not explicit_yes(text)
    assert not explicit_no(text)
