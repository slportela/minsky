"""Whole-set checks on the eval cases (tier 1 of the eval audit: cheap, exhaustive, programmatic).

Errors make the case set unusable (duplicate ids, test leakage). Warnings flag weak coverage
(one-sided sets, a language or outcome missing from a split). Run: `make eval-check`.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from evals.schema import Case, Language, Outcome, Split, Status, load_cases

LEAK_MIN_CHARS = 40  # shortest case fragment that counts as leaked into a prompt


@dataclass(frozen=True)
class Finding:
    level: str  # error | warning
    code: str
    message: str
    case_ids: tuple[str, ...] = field(default_factory=tuple)

    def __str__(self) -> str:
        ids = f" [{', '.join(self.case_ids)}]" if self.case_ids else ""
        return f"{self.level.upper():7} {self.code}: {self.message}{ids}"


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def check_cases(cases: list[Case], prompts_dir: Path | None = None) -> list[Finding]:
    findings: list[Finding] = []
    findings += _duplicate_ids(cases)
    findings += _duplicate_scenarios(cases)
    findings += _test_customer_leakage(cases)
    if prompts_dir is not None and prompts_dir.is_dir():
        findings += _prompt_leakage(cases, prompts_dir)
    findings += _coverage(cases)
    return findings


def _duplicate_ids(cases: list[Case]) -> list[Finding]:
    dup = [i for i, n in Counter(c.id for c in cases).items() if n > 1]
    return [Finding("error", "duplicate_id", "case id used more than once", tuple(dup))] if dup else []


def _duplicate_scenarios(cases: list[Case]) -> list[Finding]:
    by_text: dict[str, list[str]] = defaultdict(list)
    for c in cases:
        by_text[_norm(c.user_scenario.instructions)].append(c.id)
    return [Finding("warning", "duplicate_scenario", "identical simulator instructions", tuple(ids))
            for ids in by_text.values() if len(ids) > 1]


def _test_customer_leakage(cases: list[Case]) -> list[Finding]:
    """A customer used in test must not appear in dev/val (we tune on dev/val)."""
    test = {c.session.customer_id: c.id for c in cases if c.split == Split.TEST and c.session.customer_id}
    leaked = [c.id for c in cases
              if c.split != Split.TEST and c.session.customer_id in test]
    if not leaked:
        return []
    return [Finding("error", "test_customer_leakage",
                    "customer also used in the test split", tuple(leaked))]


def _prompt_leakage(cases: list[Case], prompts_dir: Path) -> list[Finding]:
    """Case text must never appear in prompts (e.g. few-shot copied from the golden set)."""
    corpus = _norm(" ".join(p.read_text(encoding="utf-8", errors="ignore")
                            for p in prompts_dir.rglob("*") if p.is_file()))
    leaked = []
    for c in cases:
        fragments = [c.user_scenario.instructions, *c.user_scenario.script,
                     *c.evaluation_criteria.communicate_info]
        if any(len(f) >= LEAK_MIN_CHARS and _norm(f) in corpus for f in fragments):
            leaked.append(c.id)
    return [Finding("error", "prompt_leakage", "case text found under prompts/", tuple(leaked))] if leaked else []


def _coverage(cases: list[Case]) -> list[Finding]:
    findings = []
    active = [c for c in cases if c.status == Status.ACTIVE]
    for split in Split:
        in_split = [c for c in active if c.split == split]
        if not in_split:
            continue
        outcomes = {c.evaluation_criteria.expected_outcome for c in in_split}
        missing = sorted(o.value for o in set(Outcome) - outcomes)
        if missing:
            findings.append(Finding("warning", "missing_outcome",
                                    f"{split}: no active case expects {', '.join(missing)}"))
        languages = {c.tags.language for c in in_split}
        for lang in (Language.ES, Language.PT):
            if lang not in languages:
                findings.append(Finding("warning", "missing_language", f"{split}: no active '{lang}' case"))
        by_intent: dict[str, set[Outcome]] = defaultdict(set)
        for c in in_split:
            by_intent[c.tags.intent].add(c.evaluation_criteria.expected_outcome)
        for intent, outs in sorted(by_intent.items()):
            if outs == {Outcome.ESCALATE} or Outcome.ESCALATE not in outs:
                findings.append(Finding("warning", "one_sided_intent",
                                        f"{split}/{intent}: needs cases that should and should not escalate"))
    return findings


def summary(cases: list[Case]) -> str:
    lines = [f"{len(cases)} cases"]
    for name, key in [("split", lambda c: c.split), ("status", lambda c: c.status),
                      ("outcome", lambda c: c.evaluation_criteria.expected_outcome),
                      ("language", lambda c: c.tags.language), ("attack", lambda c: c.tags.attack),
                      ("provenance", lambda c: c.provenance)]:
        counts = Counter(str(key(c)) for c in cases)
        lines.append(f"  {name:10} " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", nargs="?", default="evals/cases", type=Path)
    parser.add_argument("--prompts", default="prompts", type=Path)
    args = parser.parse_args(argv)
    cases = load_cases(args.root)
    findings = check_cases(cases, args.prompts)
    print(summary(cases))
    for f in findings:
        print(f)
    return 1 if any(f.level == "error" for f in findings) else 0


if __name__ == "__main__":
    sys.exit(main())
