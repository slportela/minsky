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
| `generate_val_cases.py` | Writes val cases from real gold rows (`bank.dispute_scenarios`): labels from data + policy; es and pt |
| `compare_systems.py` · `baselines.py` | Today's process (always send to an agent) vs Minsky on the same cases, as business metrics |
| `claims.py` | The graders' own reading of what a reply claims to have done (kept apart from the reply guard in `agent/speak.py`, so a phrasing one misses is still caught by the other) |
| `runs/` | Run outputs (`results.jsonl`, `errors.jsonl`, transcripts); git-ignored |
| `reports/` | Committed run reports: `<date>-<git sha>.md` |

Not built yet: the free-play user simulator and the LLM judge (ADR 0002).

The scripted runner calls the real chat route. Extraction is not the production model: a turn contributes a merchant, an amount or a transaction id only when the script names it (`model: scripted-extract`). That is an offline smoke, not a headline number. Drafts are skipped unless `--include-drafts` is set.

The five illustrative drafts stay unbound. Four more drafts are runnable: `dispute-declined-not-charged-es` is the bronze transaction `TRX-0012RNBNIDX7W1SSRWRX` (declined, so no dispute). `dispute-eligible-open-es` and `dispute-above-limit-es` use fixture ids whose facts `decide()` labels D09 and D07; they are not `bank.dispute_scenarios` rows. `dispute-other-customer-txn-es` keeps that same real customer and plants another customer's transaction in the trial world.

## Corrected offline smoke

Run `uv run python -m evals.runner --include-drafts --output evals/runs/<new-run-id>`.
The runner reads dev only, fails on an empty selection, and refuses to overwrite a run directory.
The twelve runnable drafts include the PR-24 regressions, both fraud card-block decisions, an expired bearer, and exact decimal-amount selection. Policy fixtures are checked against
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

### Real extraction and integrated smoke

The paid CLI is now available with `--extractor real`, explicit uncached input/output token rates,
`--max-cost-usd`, and a trial timeout. Use `--estimate-only` first. Every call reserves a conservative
UTF-8 byte/schema allowance plus its output limit before dispatch; reservations are not refunded on
provider errors. SDK retries are disabled for these diagnostics. Usage-priced cost is not an invoice;
unknown charges on failed calls remain covered by reservations. Use the actual pinned model's rates
or higher conservative rates, never rates for a cheaper model. The reservation assumption is intended
for the current byte-tokenized compatible provider; validate it before switching tokenizers/providers.

`--database postgres --gold-cases` binds supported dev scripts to read-only gold rows, rechecks policy
labels, and runs the actual pooled store. Fixture-specific filter/error scenarios remain in SQLite.
Case writes are still process-local. An expired-bearer case and empty-search clarification extend the isolated smoke; exact decimal-amount selection brings it to twelve cases;
the gold-bound diagnostic contains eight. They are separate workloads, not interchangeable scores.

`python -m evals.compare BEFORE AFTER --output NEW.json` rejects mismatched cases, model, clock, mode,
database or trial counts. For an archived pre-credential backend only, `--legacy-auth-baseline` sends
its old legitimate caller identity on policy scenarios; auth-denial/expired cases still expect rejection.
Record the archived revision with `--backend-revision`. Metadata hashes the actually imported backend
source and runtime prompt files, separately from the eval-harness checkout SHA.

`python -m evals.browser_smoke` starts a loopback-only diagnostic gateway to a separately running
frontend and the real API/PostgreSQL. It writes ephemeral credentials separately with mode 0600 and
removes them on shutdown. Requests, model calls, audit and verified process-local writes go to evidence;
credentials and headers are excluded. Scripted mode is clearly labeled and never closes the live L2 gate.
The gateway excludes Caddy/TLS; those require a final deployed-stack check.

The exact sequence and remaining checks are in [`docs/integrated_smoke.md`](../docs/integrated_smoke.md).
Model credentials must come from a securely configured inherited environment. Agents do not read `.env`.
Generated Portuguese drafts and their limitation are in [`docs/known_issues.md`](../docs/known_issues.md). Complete safety graders, held-out numbers, persistent writes and deployed smoke remain pending.
