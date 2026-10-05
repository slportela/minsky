"""Scripted dev smoke: policy-labeled cases through POST /api/chat/turn, and graders that fail a null reply."""

from decimal import Decimal
from pathlib import Path
from typing import ClassVar
from uuid import uuid4

import pytest
import yaml
from sqlmodel import select

from evals.budget import SpendBudget
from evals.evidence import ToolEvidence
from evals.graders import grade_trial
from evals.runner import _ReactiveUser, _select, run_case, run_trial
from evals.schema import Case, RewardComponent, load_case, load_cases
from evals.world import build_bank, check_label, facts_from_case
from minsky_api.agent.state import ConversationState, Phase
from minsky_api.store.cases_memory import InMemoryCasesBackend
from minsky_api.store.models import Transaction

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
    "dispute-offer-answered-with-the-charge-es",
    "dispute-follow-up-after-case-es",
    "model-outage-maintenance-es",
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


def test_the_world_records_a_transaction_type_like_the_bank_does() -> None:
    """Live run 1: rows without a type made every query that filtered by it find nothing (an eval bug)."""
    for case_id, kind in (("dispute-eligible-open-es", "Purchase"), ("dispute-above-limit-es", "Transfer")):
        case = _case(case_id)
        bank = build_bank(case, facts_from_case(case))
        try:
            types = {row.transaction_type for row in bank.session.exec(select(Transaction)).all()}
        finally:
            bank.close()
        assert None not in types and kind in types, (case_id, types)


# ---------------------------------------------------------------- flexible-matching cases (need a model)

FLEX = sorted(path.stem for path in (CASES / "dev").glob("dispute-flex-*.yaml"))
FLEX_RESOLVE = [case_id for case_id in FLEX if case_id != "dispute-flex-nothing-near-es"]


def test_the_flexible_cases_exist_and_cover_each_rule_and_a_mirror() -> None:
    assert FLEX == [
        "dispute-flex-approx-amount-wrong-day-es",
        "dispute-flex-merchant-misspelled-es",
        "dispute-flex-near-amount-es",
        "dispute-flex-near-amount-pt",
        "dispute-flex-no-merchant-withdrawal-es",
        "dispute-flex-nothing-near-es",
        "dispute-flex-two-same-day-es",
        "dispute-flex-usd-for-cop-es",
    ]
    mirror = _case("dispute-flex-nothing-near-es")
    assert mirror.evaluation_criteria.expected_outcome == "clarify"
    assert facts_from_case(mirror).label_source == "no_match"


def test_a_scripted_stand_in_does_not_run_the_cases_that_test_a_model() -> None:
    offline = {case.id for case in _select(load_cases(CASES), include_drafts=True, ids=None)}
    assert offline == set(SCRIPTED)  # the scripted smokes (and make ci) are untouched
    live = {case.id for case in _select(load_cases(CASES), include_drafts=True, ids=None, extractor="real")}
    assert set(FLEX) <= live and set(SCRIPTED) <= live


def _row(bank, transaction_id: str):  # type: ignore[no-untyped-def]
    return bank.session.get(Transaction, transaction_id)


@pytest.mark.parametrize("case_id", FLEX)
def test_each_flexible_world_holds_the_target_and_its_look_alikes(case_id: str) -> None:
    case = _case(case_id)
    facts = facts_from_case(case)
    bank = build_bank(case, facts)
    try:
        rows = {row.transaction_id: row for row in bank.session.exec(select(Transaction)).all()}
    finally:
        bank.close()
    assert facts.transaction_id in rows
    assert len(rows) >= 2 or case_id == "dispute-flex-nothing-near-es"
    assert all(row.customer_id == case.session.customer_id for row in rows.values())
    assert all(row.transaction_type for row in rows.values())  # the bank always records a type


def _day(row: Transaction) -> str:
    assert row.transaction_date is not None
    return row.transaction_date.date().isoformat()


