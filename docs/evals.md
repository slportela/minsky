# Evaluation strategy

How we measure whether the customer-service system works, is safe, and improves. Evals are part of the system, not an afterthought: every change to prompts, models, tools or policy is judged by them.

Sources: Anthropic, *Demystifying evals for AI agents* (Jan 2026); Sierra, *τ²-bench*; OpenAI, *Evaluation best practices*; the Factored brief. See `docs/references.md`.

## Vocabulary

Anthropic's definitions, used everywhere in this repo:

| Term | Meaning here |
|---|---|
| **Task / case** | One scenario with inputs and success criteria (`evals/cases/**.yaml`) |
| **Trial** | One run of a case. Outputs vary, so every case runs several trials |
| **Grader** | Logic that scores one aspect of a trial; a case has several |
| **Transcript** | The full record of a trial: messages, tool calls, tool results, errors |
| **Outcome** | The **end state** of the environment, e.g. whether a dispute exists in the mock bank. What the agent *says* happened is not the outcome |
| **Eval harness** | Runs cases × trials, records transcripts, grades, aggregates (`evals/`) |
| **Agent harness** | The system under test. We evaluate the harness and the model together |
| **Suite** | A group of cases for one goal (capability, regression, red team) |

## Principles

1. **Grade the outcome, not the path.** Check the end state and forbidden events. Never require an exact tool-call sequence; valid alternative paths must pass. A tau2 reference trajectory is only a way to *derive* the target end state.
2. **Deterministic graders first.** Use an LLM judge only for what code cannot see (clarity, tone, usefulness), and treat it as a diagnostic until it is validated (see "LLM judge").
3. **Consistency over luck.** A customer-facing agent must work every time: the headline metric is **pass^k** (all k trials pass), reported next to pass@k.
4. **Both directions.** For every behavior, test when it should happen *and* when it should not (escalate vs. not escalate, act vs. ask). One-sided sets produce one-sided agents.
5. **Labels come from the policy and the data, not from a model's output.** A case's expected outcome is decided by the policy rules and the source records. This matters doubly here because **no human will review the cases** (see "Label quality without human reviewers").
6. **Read the transcripts.** A score is not trusted until someone reads failed and passed transcripts and agrees the grade is fair.
7. **Eval health before agent blame.** A surprising result is more often an eval bug than a model fact: check the case, the grader, the simulator and the harness first.
8. **Honest reporting.** Every rate has its numerator, denominator and a 95% interval. Zero observed unsafe outcomes is reported as an upper bound, not as zero risk. Offline, simulated and projected numbers are labeled as such.

## Layers (Swiss-cheese model)

No single layer catches every problem; each one covers holes in the others.

```
                  ┌───────────────────┐
                  │ L4 ONLINE         │  traces, online checks, sampled judge, drift, feedback
                ┌─┴───────────────────┴─┐
                │ L3 RED TEAM           │  injection, cross-customer, expired session, tool faults,
                │                       │  social engineering, multilingual ambiguity
              ┌─┴───────────────────────┴─┐
              │ L2 END-TO-END SCENARIOS   │  golden cases with a user simulator, end-state grading
            ┌─┴───────────────────────────┴─┐
            │ L1 COMPONENT EVALS            │  router/intent (the learned component vs. baselines),
            │                               │  slot extraction, language id, guardrail classifiers
          ┌─┴───────────────────────────────┴─┐
          │ L0 UNIT TESTS                     │  policy rules, permissions, schemas: must be 100%
          └───────────────────────────────────┘
```

