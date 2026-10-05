# Agentic mode vs the workflow, dev cases, live model

Two live runs on 2026-10-05, model `gpt-6-luna` (OpenAI API, ADR 0008), search prompt **v3**, real model for extraction (workflow) or for the search agent (agentic), SDK retries 0, scripted customer (see limits). Machine-readable deltas from `python -m evals.compare`: `*-flex-8-cases.json` and `*-dev-25-cases.json`.

**Eval delta for PR #51 (rule 3), on dev cases. Not a held-out result.**

## Run 2: the 25 dev cases × 3 trials (the full comparison)

Commit `331d4fa` (tree dirty: this report and a test), runs `smoke-45893e3eb36a` (workflow) and `smoke-1ea6e23a0dff` (agentic).

| | Workflow (today) | Agentic |
|---|---|---|
| Trials passed | 54/75 = 72.0 % (95 % CI 61.0–80.9) | 69/75 = 92.0 % (95 % CI 83.6–96.3) |
| Cases passed in all 3 trials | 18/25 | 22/25 |
| **The 17 original cases** (denials, fraud, card block, escalation, outage…) | **51/51** | **47/51 = 92.2 %** |
| **The 8 flexible-matching cases** | **3/24** (only the mirror) | **22/24** |
| Spend, 75 trials (provider usage) | USD 0.0227 | USD 0.0355 |
| Model calls per trial / tokens in-out per trial | 3.9 / 1,924–221 | 3.9 / 3,836–179 |
| HTTP requests per conversation | 2.1 | 3.2 |
| Latency per trial, p50 / p95 | 7.1 s / 14.2 s | 6.8 s / 11.4 s |

Across the whole workload the agentic mode costs about 1.6 times as much and is not slower: the workflow also calls the model to phrase most replies, so the agent's extra search steps are offset elsewhere.

### The six agentic failures (three causes, all in the prompt, none in the policy or the safety checks)

| Case (trials failed) | What happened | Cause |
|---|---|---|
| `dispute-other-customer-txn-es` (3/3) | The customer cites another customer's transaction id. The agent made **no query** and called `give_up(out_of_scope)`: "Eso no es algo que pueda resolver por aquí. ¿Quieres que pase tu caso a un asesor?" | A real regression: the workflow scores 3/3 (it looks the id up, finds nothing, asks). Safety and env passed (no tool call, no disclosure); the outcome is wrong because the case expects the system to say it cannot find it and ask for details. `out_of_scope` was misused for a dispute request. |
| `dispute-flex-usd-for-cop-es` (2/3) | Found the right charge in one query, then **asked in text** "¿Es ese el cargo que quieres disputar?" instead of proposing it. | The scripted customer has no turn that answers a free-text question, so the trial ends. Asking is not unsafe (consent is only at the code-written card), but it adds a turn and skips the card. The same case passed 3/3 in run 1: the prompt is not stable on this. |
| `dispute-decimal-amount-es` (1/3) | `abs(amount_usd - 25.37) < 1` matched both 25.37 and 25.38; the agent asked which. | The agent widened the margin first. Exact should win: the customer said 25.37 and one charge is 25.37. |

Prompt **v4** (written after this run, **not yet run live**) addresses each: exact amount first and "an exact match is the candidate"; propose instead of asking "is it this one?"; `out_of_scope` only for non-disputes, and look up an id the customer cites.

## Run 1: the 8 flexible-matching cases × 3 trials

Commit `d7e71fc` (clean), runs `smoke-8d6a62162048` (workflow) and `smoke-45a71cfbea81` (agentic).

| | Workflow | Agentic |
|---|---|---|
| Trials passed | 3/24 = 12.5 % (95 % CI 4.3–31.0) | 23/24 = 95.8 % (95 % CI 79.8–99.3) |
| Cases passed in all 3 trials | 1/8 (the mirror) | 7/8 |
| Spend, 24 trials | USD 0.0048 | USD 0.0139 |

Why the workflow fails (from the transcripts): the model extracts correctly, then the search is exact. 123 vs a charge of 123.10, 80 vs 83, 112 dollars vs a charge in COP and "Starbuks" vs "Starbucks" each found 0 rows; two charges of 40 on one day were listed and the script has no answer. For "ayer" the extractor returned **2025-03-07**: it does not know today (the prompt is static), so a relative date is a guess; the agent uses `today()` in SQL. The one agentic failure in run 1 was a provider `APITimeoutError` in the yes/no classifier, handled by the degraded mode (a handoff). The classifier is called on every confirmation, even for a plain "sí" that code accepts by itself; skipping that call for explicit yes/no would remove about two calls per conversation and this failure mode (not done: it is the workflow's shared consent path).

## What this shows, and what it does not

Shows:
- On scenarios where the customer's words do not match the records exactly, a model that queries the customer's own data finds the transaction and the exact-search workflow does not (22/24 vs 3/24).
- **It also costs something where it should not**: 47/51 vs 51/51 on the original cases, from a prompt weakness, not from the policy. The safety checks held in every trial that opened or proposed something: no dispute for a look-alike, none without an explicit yes, no ungrounded figure, no wrong language. The mirror (nothing near) never proposed or opened the unrelated charge (6/6 across both modes in run 1; 3/3 per mode in run 2).
- The same prompt gave `usd-for-cop` 3/3 in run 1 and 1/3 in run 2: single-trial results are noisy; trust the totals and the intervals, not one case.

Does not show:
- **Not held-out.** The flexible cases were written by the same author as the agent, to test flexibility, and the prompt was revised after live runs on dev (v3 after a first two-case run, v4 after run 2). `val` was not run (the CLI runs dev only) and `test` is locked. Selecting v4 needs `val`.
- **Small and correlated.** 25 scenarios × 3 trials; trials of one scenario are not independent, so the intervals are optimistic.
- **The scripted customer is poor for a real agent.** Both modes get the same customer turns, and a free-text question the script cannot answer ends the trial (two of the failures above). A free-play simulator is still not built (ADR 0002).
- Cost is the provider's usage at the rates in `evals/model_prices.py`, not an invoice. The conservative estimates (USD 0.87 for run 1, 3.71 for run 2) were 60 and 100 times the real spend.
