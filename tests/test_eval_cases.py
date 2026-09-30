from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from evals.checks import check_cases
from evals.schema import Case, load_cases

CASES = Path(__file__).resolve().parent.parent / "evals" / "cases"


def _base(**overrides) -> dict:
    case = yaml.safe_load((CASES / "dev" / "dispute-unrecognized-charge-es.yaml").read_text())
    case.update(overrides)
    return case


def test_all_committed_cases_load_and_have_no_errors():
    cases = load_cases(CASES)
    assert cases
    errors = [f for f in check_cases(cases, CASES.parent.parent / "prompts") if f.level == "error"]
    assert not errors, errors


def test_generated_case_requires_generator_model():
    with pytest.raises(ValidationError):
        Case.model_validate(_base(generator_model=None))


def test_safety_cannot_be_dropped_from_reward_basis():
    data = _base()
    data["evaluation_criteria"]["reward_basis"] = ["outcome", "env"]
    with pytest.raises(ValidationError):
        Case.model_validate(data)


def test_escalation_must_grade_handoff():
    data = _base()
    data["evaluation_criteria"]["expected_outcome"] = "escalate"
    with pytest.raises(ValidationError):
        Case.model_validate(data)


def test_unknown_fields_are_rejected():
    with pytest.raises(ValidationError):
        Case.model_validate(_base(expected="resolve"))


def test_customer_reused_between_test_and_dev_is_an_error():
    dev = Case.model_validate(_base())
    test = Case.model_validate(_base(id="copy-in-test", split="test"))
    codes = {f.code for f in check_cases([dev, test])}
    assert "test_customer_leakage" in codes


def test_case_text_copied_into_a_prompt_is_an_error(tmp_path):
    case = Case.model_validate(_base())
    (tmp_path / "system.md").write_text("Example:\n" + case.user_scenario.instructions)
    codes = {f.code for f in check_cases([case], tmp_path)}
    assert "prompt_leakage" in codes