| Layer | Where nondeterminism enters (OpenAI's taxonomy) | Graded by |
|---|---|---|
| L0 | none (deterministic code) | pytest |
| L1 | instruction following, functional correctness of single calls, data precision (argument extraction) | labels + metrics (accuracy, macro-F1, calibration) |
| L2 | tool selection, multi-turn context, clarification, handoff | end state + safety + communicate + handoff; judge diagnostics |
| L3 | system prompt vs. user prompt conflicts, jailbreaks, tool failures | safety graders (fail on any violation) |
| L4 | real traffic distribution | online checks, sampled judge, feedback |

### Capability vs. regression suites

- **Capability** suites start with a low pass rate: tasks the agent cannot do yet, to climb.
- **Regression** suites should sit near 100%: tasks the agent must keep doing.
- A capability case that passes reliably (pass^k ≥ 0.95 for several runs) **graduates** into the regression suite. A suite that saturates gets harder cases.

## Golden dataset

### Case format

One YAML file per case, validated by `evals/schema.py` (the contract). It follows the τ²-bench task shape (a user scenario for the simulator plus evaluation criteria and a reward basis) and adds what the brief requires us to report.

| Field | Purpose |
|---|---|
| `id`, `split`, `status` | Identity; `dev`/`val`/`test`; `draft`/`active`/`retired` |
| `provenance`, `generator_model` | Who wrote it (`human-written`, `human-verified`, `data-derived`, `generated` + model id) |
| `tags` | Slices for reporting: language, country, intent, attack, segment, difficulty |
| `session` | Authentication state (`valid`, `expired`, `anonymous`) and customer |
| `tool_faults` | Fault injection: make a tool time out, fail or return empty on the n-th call |
| `llm_faults` | Fault injection: provider outage on the n-th `LLM.respond` (degraded handoff) |
| `user_scenario` | **Simulator only**: persona, hidden goal, known and unknown info, fixed scripted turns |
| `evaluation_criteria.expected_outcome` | `resolve`, `clarify`, `escalate`, `refuse`, `abstain` |
| `evaluation_criteria.env_assertions` | Named end-state checks (dispute opened, card not blocked, handoff created, no cross-customer read, …) |
| `evaluation_criteria.communicate_info` | Facts that must reach the customer (e.g. the case reference) |
| `evaluation_criteria.must_not` | Forbidden events. **Any occurrence fails the case**. All are checked by default |
| `evaluation_criteria.handoff_required_fields` | Fields the human agent must receive |
| `evaluation_criteria.nl_assertions` | Judge-graded statements; diagnostic unless the judge is validated |
| `evaluation_criteria.reward_basis` | Components that decide pass/fail. As in τ², the reward is the **product** of the listed components; `safety` is mandatory |

Every component is also recorded separately, so results show partial progress: a trial that verified the customer but did not open the dispute is better than one that failed immediately, even though both fail.

### Sources

| Source | Why | Label comes from |
|---|---|---|
| **Data-derived**: scenarios built from real records in silver (a customer who has a reversed or suspicious transaction) | Realistic grounding; the end state is computable | Policy spec + records, computed by code |
| **Organizer transcripts** (Spanish) | Real phrasing, intent distribution, seeds for simulator personas | `contact_reason`, after a quality check |
| **Generated**: Portuguese, portuñol, dialects, edge cases (one-word requests, typos, several intents in one message, long conversations) | Coverage the data does not have | Policy spec; wording by a model (`generator_model` recorded) |
| **Adversarial library** (L3) | Attacks by category (OWASP LLM Top 10: prompt injection, sensitive information disclosure, excessive agency) | Expected `refuse`/`escalate` by construction |
| **Failures** (from dev runs and online traces) | The most valuable source: real weaknesses | Policy spec, re-derived; never the failing output |

Anthropic's advice for starting: 20-50 cases drawn from real failures beat hundreds of synthetic ones. We start small and grow the set from failures.

### Splits and leakage

| Split | Use | Rules |
|---|---|---|
| `dev` | Iterate, error analysis, hill-climbing | Anything goes |
| `val` | Choose prompts, thresholds, models | Not read during error analysis |
| `test` | **Headline numbers only** | Locked: agents cannot edit it (`.claude/settings.json`); never used for tuning; changed only as a new versioned release |

- A customer that appears in `test` never appears in `dev`/`val` (`make eval-check` enforces this).
- No case text may appear under `prompts/`, e.g. as a few-shot example (enforced).
- For L1 (the learned router), splits are by **customer and by time** (train on older interactions, test on newer), never random rows.
- Draw splits at random, stratified by intent. Never pick them by current score (regression to the mean fakes improvements).

### Size and statistics

The noise floor of a pass rate is about 1/√(cases × trials): 25 × 2 ≈ ±14 points, 100 × 3 ≈ ±6, 150 × 3 ≈ ±5. A change smaller than the noise floor is not a result.

| Split | Target | Trials |
|---|---|---|
| dev | ~100 | 1-3 |
| val | ~50 | 3 |
| test | ~150 | 5 (pass^k with k up to 5) |

### Label quality without human reviewers

No human will review the cases, including the Portuguese ones. We design around it and report it as a limitation:

1. **Labels by construction.** The expected outcome and end state follow from the policy spec and the records, and are the same whatever the language. The language only changes the *wording* the simulator uses, not the label. So translation quality affects realism, not correctness.
2. **Automated audits instead of reviewers** (tier 3 of the audit checklist). A per-case auditor model flags ambiguous cases, suspect expected outcomes, cheatable graders, and too-strict or too-lenient graders.
3. **Oracle and null runs.** A reference solution must pass the case. The null baselines (always escalate, always refuse, empty reply) must fail it where they should.
4. **The judge is validated against deterministic judgments**, which the brief allows ("human or deterministic").
5. Everything a model wrote carries `provenance: generated` and its `generator_model`, and reports break results down by provenance.

## Graders

### Deterministic (decide pass/fail)

| Grader | Checks |
|---|---|
| `outcome` | The final outcome class matches `expected_outcome` |
| `env` | Every `env_assertion` holds on the end state of the mock bank |
| `safety` | No `must_not` event in the tool-layer audit log or replies: cross-customer read, action without confirmation, "done" without a verified write, a fact (amount, date, merchant, id) not present in tool results, wrong reply language, following injected instructions |
| `communicate` | Every `communicate_info` item appears in the agent's messages (normalized) |
| `handoff` | The handoff exists, validates against its schema and has the required fields |

What is implemented today, and how independent each check is:

- **Records first.** `env` reads the end state the tools wrote (dispute opened, card blocked, handoff created) and is the primary check. Treat it as the ground truth of what happened.
- **`outcome` is partly self-reported.** With model-written replies there is no fixed template to recognize, so the observed outcome comes from the conversation's own record (the acts the orchestrator accepted and the policy rule it applied), cross-checked with the dispute record when one exists. A wrong act would be visible in `env` and `safety`, not always in `outcome`.
- **`safety` does not reuse the product's checks.** The product refuses unsupported claims and invented numbers before sending (`backend/.../agent/speak.py`); the grader has its own rules for both (`evals/claims.py`): broader verb stems near their objects, questions and offers skipped, a refund statement accepted only for a reversed charge, "I opened it" accepted only if this conversation created a dispute, and every number or ISO date in a reply must appear in a tool result, the customer's words or the policy's constants. A shared bug cannot hide itself. Supported today: cross-customer disclosure, action without confirmation (judged from the customer's own words), unverified action claim, ungrounded fact (numbers and ISO dates; merchant names are not checked), and wrong language (lingua, only when the case language is es or pt and the case lists wrong_language; val cases do not list it). Not yet: followed injected instruction; a case that requires it fails explicitly.
- **Val labels come from the same `decide()`** the system runs, so the val comparison checks wiring on real records more than the policy itself. Hand-labeled cases and a live run are what test the policy and the model.

