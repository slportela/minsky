# evals

The eval harness. Strategy and rules: [`docs/evals.md`](../docs/evals.md).

| File | What |
|---|---|
| `schema.py` | The case contract (pydantic). A case = user scenario for the simulator + evaluation criteria |
| `checks.py` | Whole-set checks: duplicates, test-customer leakage, prompt leakage, coverage (`make eval-check`) |
| `metrics.py` | pass^k / pass@k, Wilson intervals, zero-event upper bounds, percentiles, cost per resolution |
| `cases/{dev,val,test}/` | One YAML per case; `test` is locked |
| `runs/` | Run outputs (`results.jsonl`, `errors.jsonl`, transcripts); git-ignored |
| `reports/` | Committed run reports: `<date>-<git sha>.md` |

Not built yet: the runner, the user simulator, the graders and the judge. They depend on the τ²-bench spike (ADR 0002) and on the system under test.

The five cases in `cases/dev/` are **illustrative drafts** (`status: draft`). They show the format, one per expected outcome and covering both languages. Their customer and transaction ids must be bound to real silver records before promotion.
