# Empty-search clarification regression

Review follow-up: a zero-result search cleared all extracted slots, so a follow-up amount could
select a different merchant. Remove that reset; preserve merchant, amount and dates on both empty
and multiple matches. Existing explicit new-search/transaction-id and rejected-selection resets
remain unchanged.

Two real-SQL unit controls failed before the change and pass afterwards:
- Cafe + amount 50 + date range returns no rows; correcting only the amount to 25 retains merchant
  and dates and selects Cafe rather than the competing Other charge.
- Cafe has no rows; adding amount 25 still yields no match instead of selecting Other's charge.

A new draft dev case exercises the first path through the real HTTP API, tools, policy and SQLite.
On the same four-case offline regression workload, pass counts are **3/4 → 4/4** (95% Wilson intervals
30.1–95.4% → 51.0–100%). The added case changes **0/1 → 1/1** (0–79.3% → 20.7–100%). These deliberately
selected one-trial scripted diagnostics do not establish generalization or live-model reliability.
No provider calls were made; no locked-test files were changed or used for tuning.

Full transcripts, HTTP results, audit and verified dispute state are preserved in
`2026-10-02-search-miss-before.json` and `2026-10-02-search-miss-after.json`. The baseline is the
pre-follow-up backend `165b828`; the corrected run used its working tree plus this fix, with the
containing PR commit supplying the final source. Draft-case safety coverage is explicitly partial.

Reproduce: `uv run python -m evals.regression_smoke --output evals/runs/search-miss.json`.
Live extraction and the deployed integrated gate remain pending as tracked in the PRs.