def test_the_worlds_hold_what_each_case_is_about() -> None:
    def world(case_id: str) -> dict[str, Transaction]:
        case = _case(case_id)
        bank = build_bank(case, facts_from_case(case))
        try:
            return {r.transaction_id: r for r in bank.session.exec(select(Transaction)).all()}
        finally:
            bank.close()

    near = world("dispute-flex-near-amount-es")
    assert (
        Decimal(str(near["TRX-FLEX-NEAR"].amount_usd)) == Decimal("123.10")
        and near["TRX-FLEX-NEAR-OTHER"].merchant_name == "Café Sur"
    )

    cop = world("dispute-flex-usd-for-cop-es")["TRX-FLEX-COP"]
    assert cop.currency == "COP"
    assert (Decimal(str(cop.amount)), Decimal(str(cop.amount_usd))) == (Decimal("450000"), Decimal("112.50"))
    assert world("dispute-flex-usd-for-cop-es")["TRX-FLEX-COP-OTHER"].currency == "USD"

    day = world("dispute-flex-approx-amount-wrong-day-es")
    assert _day(day["TRX-FLEX-DAY"]) == "2026-06-18"  # today, not yesterday
    assert _day(day["TRX-FLEX-DAY-OTHER"]) == "2026-06-17"

    atm = world("dispute-flex-no-merchant-withdrawal-es")
    assert atm["TRX-FLEX-ATM"].merchant_name is None and atm["TRX-FLEX-ATM"].transaction_type == "Withdrawal"
    assert atm["TRX-FLEX-ATM-OTHER"].transaction_type == "Purchase"
    assert atm["TRX-FLEX-ATM"].amount_usd == atm["TRX-FLEX-ATM-OTHER"].amount_usd

    dates = world("dispute-flex-two-same-day-es")
    assert {_day(r) for r in dates.values()} == {"2026-06-17"}

    assert world("dispute-flex-merchant-misspelled-es")["TRX-FLEX-NAME"].merchant_name == "Starbucks"


def _oracle(items: list, facts):  # type: ignore[no-untyped-def]
    """The reference solution: it knows the transaction. A case the oracle cannot pass is a broken case."""
    from evals import runner

    last_user = max((i for i, item in enumerate(items) if item.get("role") == "user"), default=-1)
    answered = any(item.get("type") == "function_call_output" for item in items[last_user + 1 :])
    if facts.label_source == "no_match":
        return runner._text_step("No encontré nada parecido. ¿Puedes decirme el comercio o la fecha?")
    if not answered:
        return runner._tool_step(
            "query_transactions",
            sql=f"SELECT transaction_id FROM transactions WHERE transaction_id = '{facts.transaction_id}'",
        )
    return runner._tool_step("propose_transaction", transaction_id=facts.transaction_id, customer_says_not_me=False)


@pytest.mark.parametrize("case_id", FLEX)
async def test_the_oracle_passes_every_flexible_case(case_id: str) -> None:
    from unittest.mock import patch

    from evals import runner

    with patch.object(runner, "_scripted_agent_step", _oracle):
        record = await run_trial(_case(case_id), agent_mode="agentic")
    assert record.status == "passed", (record.grade, record.error_class)


@pytest.mark.parametrize("case_id", FLEX_RESOLVE)
async def test_an_agent_that_opens_nothing_fails_every_case_that_must_open(case_id: str) -> None:
    from unittest.mock import patch

    from evals import runner

    with patch.object(
        runner, "_scripted_agent_step", lambda items, facts: runner._text_step("Cuéntame más, por favor.")
    ):
        record = await run_trial(_case(case_id), agent_mode="agentic")
    assert record.status == "failed"


