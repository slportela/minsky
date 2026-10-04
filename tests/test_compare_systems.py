"""The business comparison: metric definitions, and the baseline behaves like today's process."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from evals.compare_systems import Row, compare, summarize
from evals.runner import run_trial
from evals.schema import load_cases


def _row(
    expected: str, *, handoff: bool, passed: bool = True, safe: bool | None = True, error: str | None = None
) -> Row:
    return Row("c", "es", expected, expected, passed, passed, safe, handoff, error)


def test_summary_counts_unnecessary_and_missed_handoffs():
    rows = [
        _row("resolve", handoff=True),  # unnecessary
        _row("resolve", handoff=False),
        _row("escalate", handoff=False),  # missed
        _row("refuse", handoff=True),
    ]
    summary = summarize(rows)
    assert summary["handled_without_agent"]["count"] == 2
    assert summary["unnecessary_handoffs"]["count"] == 1 and summary["unnecessary_handoffs"]["n"] == 2
    assert summary["missed_handoffs"]["count"] == 1 and summary["missed_handoffs"]["n"] == 2
    assert summary["agent_intake_minutes"] == pytest.approx(2 * 7.2)
    assert summary["unsafe_outcomes"]["zero_event_upper95"] is not None


def test_test_split_is_refused():
    with pytest.raises(SystemExit):
        asyncio.run(compare("test"))


def test_runner_refuses_val_unless_asked():
    case = next(c for c in load_cases(Path("evals/cases")) if c.split.value == "val")
    record = asyncio.run(run_trial(case))
    assert record.status == "error"


def test_errored_trials_leave_the_denominators():
    rows = [_row("resolve", handoff=False), _row("escalate", handoff=False, safe=None, error="ValueError")]
    summary = summarize(rows)
    assert summary["errors"] == 1 and summary["cases"] == 1
    assert summary["handled_without_agent"]["n"] == 1
    assert summary["missed_handoffs"]["n"] == 0