### LLM judge (diagnostics first)

- One isolated call per dimension (clarity, tone, clarification quality, handoff usefulness, each `nl_assertion`). Pass/fail or pairwise, never a blended score.
- The rubric is explicit, with an **"unknown" way out**. The candidate text is treated as untrusted data (it may contain injections aimed at the judge).
- The judge is not the model under test. Its model and prompt versions are recorded, as is its cost, separately.
- Controls: randomize order in pairwise comparisons; do not reward length; never tell the judge which answer is the baseline.
- **Validation**: known negatives (empty reply, "I don't know", confident answer to another question, invented amounts) must fail. Agreement against deterministic judgments on cases where the answer is known by construction must be ≥ 90% before any judge metric counts toward pass/fail.

### Metrics reported

Definitions follow the brief:

| Metric | Definition |
|---|---|
| Safe automated resolution | In-scope cases that reach the correct, policy-compliant outcome without a human, over **all in-scope cases**; plus the share where automation was attempted |
| Containment | Cases that end without transfer (reported, but it does not show the problem was solved) |
| Escalation quality | Correct transfers with useful handoff; **missed** and **unnecessary** transfers counted separately |
| Unsafe outcomes | Counts with denominators, by type; upper bound when zero |
| Reliability | pass^k and pass@k, k = 1…5 |
| Latency | End-to-end p50/p95 per trial (final successful request; retries excluded and counted) |
| Cost | Per attempted case and per successful resolution ("not defined" with no resolutions), from API usage data |
| Slices | All of the above by language, country, segment, attack type and provenance, with sample sizes |

Implemented in `evals/metrics.py`.

### Baselines (same workload, same graders)

1. **Always escalate**: a proxy for today's fully human process. Safe, zero automation.
2. **Rules/FAQ bot**: keyword routing plus templates.
3. **Our system**, per configuration (model, prompt version, router variant).

The null baselines double as harness checks: if "always escalate" scores well on resolution, a grader is broken.

## Harness

**Decision:** adopt the **τ²-bench** methodology and, if a spike confirms it fits, the library itself (MIT, Python ≥ 3.12). We would implement our workflow as a τ² domain (`policy.md`, tools, DB, tasks, optional user tools) and plug our system in as a custom agent. We keep our own graders for what τ² does not score (safety events, grounding, handoff) and run them over the saved trajectories. If the spike shows friction (agent adapter, determinism of the DB end state), we run our own runner on the same case format. See `docs/adr/0002-eval-methodology.md`.

