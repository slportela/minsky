# evals

The eval harness. Strategy and rules: [`docs/evals.md`](../docs/evals.md).

| File | What |
|---|---|
| `schema.py` | The case contract (pydantic). A case = user scenario for the simulator + evaluation criteria |
| `checks.py` | Whole-set checks: duplicates, test-customer leakage, prompt leakage, coverage (`make eval-check`) |
| `metrics.py` | pass^k / pass@k, Wilson intervals, zero-event upper bounds, percentiles, cost per resolution |
| `runner.py` | Scripted smoke of `POST /api/chat/turn` (`python -m evals.runner --include-drafts`) |
| `graders.py` | End-state graders: outcome, env, communicate, handoff, safety |
| `world.py` | Trial bank rows from `known_info`, labeled with `policy.disputes.decide` |
| `cases/{dev,val,test}/` | One YAML per case; `test` is locked |
| `runs/` | Run outputs (`results.jsonl`, `errors.jsonl`, transcripts); git-ignored |
| `reports/` | Committed run reports: `<date>-<git sha>.md` |

Not built yet: the free-play user simulator and the LLM judge (ADR 0002).

The scripted runner calls the real chat route. Extraction is not the production model: a turn contributes a merchant, an amount or a transaction id only when the script names it (`model: scripted-extract`). That is an offline smoke, not a headline number. Drafts are skipped unless `--include-drafts` is set.

The five illustrative drafts stay unbound. Four more drafts are runnable: `dispute-declined-not-charged-es` is the bronze transaction `TRX-0012RNBNIDX7W1SSRWRX` (declined, so no dispute). `dispute-eligible-open-es` and `dispute-above-limit-es` use fixture ids whose facts `decide()` labels D09 and D07; they are not `bank.dispute_scenarios` rows. `dispute-other-customer-txn-es` keeps that same real customer and plants another customer's transaction in the trial world.
