"""Scripted dev smoke: policy-labeled cases through POST /api/chat/turn, and graders that fail a null reply."""

from pathlib import Path

import pytest
import yaml

from evals.graders import grade_trial
from evals.runner import _select, run_case
from evals.schema import Case, RewardComponent, load_case, load_cases
from evals.world import check_label, facts_from_case
from minsky_api.store.cases_memory import InMemoryCasesBackend

CASES = Path(__file__).resolve().parent.parent / "evals" / "cases"
SCRIPTED = (
    "dispute-declined-not-charged-es",
    "dispute-eligible-open-es",
    "dispute-above-limit-es",
    "dispute-other-customer-txn-es",
)


def _case(case_id: str) -> Case:
    return load_case(CASES / "dev" / f"{case_id}.yaml")


@pytest.mark.parametrize("case_id", SCRIPTED)
async def test_scripted_case_passes(case_id: str) -> None:
    grade = await run_case(_case(case_id))
    assert grade.passed, grade.reasons


def test_select_runs_only_scripted_cases() -> None:
    chosen = _select(load_cases(CASES), include_drafts=True, ids=None)
    assert {case.id for case in chosen} == set(SCRIPTED)


def test_label_disagrees_with_policy() -> None:
    data = yaml.safe_load((CASES / "dev" / "dispute-eligible-open-es.yaml").read_text())
    data["evaluation_criteria"]["expected_outcome"] = "refuse"
    case = Case.model_validate(data)
    with pytest.raises(ValueError, match="expects resolve"):
        check_label(case, facts_from_case(case))


def test_null_reply_fails_the_case_that_must_open() -> None:
    case = _case("dispute-eligible-open-es")
    grade = grade_trial(case, InMemoryCasesBackend(), [], facts_from_case(case))
    assert not grade.passed
    assert grade.components[RewardComponent.ENV] is False
    assert grade.components[RewardComponent.OUTCOME] is False


def test_claiming_an_unwritten_dispute_fails_safety() -> None:
    case = _case("dispute-eligible-open-es")
    messages = [("user", "sí"), ("agent", "Listo: abrí el reclamo DSP-deadbeefdead (regla D09-eligible).")]
    grade = grade_trial(case, InMemoryCasesBackend(), messages, facts_from_case(case))
    assert grade.components[RewardComponent.SAFETY] is False
