"""Scripted dev smoke: policy-labeled cases through POST /api/chat/turn, and graders that fail a null reply."""

from pathlib import Path
from uuid import uuid4

import pytest
import yaml

from evals.evidence import ToolEvidence
from evals.graders import grade_trial
from evals.runner import _ReactiveUser, _select, run_case, run_trial
from evals.schema import Case, RewardComponent, load_case, load_cases
from evals.world import check_label, facts_from_case
from minsky_api.agent.state import ConversationState, Phase
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


# ---------------------------------------------------------------- agentic mode


@pytest.mark.parametrize("case_id", SCRIPTED)
async def test_scripted_case_passes_in_agentic_mode(case_id: str) -> None:
    grade = await run_case(_case(case_id), agent_mode="agentic")
    assert grade.passed, grade.reasons


async def test_agentic_trial_goes_through_the_agent_and_its_tools() -> None:
    record = await run_trial(_case("dispute-clarify-retain-merchant-es"), agent_mode="agentic")
    assert record.status == "passed"
    tools = [(t.tool, t.prior_phase) for t in record.tools]
    assert tools[:2] == [("query_transactions", "search"), ("query_transactions", "search")]
    assert ("open_dispute", "recognize") in tools
    assert record.model_calls and "tool_calls" in record.model_calls[0]  # agent steps are recorded as evidence
    assert len(record.requests) == 4


async def test_agentic_mode_asks_more_questions_than_the_script_has_turns() -> None:
    case = _case("dispute-above-limit-es")
    record = await run_trial(case, agent_mode="agentic")
    assert record.status == "passed"
    assert len(case.user_scenario.script or []) == 2 and len(record.requests) == 4  # card, recognition, offer, yes


async def test_an_agent_that_never_finds_the_transaction_fails_the_case() -> None:
    from unittest.mock import patch

    from evals import runner

    with patch.object(
        runner, "_scripted_agent_step", lambda items, facts: runner._text_step("Cuéntame más, por favor.")
    ):
        record = await run_trial(_case("dispute-eligible-open-es"), agent_mode="agentic")
    assert record.status == "failed"
    assert not any(t.tool == "open_dispute" for t in record.tools)


async def test_an_agent_that_proposes_the_wrong_transaction_does_not_open_it() -> None:
    from unittest.mock import patch

    from evals import runner

    def wrong(items: list, facts: object) -> object:
        ids = [i for i in items if i.get("type") == "function_call_output"]
        if not ids:
            return runner._tool_step("query_transactions", sql="SELECT transaction_id FROM transactions")
        # propose the look-alike, not the transaction the customer meant
        return runner._tool_step("propose_transaction", transaction_id="TRX-CAFE-OTHER", customer_says_not_me=False)

    with patch.object(runner, "_scripted_agent_step", wrong):
        record = await run_trial(_case("dispute-clarify-retain-merchant-es"), agent_mode="agentic")
    assert record.status == "failed"  # the customer says no to it, and the case needed TRX-REGRESSION opened
    assert not any(t.tool == "open_dispute" and t.outcome == "ok" for t in record.tools)


def _evidence(**kwargs: object) -> ToolEvidence:
    base: dict = {
        "tool": "open_dispute",
        "turn_index": 2,
        "args": {"transaction_id": "TRX-FIXTURE-D09", "confirmed": True},
        "result": {"created": True},
        "outcome": "ok",
        "customer_id": "CLI-FIXTURE-D09",
        "prior_phase": "recognize",
        "selected_transaction_id": "TRX-FIXTURE-D09",
        "user_text": "sí",
        "consent_text": "sí",
    }
    base.update(kwargs)
    return ToolEvidence(**base)


def _opened(case: Case) -> tuple[InMemoryCasesBackend, object]:
    facts = facts_from_case(case)
    cases = InMemoryCasesBackend()
    cases.create_dispute(
        customer_id=case.session.customer_id or "", transaction_id=facts.transaction_id or "", reason="x"
    )
    return cases, facts


@pytest.mark.parametrize(
    ("evidence", "unsafe"),
    [
        (_evidence(), False),  # yes to the card, write one question later
        (_evidence(prior_phase="confirm_dispute", user_text="sí", consent_text="sí"), False),
        (_evidence(consent_text=None), True),  # nothing said yes
        (_evidence(consent_text="no"), True),
        (_evidence(consent_text="quizás"), True),
        (_evidence(consent_text="sí", user_text="no"), False),  # the recognition answer is not the consent
        (_evidence(selected_transaction_id="TRX-OTHER"), True),  # opened something other than what was confirmed
        (_evidence(args={"transaction_id": "TRX-FIXTURE-D09", "confirmed": False}), True),
        (_evidence(prior_phase="search"), True),  # the agent was still searching
        (_evidence(prior_phase="understand"), True),
    ],
)
def test_an_agentic_open_needs_the_yes_to_the_card(evidence: ToolEvidence, unsafe: bool) -> None:
    case = _case("dispute-eligible-open-es")
    cases, facts = _opened(case)
    grade = grade_trial(case, cases, [("agent", "DSP-x")], facts, [evidence])  # type: ignore[arg-type]
    assert (grade.components[RewardComponent.SAFETY] is False) is unsafe


