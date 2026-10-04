"""The grader's claim detector reads the same phrase table as the reply guard, with its own code."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from evals.claims import claims_in

PHRASES = json.loads((Path(__file__).parent / "fixtures" / "claim_phrases.json").read_text())
# The grader names the opening of a case and its state alike: a reference must exist for either.
KIND = {"dispute_state": "dispute_opened"}


@pytest.mark.parametrize("item", PHRASES["claim"], ids=lambda item: item["text"][:42])
def test_the_grader_sees_completed_and_promised_actions(item):
    assert KIND.get(item["kind"], item["kind"]) in claims_in(item["text"])


@pytest.mark.parametrize("item", PHRASES["ok"], ids=lambda item: item["text"][:42])
def test_the_grader_leaves_offers_questions_negations_and_bank_facts_alone(item):
    assert not claims_in(item["text"])


def test_the_grader_does_not_import_the_guard():
    source = (Path(__file__).parent.parent / "evals" / "claims.py").read_text()
    assert "minsky_api" not in source.replace("`minsky_api.agent.speak`", "")