async def test_an_agent_that_proposes_the_look_alike_never_gets_it_opened() -> None:
    from unittest.mock import patch

    from evals import runner

    def look_alike(items: list, facts):  # type: ignore[no-untyped-def]
        last_user = max((i for i, item in enumerate(items) if item.get("role") == "user"), default=-1)
        if not any(item.get("type") == "function_call_output" for item in items[last_user + 1 :]):
            return runner._tool_step("query_transactions", sql="SELECT transaction_id FROM transactions")
        return runner._tool_step(
            "propose_transaction", transaction_id="TRX-FLEX-NEAR-OTHER", customer_says_not_me=False
        )

    with patch.object(runner, "_scripted_agent_step", look_alike):
        record = await run_trial(_case("dispute-flex-near-amount-es"), agent_mode="agentic")
    assert record.status == "failed"
    assert not any(t.tool == "open_dispute" for t in record.tools)  # the customer said no to the wrong one


async def test_an_agent_that_proposes_the_unrelated_charge_does_not_get_it_opened() -> None:
    from unittest.mock import patch

    from evals import runner

    def propose_it(items: list, facts):  # type: ignore[no-untyped-def]
        last_user = max((i for i, item in enumerate(items) if item.get("role") == "user"), default=-1)
        if not any(item.get("type") == "function_call_output" for item in items[last_user + 1 :]):
            return runner._tool_step("query_transactions", sql="SELECT transaction_id FROM transactions")
        return runner._tool_step("propose_transaction", transaction_id="TRX-FLEX-NONE", customer_says_not_me=False)

    with patch.object(runner, "_scripted_agent_step", propose_it):
        record = await run_trial(_case("dispute-flex-nothing-near-es"), agent_mode="agentic")
    assert not any(t.tool == "open_dispute" for t in record.tools)  # nothing is near: the customer says no


@pytest.mark.parametrize("case_id", FLEX_RESOLVE)
async def test_the_workflows_exact_search_cannot_pass_these_cases_with_scripted_understanding(case_id: str) -> None:
    """The cases separate the two designs: exact amount, date and merchant find none of them (offline, scripted)."""
    record = await run_trial(_case(case_id))
    assert record.status == "failed"


def test_a_no_match_world_must_expect_clarify() -> None:
    data = yaml.safe_load((CASES / "dev" / "dispute-flex-nothing-near-es.yaml").read_text())
    data["evaluation_criteria"]["expected_outcome"] = "resolve"
    data["evaluation_criteria"]["reward_basis"] = ["outcome", "env", "communicate", "safety"]
    data["evaluation_criteria"]["communicate_info"] = ["DSP-"]
    case = Case.model_validate(data)
    with pytest.raises(ValueError, match="no matching transaction must expect clarify"):
        check_label(case, facts_from_case(case))


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
    """`python -m evals.runner --extractor real` crashed on model-outage-maintenance-es, the last case: the trial closes
    the client of its llm, and the fault wrapper around it had none. The exception came out of a `finally`, so it
    ended the whole run and lost the summary and artifacts of every trial before it."""
    _ProviderLLM.instances = []
    monkeypatch.setattr("evals.runner.LLM", _ProviderLLM)
    record = await run_trial(_case("model-outage-maintenance-es"), extractor="real", budget=_BUDGET)
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


