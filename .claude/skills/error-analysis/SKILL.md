---
name: error-analysis
description: Analyze failed eval trials from an eval run, build a failure taxonomy with counts, and propose fixes and new cases. Use after an eval run or when a metric drops.
---

# Error analysis

1. Load the run's `results.jsonl` and `errors.jsonl` from `evals/runs/<run-id>/`. Infra errors (`errors.jsonl`) are reported separately and never counted as agent failures.
2. Read the full transcripts of the failed trials (not only the grades). For each one, write a one-line note of the **first** thing that went wrong (open coding).
3. Group the notes into categories (grader bug, ambiguous case, policy gap, router error, extraction error, ungrounded reply, unsafe action, language, tool handling, simulator error). Count them, and give example case ids.
4. Check the eval before blaming the agent: if a failure is a grader bug, an ambiguous case or a simulator error, fix the eval first (see `docs/evals.md`, "Eval health").
5. Output: the taxonomy table, the top 3 fixes ranked by failures removed, and draft cases for uncovered failure modes (use the `new-eval-case` skill, `split: dev`).
6. Never edit the test split and never tune on test results.
