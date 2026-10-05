"""Scripted dev smoke: policy-labeled cases through POST /api/chat/turn, and graders that fail a null reply."""

from decimal import Decimal
from pathlib import Path
from typing import ClassVar

import pytest
import yaml

from evals.budget import SpendBudget
from evals.graders import grade_trial
from evals.runner import _select, run_case, run_trial
from evals.schema import Case, RewardComponent, load_case, load_cases
from evals.world import check_label, facts_from_case
from minsky_api.store.cases_memory import InMemoryCasesBackend

CASES = Path(__file__).resolve().parent.parent / "evals" / "cases"
SCRIPTED = (
    "dispute-decimal-amount-es",
    "dispute-miss-retain-merchant-es",
    "dispute-expired-session-es",
    "dispute-declined-not-charged-es",
    "dispute-eligible-open-es",
    "dispute-eligible-open-pt",
    "dispute-eligible-open-mixed",
    "dispute-above-limit-es",
    "dispute-above-limit-pt",
    "dispute-other-customer-txn-es",
    "dispute-auth-header-denied-es",
    "dispute-clarify-retain-merchant-es",
    "dispute-policy-failure-retry-es",
    "dispute-fraud-block-es",
    "dispute-fraud-no-block-es",
    "dispute-already-disputed-es",
    "dispute-natural-reply-then-yes-es",
    "dispute-unclear-replies-handoff-es",
    "dispute-offer-declined-then-dispute-es",
    "dispute-follow-up-after-case-es",
    "model-outage-handoff-es",
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


# ---- the real extractor with an injected provider fault ----------------------------------------------------------


_BUDGET = SpendBudget(Decimal(1), Decimal("0.10"), Decimal("0.50"))  # real extraction requires one


class _ClosableClient:
    def __init__(self) -> None:
        self.closed = False

    async def close(self) -> None:
        self.closed = True


class _ProviderLLM:
    """Stands for minsky_api.llm.client.LLM: it owns an async client that the runner has to close."""

    instances: ClassVar[list["_ProviderLLM"]] = []

    def __init__(self, settings: object = None) -> None:
        self.client = _ClosableClient()
        type(self).instances.append(self)

    async def respond(self, *args: object, **kwargs: object) -> object:
        raise AssertionError("the outage case faults the first call, so no real call is made")


async def test_the_real_extractor_closes_the_client_behind_an_injected_fault(monkeypatch: pytest.MonkeyPatch) -> None:
    """`python -m evals.runner --extractor real` crashed on model-outage-handoff-es, the last case: the trial closes
    the client of its llm, and the fault wrapper around it had none. The exception came out of a `finally`, so it
    ended the whole run and lost the summary and artifacts of every trial before it."""
    _ProviderLLM.instances = []
    monkeypatch.setattr("evals.runner.LLM", _ProviderLLM)
    record = await run_trial(_case("model-outage-handoff-es"), extractor="real", budget=_BUDGET)
    assert record.status == "passed", (record.status, record.error_class, record.grade)
    assert len(_ProviderLLM.instances) == 1
    assert _ProviderLLM.instances[0].client.closed is True


async def test_an_unwrapped_real_llm_is_still_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    """No fault wrapper: the client is closed whether or not the trial itself succeeds."""
    _ProviderLLM.instances = []
    monkeypatch.setattr("evals.runner.LLM", _ProviderLLM)
    await run_trial(_case("dispute-eligible-open-es"), extractor="real", budget=_BUDGET)
    assert len(_ProviderLLM.instances) == 1
    assert _ProviderLLM.instances[0].client.closed is True


def test_a_case_whose_customer_never_answers_yes_or_no_must_expect_escalate() -> None:
    data = yaml.safe_load((CASES / "dev" / "dispute-unclear-replies-handoff-es.yaml").read_text())
    data["evaluation_criteria"]["expected_outcome"] = "resolve"
    case = Case.model_validate(data)
    with pytest.raises(ValueError, match="never answers yes or no must expect escalate"):
        check_label(case, facts_from_case(case))