async def test_the_agentic_real_run_survives_the_outage_case_and_closes_its_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Same crash, agentic mode: the first model call is `step`, and the fault wrapper must fault it too."""
    _ProviderLLM.instances = []
    monkeypatch.setattr("evals.runner.LLM", _ProviderLLM)
    record = await run_trial(
        _case("model-outage-maintenance-es"), extractor="real", budget=_BUDGET, agent_mode="agentic"
    )
    assert record.status == "passed", (record.status, record.error_class, record.grade)
    assert len(_ProviderLLM.instances) == 1 and _ProviderLLM.instances[0].client.closed is True
    replies = [text for role, text in record.messages if role == "agent"]
    assert any("mantenimiento" in text for text in replies) and not any("HO-" in text for text in replies)  # no case


# ---------------------------------------------------------------- --workers


def _run_main(tmp_path: Path, name: str, *extra: str) -> Path:
    from evals.runner import main

    out = tmp_path / name
    ids = "dispute-eligible-open-es,dispute-fraud-block-es,dispute-above-limit-pt"
    assert (
        main(
            ["--include-drafts", "--agent-mode", "agentic", "--trials", "2", "--ids", ids, "--output", str(out), *extra]
        )
        == 0
    )
    return out


def _statuses(folder: Path) -> dict[str, str]:
    import json

    return {p.name: json.loads(p.read_text())["status"] for p in sorted(folder.glob("*-*.json"))}


def test_workers_run_the_same_trials_as_a_sequential_run(tmp_path: Path) -> None:
    import json

    sequential = _run_main(tmp_path, "one", "--workers", "1")
    parallel = _run_main(tmp_path, "three", "--workers", "3")
    assert len(_statuses(parallel)) == 6 and _statuses(parallel) == _statuses(sequential)
    assert json.loads((parallel / "metadata.json").read_text())["workers"] == 3
    assert json.loads((sequential / "metadata.json").read_text())["workers"] == 1
    assert json.loads((parallel / "summary.json").read_text())["attempted"] == 6
    lines = (parallel / "results.jsonl").read_text().splitlines()
    assert len(lines) == 6 and not (parallel / "errors.jsonl").exists()  # every trial in one run folder


def test_a_run_made_by_workers_can_be_compared_with_a_sequential_one(tmp_path: Path) -> None:
    from evals.compare import main as compare

    sequential = _run_main(tmp_path, "one", "--workers", "1")
    parallel = _run_main(tmp_path, "two", "--workers", "2")
    assert compare([str(sequential), str(parallel), "--output", str(tmp_path / "delta.json")]) == 0


def test_more_workers_than_trials_is_fine(tmp_path: Path) -> None:
    out = _run_main(tmp_path, "many", "--workers", "16")
    assert len(_statuses(out)) == 6


@pytest.mark.parametrize("workers", ["0", "17", "-1"])
def test_workers_must_be_between_1_and_16(workers: str) -> None:
    from evals.runner import main

    with pytest.raises(SystemExit):
        main(["--include-drafts", "--ids", "dispute-eligible-open-es", "--workers", workers])


def test_a_worker_trial_returns_a_record_as_plain_json() -> None:
    from evals.evidence import TrialRecord
    from evals.runner import _trial_in_worker

    case = _case("dispute-eligible-open-es")
    options = {
        "extractor": "scripted",
        "timeout_s": 120,
        "database": "sqlite",
        "legacy_auth_baseline": False,
        "agent_mode": "agentic",
    }
    record = TrialRecord.model_validate_json(_trial_in_worker(case.model_dump_json(), options))
    assert record.case_id == case.id and record.status == "passed"


def test_with_workers_a_real_run_shares_one_cap_between_them(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from evals import runner

    made: list[object] = []

    class Spy(runner.SharedSpendBudget):
        def __init__(self, *args: object, **kwargs: object) -> None:
            super().__init__(*args, **kwargs)  # type: ignore[arg-type]
            made.append(self)

    monkeypatch.setattr(runner, "SharedSpendBudget", Spy)
    argv = [
        "--include-drafts",
        "--extractor",
        "real",
        "--estimate-only",
        "--max-cost-usd",
        "10",
        "--input-usd-per-million",
        "0.1",
        "--output-usd-per-million",
        "0.5",
        "--ids",
        "dispute-eligible-open-es,dispute-fraud-block-es",
    ]
    assert runner.main([*argv, "--workers", "2"]) == 0
    assert len(made) == 1 and "Conservative estimate" in capsys.readouterr().out
    made.clear()
    assert runner.main([*argv, "--workers", "1"]) == 0
    assert made == []  # one worker keeps the plain in-process budget


def test_a_case_whose_customer_never_answers_yes_or_no_must_expect_escalate() -> None:
    data = yaml.safe_load((CASES / "dev" / "dispute-unclear-replies-handoff-es.yaml").read_text())
    data["evaluation_criteria"]["expected_outcome"] = "resolve"
    case = Case.model_validate(data)
    with pytest.raises(ValueError, match="never answers yes or no must expect escalate"):
        check_label(case, facts_from_case(case))
