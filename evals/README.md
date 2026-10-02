# evals

The eval harness. Strategy and rules: [`docs/evals.md`](../docs/evals.md).

| File | What |
|---|---|
| `schema.py` | The case contract (pydantic). A case = user scenario for the simulator + evaluation criteria |
| `checks.py` | Whole-set checks: duplicates, test-customer leakage, prompt leakage, coverage (`make eval-check`) |
| `metrics.py` | pass^k / pass@k, Wilson intervals, zero-event upper bounds, percentiles, cost per resolution |
| `runner.py` | Scripted smoke of `POST /api/chat/turn` (`python -m evals.runner --include-drafts`) |
| `graders.py` | End-state graders: outcome, env, communicate, handoff, explicitly partial safety |
| `world.py` | Trial bank rows from `known_info`, labeled with `policy.disputes.decide`; real read SQL in SQLite |
| `cases/{dev,val,test}/` | One YAML per case; `test` is locked |
| `runs/` | Run outputs (`results.jsonl`, `errors.jsonl`, transcripts); git-ignored |
| `reports/` | Committed run reports: `<date>-<git sha>.md` |

Not built yet: the free-play user simulator and the LLM judge (ADR 0002).

The scripted runner calls the real chat route. Extraction is not the production model: a turn contributes a merchant, an amount or a transaction id only when the script names it (`model: scripted-extract`). That is an offline smoke, not a headline number. Drafts are skipped unless `--include-drafts` is set.

The five illustrative drafts stay unbound. Four more drafts are runnable: `dispute-declined-not-charged-es` is the bronze transaction `TRX-0012RNBNIDX7W1SSRWRX` (declined, so no dispute). `dispute-eligible-open-es` and `dispute-above-limit-es` use fixture ids whose facts `decide()` labels D09 and D07; they are not `bank.dispute_scenarios` rows. `dispute-other-customer-txn-es` keeps that same real customer and plants another customer's transaction in the trial world.

## Corrected offline smoke

Run `uv run python -m evals.runner --include-drafts --output evals/runs/<new-run-id>`.
The runner reads dev only, fails on an empty selection, and refuses to overwrite a run directory.
The nine runnable drafts include the three PR-24 regressions and both fraud card-block decisions. Policy fixtures are checked against
`decide()` and contradictory scripted not-me signals are rejected. The D09 case disputes an incorrect
amount on a recognized charge; “no reconozco” routes to D06. Each trial executes the real read-store
SQL in a fresh SQLite bank: predicates, order, and limits are not discarded. PostgreSQL-specific
behavior still needs the integrated smoke.

Credentials are random and provisioned only inside the isolated trial. Headers and credentials are
never saved or given to extraction. Customer state and expected transient HTTP failures are explicit.
Records preserve requests/responses, model usage, tool inputs/results, tool audit, final state, and
grader reasons. `metadata.json` records commit, dirty status, case/prompt hashes, clock, and mode;
`results.jsonl`, `errors.jsonl`, and per-trial JSON preserve successes and failures separately. A trial
error does not stop later cases. `summary.json` includes denominators, errors, and provider spend.

### Safety scope

The runnable smoke drafts explicitly request cross-customer disclosure, action confirmation, and
unverified-action-claim checks. Each write is checked against the preceding server state, exact user
confirmation, and selected transaction/product; selection yes is not action yes. An uninstrumented
write fails confirmation grading. Grounding, reply-language, and injection-following checks are
**unsupported**, not successful: a case requiring one fails explicitly. The illustrative cases keep
their full safety requirements and are not silently promoted or scored by this partial runner.

### Real extraction

`run_trial(case, extractor="real")` uses the configured production LLM and records its model and usage.
The CLI intentionally refuses this mode until an integrated harness supplies a printed estimate,
spend cap, and timeout. No model credentials were available for this correction; no live L2 result is
claimed. Do not load `.env` through an agent or pass keys in commands/chat. Production-model validation,
Portuguese, baselines beyond null controls, and headline held-out metrics remain pending.
