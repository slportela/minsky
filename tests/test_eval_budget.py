"""Paid diagnostics must stop before dispatch when their allowance is exhausted."""

from decimal import Decimal

import pytest

from evals.budget import BudgetExceeded, SpendBudget
from evals.evidence import TrialRecord
from evals.runner import RecordedLLM


def test_rejected_reservation_does_not_change_allowance():
    budget = SpendBudget(Decimal("0.000001"), Decimal(1), Decimal(1))
    with pytest.raises(BudgetExceeded):
        budget.reserve("instructions", [{"role": "user", "content": "hello"}], 256)
    assert budget.reserved_usd == 0


@pytest.mark.parametrize("value", ["0", "-1", "NaN", "Infinity"])
def test_invalid_budget_rejected(value):
    with pytest.raises(ValueError):
        SpendBudget(Decimal(value), Decimal(1), Decimal(1))


async def test_exhausted_budget_never_calls_provider():
    class Provider:
        async def respond(self, *args, **kwargs):
            pytest.fail("provider must not be called")

    budget = SpendBudget(Decimal("0.000001"), Decimal(1), Decimal(1))
    llm = RecordedLLM(Provider(), TrialRecord(case_id="negative"), budget)
    with pytest.raises(BudgetExceeded):
        await llm.respond("instructions", [{"role": "user", "content": "hello"}])


async def test_provider_error_keeps_reservation_and_error_evidence():
    class Provider:
        async def respond(self, *args, **kwargs):
            raise TimeoutError("sensitive provider detail")

    budget = SpendBudget(Decimal(1), Decimal(1), Decimal(1))
    record = TrialRecord(case_id="negative")
    with pytest.raises(TimeoutError):
        await RecordedLLM(Provider(), record, budget).respond("instructions", [{"role": "user", "content": "hello"}])
    assert budget.reserved_usd > 0
    assert record.model_calls[0]["error_class"] == "TimeoutError"
    assert "sensitive" not in record.model_dump_json()


def test_unexpected_usage_stops_further_calls():
    budget = SpendBudget(Decimal(1), Decimal(1), Decimal(1))
    allowance = budget.reserve("hi", [], 1)
    with pytest.raises(BudgetExceeded):
        budget.account(2_000_000, 0, allowance)
    with pytest.raises(BudgetExceeded):
        budget.reserve("hi", [], 1)


def test_compare_rejects_unmatched_workloads(tmp_path):
    import json

    from evals.compare import main

    for name, model in (("before", "one"), ("after", "two")):
        run = tmp_path / name
        run.mkdir()
        (run / "metadata.json").write_text(json.dumps({"cases": {}, "model": model}))
    with pytest.raises(SystemExit):
        main([str(tmp_path / "before"), str(tmp_path / "after"), "--output", str(tmp_path / "delta.json")])
    assert not (tmp_path / "delta.json").exists()


def test_live_cli_requires_budget_before_dispatch():
    from evals.runner import main

    with pytest.raises(SystemExit):
        main(["--extractor", "real", "--include-drafts"])


async def test_trial_timeout_preserves_failing_request(monkeypatch):
    import asyncio
    from pathlib import Path

    from evals.runner import ScriptedLLM, run_trial
    from evals.schema import load_case

    async def slow(self, *args, **kwargs):
        await asyncio.sleep(1)

    monkeypatch.setattr(ScriptedLLM, "respond", slow)
    case = load_case(Path("evals/cases/dev/dispute-eligible-open-es.yaml"))
    record = await run_trial(case, timeout_s=0.01)
    assert record.status == "error"
    assert record.error_class == "TimeoutError"
    assert len(record.requests) == 1
    assert record.model_calls[0]["error_class"] == "CancelledError"


def test_browser_lifespan_removes_private_credentials(tmp_path, monkeypatch):
    import asyncio
    from pathlib import Path

    from evals import browser_smoke
    from evals.schema import load_case
    from minsky_api.api import chat
    from minsky_api.config import get_settings

    async def bound(_cases):
        return [load_case(Path("evals/cases/dev/dispute-eligible-open-es.yaml"))]

    output = tmp_path / "browser"
    private = output / "test-credentials.json"

    def serve(app, **kwargs):
        async def lifecycle():
            async with app.router.lifespan_context(app):
                assert private.exists()
                assert private.stat().st_mode & 0o777 == 0o600
            assert not private.exists()

        asyncio.run(lifecycle())

    monkeypatch.setenv("MINSKY_TEST_SESSIONS", "")
    monkeypatch.setattr(chat, "LLM", chat.LLM)
    monkeypatch.setattr(browser_smoke, "bind_gold_cases", bound)
    monkeypatch.setattr(browser_smoke.uvicorn, "run", serve)
    assert browser_smoke.main(["--output", str(output)]) == 0
    get_settings.cache_clear()
