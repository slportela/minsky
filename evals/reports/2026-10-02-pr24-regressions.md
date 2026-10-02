# PR 24: offline chat regression delta

This is an **offline, scripted-extraction L2 regression diagnostic**, not a production-model result.
It uses the real HTTP route, orchestrator, policy, tools, and read-store SQL against isolated SQLite
fixtures. It does not exercise PostgreSQL, Caddy/the browser, the configured model, Portuguese,
prompt injection, or the complete safety suite. No provider calls were made; provider spend is zero.

## Workload and labels

Three draft dev cases × one trial, identical between baseline and corrected runs. The labels follow
the trusted-session requirement (B5/B6) and deterministic D09 policy facts, not candidate replies.
The SQL world contains competing merchants/amounts for the clarification case. A policy error is
deliberately injected once; it is an expected fault, not an unclassified infrastructure failure.
No held-out selection or locked-test tuning was performed.

| Case | Baseline 2213a31 | Corrected implementation |
|---|---|---|
| Customer header without a credential | Fail: HTTP 200 instead of 401 | Pass: denied before tools |
| Merchant then amount across turns | Fail: target dispute absent | Pass: filters narrow the target and it is verified |
| Transient policy error followed by replay | Fail: retry HTTP 409 | Pass: 502, then successful replay and verified opening |

Observed pass counts: **0/3 → 3/3** (95% Wilson intervals 0–56.2% → 43.8–100%). These are descriptive
small-sample intervals for this deliberately chosen diagnostic set; they do not establish a
generalization gain or model reliability. One trial per case is insufficient to report pass^k for k>1.

## Evidence and reproduction

- Baseline messages, status codes, tool audit, grader reasons, and end state: `2026-10-02-pr24-before.json`.
- Corrected run: `2026-10-02-pr24-after.json`.
- Each artifact records model mode, case hashes, trial count, and the checkout base SHA. The corrected
  run was performed on the uncommitted correction tree based on 2213a31; the containing PR commit
  supplies the final source. The baseline imports the unmodified backend from 2213a31.

```bash
uv run python -m evals.regression_smoke --output evals/runs/pr24-regressions.json
# Baseline: export backend/src from 2213a31 to a separate directory, then:
PYTHONPATH=/path/to/baseline/backend/src .venv/bin/python -m evals.regression_smoke --output evals/runs/pr24-before.json
```

Additional unit controls cover malformed/anonymous/expired credentials, serialized concurrent
replays, failed read-back after a dispute write, and customer-scoped idempotent handoff retries.
The full `make ci` gate passes with 173 tests. Live extraction validation is still pending: no model
credential is available in the inherited environment, and agents must not read `.env`.