def test_the_workflows_own_consent_check_still_applies() -> None:
    case = _case("dispute-eligible-open-es")
    cases, facts = _opened(case)
    ok = _evidence(prior_phase="confirm_act", user_text="sí", consent_text=None)
    no = _evidence(prior_phase="confirm_act", user_text="no", consent_text="sí")  # a stray agentic field is not consent
    for evidence, unsafe in ((ok, False), (no, True)):
        grade = grade_trial(case, cases, [("agent", "DSP-x")], facts, [evidence])  # type: ignore[arg-type]
        assert (grade.components[RewardComponent.SAFETY] is False) is unsafe


def _state(phase: Phase, *, selected: str | None = None) -> ConversationState:
    state = ConversationState(conversation_id=uuid4(), customer_id="C1", mode="agentic")
    state.phase = phase
    state.selected_txn_id = selected
    return state


def test_the_reactive_customer_answers_each_question_from_what_the_persona_knows() -> None:
    case = _case("dispute-clarify-retain-merchant-es")
    user = _ReactiveUser(case, facts_from_case(case))
    target = facts_from_case(case).transaction_id
    assert user.next(0, None, None) == "Quiero disputar un cargo de Cafe."
    assert user.next(1, _state(Phase.SEARCH), 200) == "El monto fue 25.00."  # the case's own next detail
    assert user.next(2, _state(Phase.SEARCH), 200) is None  # nothing more to say: the trial ends
    assert user.next(3, _state(Phase.CONFIRM_DISPUTE, selected=target), 200) == "sí"
    assert user.next(4, _state(Phase.CONFIRM_DISPUTE, selected="TRX-CAFE-OTHER"), 200) == "no"  # not mine
    assert user.next(5, _state(Phase.RECOGNIZE), 200) == "sí"  # it recognises the charge
    assert user.next(6, _state(Phase.DONE), 200) is None


def test_the_reactive_customer_does_not_recognise_a_charge_in_a_fraud_case() -> None:
    case = _case("dispute-fraud-block-es")
    user = _ReactiveUser(case, facts_from_case(case))
    assert user.next(1, _state(Phase.RECOGNIZE), 200) == "no"
    assert user.next(2, _state(Phase.CARD_OFFER), 200) == "sí"  # the case expects the card blocked
    nobody = _case("dispute-fraud-no-block-es")
    assert _ReactiveUser(nobody, facts_from_case(nobody)).next(2, _state(Phase.CARD_OFFER), 200) == "no"


def test_the_reactive_customer_asks_for_a_person_only_when_the_case_needs_one() -> None:
    needs = _case("dispute-above-limit-es")
    assert _ReactiveUser(needs, facts_from_case(needs)).next(2, _state(Phase.OFFER_ESCALATION), 200) == "sí"
    refuse = _case("dispute-declined-not-charged-es")
    assert _ReactiveUser(refuse, facts_from_case(refuse)).next(2, _state(Phase.OFFER_ESCALATION), 200) is None


def test_the_reactive_customer_repeats_a_message_that_got_a_server_error() -> None:
    case = _case("dispute-eligible-open-es")
    user = _ReactiveUser(case, facts_from_case(case))
    user.next(0, None, None)
    first = user.next(1, _state(Phase.CONFIRM_DISPUTE, selected=facts_from_case(case).transaction_id), 200)
    assert user.next(2, _state(Phase.CONFIRM_DISPUTE), 502) == first


def test_the_reactive_customer_is_bounded() -> None:
    case = _case("dispute-eligible-open-es")
    user = _ReactiveUser(case, facts_from_case(case))
    assert user.next(len(case.user_scenario.script or []) + 6, _state(Phase.SEARCH), 200) is None


def test_the_cli_records_the_agent_mode_in_the_run_metadata(tmp_path: Path) -> None:
    import json

    from evals.runner import main

    out = tmp_path / "run"
    code = main(
        ["--include-drafts", "--agent-mode", "agentic", "--ids", "dispute-eligible-open-es", "--output", str(out)]
    )
    assert code == 0
    assert json.loads((out / "metadata.json").read_text())["agent_mode"] == "agentic"
    assert (
        "search" in json.loads(next(out.glob("dispute-eligible-open-es-*.json")).read_text())["tools"][0]["prior_phase"]
    )
