"""Today's process vs Minsky on the same cases, stated as business results.

    uv run python -m evals.compare_systems --split val --report evals/reports/<date>-system-comparison

Both systems run behind the real chat route, tools, policy and graders. "always_escalate" sends every
contact to an agent (today); "minsky" is the orchestrator. Extraction is scripted (offline): this
measures the decisions, the handoffs and the safety checks on the case workload, not the model's
language understanding. Agent minutes use the measured median length of a complaint call.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import patch

from evals.baselines import always_escalate
from evals.evidence import TrialRecord
from evals.metrics import wilson_interval, zero_event_upper_bound
from evals.runner import run_trial
from evals.schema import Case, Split, Status, load_cases
from minsky_api.agent import orchestrator

# Median complaint-call duration, call_center_interactions (silver), 117,021 calls, 2023-06 to 2026-06.
AGENT_MINUTES_PER_INTAKE = 7.2
AGENT_NEEDED = {"escalate", "refuse"}  # refuse also offers a person (D05)

SYSTEMS: dict[str, Callable[..., Awaitable[Any]] | None] = {"always_escalate": always_escalate, "minsky": None}


@dataclass
class Row:
    case_id: str
    language: str
    expected: str
    observed: str | None
    passed: bool
    outcome_ok: bool
    safe: bool | None  # None when the trial errored before grading
    handoff: bool
    error: str | None


async def _run(case: Case, system: str) -> Row:
    replacement = SYSTEMS[system]
    if replacement is None:
        record: TrialRecord = await run_trial(case, allow_val=True)
    else:
        with patch.object(orchestrator, "run_turn", replacement):
            record = await run_trial(case, allow_val=True)
    grade = record.grade or {}
    components = grade.get("components") or {}
    observed = grade.get("observed_outcome")
    return Row(
        case_id=case.id,
        language=case.tags.language.value,
        expected=case.evaluation_criteria.expected_outcome.value,
        observed=str(observed.value if hasattr(observed, "value") else observed) if observed else None,
        passed=record.status == "passed",
        outcome_ok=bool(components.get("outcome")) or bool(components.get("RewardComponent.OUTCOME")),
        # Unsafe only when the safety grader ran and failed; an errored trial has no safety verdict.
        safe=None if record.error_class else components.get("safety") is not False,
        handoff=any(t.tool == "create_handoff" and t.outcome == "ok" for t in record.tools),
        error=record.error_class,
    )


def _rate(numerator: int, n: int) -> dict[str, Any]:
    interval = wilson_interval(numerator, n)
    return {"n": n, "count": numerator, "rate": numerator / n if n else None, "wilson95": interval}


def summarize(all_rows: list[Row]) -> dict[str, Any]:
    # Errored trials have no verdict: they leave every denominator and are reported on their own row.
    rows = [r for r in all_rows if not r.error]
    n = len(rows)
    needed = [r for r in rows if r.expected in AGENT_NEEDED]
    not_needed = [r for r in rows if r.expected not in AGENT_NEEDED]
    unsafe = sum(1 for r in rows if not r.safe)
    handoffs = sum(1 for r in rows if r.handoff)
    return {
        "cases": n,
        "errors": len(all_rows) - n,
        "fully_correct": _rate(sum(r.passed for r in rows), n),
        "correct_outcome": _rate(sum(r.outcome_ok for r in rows), n),
        "handled_without_agent": _rate(n - handoffs, n),
        "unnecessary_handoffs": _rate(sum(r.handoff for r in not_needed), len(not_needed)),
        "missed_handoffs": _rate(sum(not r.handoff for r in needed), len(needed)),
        "unsafe_outcomes": {
            **_rate(unsafe, n),
            "zero_event_upper95": zero_event_upper_bound(n) if unsafe == 0 else None,
        },
        "agent_intake_minutes": round(handoffs * AGENT_MINUTES_PER_INTAKE, 1),
        "by_language": {
            lang: _rate(sum(r.passed for r in rows if r.language == lang), sum(1 for r in rows if r.language == lang))
            for lang in sorted({r.language for r in rows})
        },
    }


def _pct(cell: dict[str, Any]) -> str:
    if not cell["n"]:
        return "not defined (n=0)"
    low, high = cell["wilson95"]
    return f"{cell['count']}/{cell['n']} = {100 * cell['rate']:.0f}% [{100 * low:.0f}–{100 * high:.0f}]"


def render(results: dict[str, dict[str, Any]], split: str, meta: dict[str, Any]) -> str:
    base, ours = results["always_escalate"], results["minsky"]
    lines = [
        f"# Today's process vs Minsky ({split} split, offline)",
        "",
        f"Run {meta['run_at']} · commit {meta['commit']} · {base['cases']} cases × 1 trial · scripted extraction "
        "(offline: decisions, handoffs and safety, not language understanding). Intervals are Wilson 95 %.",
        "",
        "| Metric | Always send to an agent (today) | Minsky |",
        "|---|---|---|",
        f"| Handled without an agent | {_pct(base['handled_without_agent'])} | {_pct(ours['handled_without_agent'])} |",
        f"| Correct outcome | {_pct(base['correct_outcome'])} | {_pct(ours['correct_outcome'])} |",
        f"| Fully correct (outcome, records, reference, handoff content, safety) | {_pct(base['fully_correct'])} | "
        f"{_pct(ours['fully_correct'])} |",
        f"| Unnecessary handoffs (case did not need a person) | {_pct(base['unnecessary_handoffs'])} | "
        f"{_pct(ours['unnecessary_handoffs'])} |",
        f"| Missed handoffs (case needed a person) | {_pct(base['missed_handoffs'])} | "
        f"{_pct(ours['missed_handoffs'])} |",
        f"| Unsafe outcomes | {_pct(base['unsafe_outcomes'])} | {_pct(ours['unsafe_outcomes'])} |",
        f"| Agent intake minutes (handoffs × {AGENT_MINUTES_PER_INTAKE} min) | {base['agent_intake_minutes']} | "
        f"{ours['agent_intake_minutes']} |",
        f"| Trials that errored (excluded above) | {base['errors']} | {ours['errors']} |",
        "",
        "By language (fully correct): "
        + ", ".join(f"{lang} {_pct(cell)}" for lang, cell in ours["by_language"].items())
        + ".",
    ]
    if ours["unsafe_outcomes"]["count"] == 0 and ours["unsafe_outcomes"].get("zero_event_upper95") is not None:
        lines.append(
            f"No unsafe outcome was observed for Minsky; with n={ours['cases']} the true rate could still be up to "
            f"{100 * ours['unsafe_outcomes']['zero_event_upper95']:.0f}% (one-sided 95 %)."
        )
    lines += [
        "",
        "How to read it:",
        "- The baseline is correct on every case that needs a person and wrong on every case that does not: "
        "that gap is the intake work Minsky removes.",
        "- Agent minutes count intake only; investigating an opened dispute is back-office work in both systems.",
        f"- Agent minutes use the measured median complaint call ({AGENT_MINUTES_PER_INTAKE} min); the case mix is "
        "the eval workload (one case per scenario), not the bank's real mix of disputes.",
        "- Offline and scripted: a live run with the production model is needed before quoting these as "
        "system performance (docs/evals.md).",
        "- val is the selection split: failures found on it are fixed, so these are not held-out test numbers. "
        "The locked test split is still empty and must be written by people (AGENTS rule 4).",
        "- The labels come from the same policy code (policy.disputes.decide) that Minsky runs, and the outcome is "
        "read partly from the system's own state. Offline, with scripted understanding, this mostly checks that "
        "the system wires the policy, tools, handoffs and safety checks correctly on real records; a policy bug "
        "would not show here. Hand-labeled cases and a live run are what test the policy and the model.",
    ]
    return "\n".join(lines) + "\n"


async def compare(split: str) -> tuple[dict[str, dict[str, Any]], dict[str, list[Row]]]:
    cases = [
        c
        for c in load_cases(Path("evals/cases"))
        if c.split.value == split and (c.status == Status.ACTIVE or split == Split.DEV.value)
    ]
    if split == Split.TEST.value:
        raise SystemExit("the test split is locked")
    if not cases:
        raise SystemExit(f"no runnable cases in {split}")
    rows: dict[str, list[Row]] = {}
    for system in SYSTEMS:
        rows[system] = [await _run(case, system) for case in cases]
    return {system: summarize(r) for system, r in rows.items()}, rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compare today's process with Minsky on the same cases.")
    parser.add_argument("--split", choices=("dev", "val"), default="val")
    parser.add_argument("--report", type=Path, help="write <path>.md and <path>.json")
    args = parser.parse_args(argv)
    import subprocess

    commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip()
    dirty = bool(subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True).stdout.strip())
    meta = {
        "run_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "commit": commit + ("-dirty" if dirty else ""),
        "split": args.split,
    }
    results, rows = asyncio.run(compare(args.split))
    text = render(results, args.split, meta)
    print(text)
    errors = sum(result["errors"] for result in results.values())
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.with_suffix(".md").write_text(text, encoding="utf-8")
        payload = {"meta": meta, "results": results, "rows": {k: [vars(r) for r in v] for k, v in rows.items()}}
        args.report.with_suffix(".json").write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    if errors:
        print(f"{errors} trial(s) errored: fix them before quoting this report (e.g. run make gold)")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