Requirements for the runner, from Anthropic's eval audit checklist:

- **Same entry point as production.** The runner calls the real system, not a re-implementation, with the same prompts, tools and model settings.
- **Isolated trials.** Every (case, trial) starts from a fresh mock-bank state; no state shared between trials.
- **Infra failures are not agent failures.** Timeouts, API errors after retries and grader crashes go to `errors.jsonl` with a failure class. Truncated replies are marked `status: truncated`. Refusals are their own metric.
- **Full transcripts saved** per trial, plus the grader inputs and outputs (`evals/runs/<run-id>/`).
- **Pinned and recorded**: model ids (asserted on every response), prompt versions, sampling settings, seeds, case-set version, git SHA.
- **Bounded retries with jittered backoff**, attempt counts recorded; a hard wall-clock limit per trial.
- **Tokens and cost from the API usage data**, cache reads included; judge cost kept separate.
- **Oracle and null runs** before any paid full run.

### User simulator

The simulator is a model and can fail: it may leak the hidden goal, give up, invent facts or switch language. Mitigations:
- Adversarial and critical turns are **scripted** (`user_scenario.script`).
- The simulator runs on a different model or configuration than the agent.
- `simulator_error` is a failure category in error analysis; affected trials are excluded and counted.
- Simulator transcripts are read in every error analysis.

## Online evals

Hackathon traffic is simulated or demo traffic, and is **labeled as simulated online** in every report. The setup is the one a real deployment would use:

- **Tracing**: OpenTelemetry instrumentation plus our own trace and audit tables in Postgres (ADR 0007), viewable in the agent console and, during development, in Phoenix: every turn, LLM call, tool call, policy decision and model or prompt version. The trace is the audit record.
- **Deterministic online checks on every trace**, the same code as the offline `safety`/`communicate`/`handoff` graders: grounding, language match, policy violations, unverified claims. A violation is an alert, not only a dashboard entry.
- **Sampled judge**: the validated judge on a sample of traces (e.g. 10%), asynchronously.
- **Feedback**: the human agent rates each handoff ("useful? what was missing?"), and the customer can mark a reply as wrong.
- **Drift**: intent mix, router confidence, clarify/abstain/escalate rates, p95 latency and cost per resolution, against thresholds.
- **Release path** (described, not run): offline test gate → shadow mode → canary → A/B.

## Self-improving loop

```
 dev runs + online traces
          │
          ▼
 error analysis (read transcripts → first failure per trial → categories with counts)
          │
          ├──▶ eval bug? (grader, ambiguous case, simulator) ──▶ fix the eval first
          │
          ▼
 draft cases for uncovered failures ──▶ adversarial generator mutates passing cases
 (split: dev, status: draft)            (paraphrase, switch to pt, add injection, drop a field,
          │                              inject a tool fault); keeps only the variants that break
          ▼
 PROMOTION GATE (automated, no humans available)
   1. schema + make eval-check pass
   2. the oracle passes the case; the null baselines fail it where they should
   3. the per-case auditor (different model than the author) returns "ok"
   4. the expected outcome is re-derived from the policy spec, not from any output
          │
          ▼
 status: active in dev/val ──▶ regression suite grows; saturated slices get harder cases;
                                judge re-validated when its agreement drops
```

Rules:
- **Models may propose cases; they never decide labels.** Labels come from the policy spec and the data (principle 5).
- Hill-climb prompts on `dev`, select on `val`, report on `test`. A gain on dev with a flat val means overfitting.
- `test` changes only as a new release (new version, new ids), never in place and never mid-cycle.

## When evals run

| When | What | Gate |
|---|---|---|
| Every commit (pre-commit) | `make eval-check` when cases or prompts change | schema, leakage |
| Every PR (`make ci`) | L0 + L1 + L2 smoke: ~30 dev cases, 1 trial, deterministic graders | fails the PR; hard spend cap |
| Nightly | dev + val, 3 trials, judge diagnostics | report committed to `evals/reports/` |
| Release / submission | locked test, 5 trials | the headline table |

Bedrock has no batch discount for these runs (ADR 0001). Budget: every paid run prints its estimated cost first, and runs above the cap need explicit approval.

## Eval health

Before trusting a run, and after any surprising result:
- Tier 1: `make eval-check` (duplicates, balance, leakage, coverage).
- Tier 2: read a stratified sample of 20-50 cases and their transcripts.
- Tier 3: the per-case auditor over the whole set.
- Oracle passes and null fails; the model that answered is the model requested; infrastructure errors are separated; the headline recomputes from raw rows.
- Watch for saturation: at around 95%+ a suite measures format quirks, not capability. Graduate it and add harder cases.
