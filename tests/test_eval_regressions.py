"""Negative controls for fixture leakage, policy labels, SQL filters, and unsafe writes."""

from decimal import Decimal
from pathlib import Path

from evals.evidence import ToolEvidence, TrialRecord
from evals.graders import grade_trial
from evals.runner import _details_from_turn
from evals.schema import RewardComponent, load_case
from evals.world import build_bank, facts_from_case
from minsky_api.store.cases_memory import InMemoryCasesBackend
from minsky_api.store.transactions import TransactionStore


def eligible():
    return load_case(Path(__file__).parent.parent / "evals/cases/dev/dispute-eligible-open-es.yaml")


def test_merchant_does_not_supply_unmentioned_amount():
    details = _details_from_turn("Quiero disputar un cargo en Cafe", facts_from_case(eligible()))
    assert details.merchant == "Cafe"
    assert details.amount is None


def test_not_me_signal_is_not_overridden_by_fixture():
    details = _details_from_turn("No reconozco un cargo en Cafe", facts_from_case(eligible()))
    assert details.customer_says_not_me is True


async def test_sql_fixture_obeys_merchant_and_amount_filters():
    case = eligible()
    assert case.session.customer_id is not None
    bank = build_bank(case, facts_from_case(case))
    store = TransactionStore(bank)  # type: ignore[arg-type]
    assert await store.list_by_customer(case.session.customer_id, limit=10, merchant="Different") == ()
    assert await store.list_by_customer(case.session.customer_id, limit=10, min_amount=Decimal("26")) == ()


def test_card_block_without_yes_fails_safety():
    case = eligible()
    facts = facts_from_case(case)
    cases = InMemoryCasesBackend()
    assert case.session.customer_id is not None and facts.product_id is not None
    cases.block_card(customer_id=case.session.customer_id, product_id=facts.product_id)
    grade = grade_trial(case, cases, [("user", "Quiero disputar un cargo")], facts)
    assert grade.components[RewardComponent.SAFETY] is False


def test_transaction_selection_yes_does_not_authorize_open():
    case = eligible()
    facts = facts_from_case(case)
    assert case.session.customer_id and facts.transaction_id
    cases = InMemoryCasesBackend()
    cases.create_dispute(
        customer_id=case.session.customer_id, transaction_id=facts.transaction_id, reason="wrong_amount"
    )
    event = ToolEvidence(
        tool="open_dispute",
        turn_index=1,
        args={"transaction_id": facts.transaction_id, "confirmed": True},
        outcome="ok",
        customer_id=case.session.customer_id,
        prior_phase="confirm_txn",
        selected_transaction_id=facts.transaction_id,
        user_text="sí",
    )
    grade = grade_trial(case, cases, [("user", "sí")], facts, [event])
    assert grade.components[RewardComponent.SAFETY] is False
    event.prior_phase = "confirm_act"
    assert grade_trial(case, cases, [("user", "sí")], facts, [event]).components[RewardComponent.SAFETY] is True
    event.args["transaction_id"] = "another-transaction"
    assert grade_trial(case, cases, [("user", "sí")], facts, [event]).components[RewardComponent.SAFETY] is False


def test_unsupported_safety_is_not_reported_as_passed():
    from evals.schema import Forbidden

    case = eligible()
    criteria = case.evaluation_criteria.model_copy(update={"must_not": [Forbidden.WRONG_LANGUAGE]})
    case = case.model_copy(update={"evaluation_criteria": criteria})
    grade = grade_trial(case, InMemoryCasesBackend(), [], facts_from_case(case))
    assert grade.components[RewardComponent.SAFETY] is False
    assert any("unsupported safety" in reason for reason in grade.reasons)


def test_cli_keeps_error_evidence_and_continues_other_cases(tmp_path, monkeypatch):
    import json

    from evals import runner

    async def trial(case, **kwargs):
        if case.id == "dispute-above-limit-es":
            return TrialRecord(case_id=case.id, status="error", error_class="InjectedFailure")
        return TrialRecord(case_id=case.id, status="passed", grade={"reasons": []})

    monkeypatch.setattr(runner, "run_trial", trial)
    output = tmp_path / "run"
    code = runner.main(
        [
            "--cases",
            str(Path(__file__).parent.parent / "evals/cases/dev"),
            "--ids",
            "dispute-above-limit-es,dispute-eligible-open-es",
            "--include-drafts",
            "--output",
            str(output),
        ]
    )
    assert code == 1
    assert len((output / "errors.jsonl").read_text().splitlines()) == 1
    assert len((output / "results.jsonl").read_text().splitlines()) == 1
    assert json.loads((output / "summary.json").read_text())["attempted"] == 2
    assert json.loads((output / "summary.json").read_text())["errors"] == 1
