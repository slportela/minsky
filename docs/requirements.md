# Requirements traceability

Every requirement in the brief (`kickoff_docs/Factored AI & Data Hackathon 2026.pdf`, "PS") and the kickoff deck ("KO"), with how we plan to meet it, where it lives, and its status. **Check every proposal against this page**, and update it whenever the plan or the build changes.

Status: ✅ done · 🟡 partial · ⬜ planned · ⚠️ risk or gap

## Scope and deliverables

| ID | Requirement | Our answer | Where | Status |
|---|---|---|---|---|
| S1 | Working AI-first customer-service system, not a chatbot (PS, KO) | Dispute intake: understand → decide → act → verify → escalate | `solution.md` | ⬜ |
| S2 | One coherent workflow; more workflows earn nothing (PS) | Transaction-dispute intake | ADR 0005 | ✅ |
| S3 | Normal resolution path (PS, KO) | Rule D09: automatic intake with confirmation | `dispute_policy.md` | 🟡 policy only |
| S4 | Ambiguous or unsupported request (PS, KO) | Several candidate transactions → clarify; out of scope → abstain | `solution.md` step 2-3 | ⬜ |
| S5 | Case requiring a human (PS, KO) | D06 fraud, D07 above limit, D08 repeat complainer → structured handoff | `dispute_policy.md` | 🟡 policy only |
| S6 | Interactions in Spanish **and Portuguese**; report language and data limitations (PS, KO) | es + pt suites; Portuguese is generated and labeled; limitation stated | `evals.md`, `known_issues.md` | ⚠️ no pt source data |
| S7 | Working prototype + evidence of production readiness + honest remaining work (PS) | POC on EC2; target architecture; POC → production map | `architecture.md`, `poc_to_prod.md` | 🟡 docs |
| D1 | Public repo `factored-hackathon-2026-<team>` (KO) | Rename or new repo before submission; remove private links from history | — | ⚠️ history has a private link |
| D2 | Link to the deployed tool (KO) | Demo on EC2, up until 2026-10-16 | `infra/README.md` | ⬜ |
| D3 | 4-6 slides (KO) | — | — | ⬜ |
| D4 | Short video: working solution + core architecture decisions (KO) | — | — | ⬜ |

## What the solution should demonstrate (PS 1-6)

| ID | Requirement | Our answer | Where | Status |
|---|---|---|---|---|
| P1.1 | Analyze contact reasons, demand patterns, data quality, operational constraints | Analysis notebook/report on interactions, transcripts, complaints | `known_issues.md` (quality) | 🟡 quality done |
| P1.2 | Use the evidence to prioritize the workflow and define customer and business outcomes | Numbers backing ADR 0005; outcomes in `solution.md` | ADR 0005 | ⬜ |
| P2.1 | Maintain conversational context | Orchestrator state per conversation | `backend/agent` | ⬜ |
| P2.2 | Clarify ambiguity | Clarify step with masked candidates | `solution.md` | ⬜ |
| P2.3 | Ground answers in permitted account, transaction or policy information | Tools scoped to the session; output grounding check | `backend/tools`, `guardrails` | ⬜ |
| P2.4 | Use tools when they serve the workflow; report only verified outcomes | Read-back after every write | AGENTS rule 2 | ⬜ |
| P3.1 | Define what it answers, what needs confirmation, when to abstain or transfer | Policy D01-D09; confirmation for dispute and card block | `dispute_policy.md` | 🟡 |
| P3.2 | Enforce permissions and policy outside model-generated text | Pure policy module; tool-layer checks; RLS in production | `backend/policy` | 🟡 policy done |
| P3.3 | Give the human the request, verified facts, actions, evidence, open questions | Structured handoff JSON + console | `solution.md`, `frontend` | ⬜ |
| P4.1 | Repeatable data preparation with contracts, quality checks, lineage | Dictionary → generated dbt models + tests, dbt lineage, bronze manifest | `pipeline/` | ✅ |
| P4.2 | Update and freshness policy | Incremental bronze; freshness recorded and alarmed | `pipeline/`, `architecture.md` | 🟡 |
| P4.3 | If data is static, demonstrate update correctness with a labeled test fixture | Late and corrected partition fixture | — | ⬜ |
| P4.4 | Evaluate at least one learned component against a baseline | Router ladder | `ml/README.md` | ⬜ |
| P4.5 | Valid labels, no leakage, justified representations, metrics, thresholds, splits | Customer + time splits; leakage check; threshold on val | `ml/README.md`, `evals.md` | ⬜ |
| P5.1 | Evaluate on held-out cases | Locked test split | `evals.md` | 🟡 harness partial |
| P5.2 | Cover incorrect or missing data, expired sessions, unauthorized access, prompt injection, tool failures, multilingual ambiguity | Red-team and failure suites; fault injection in cases | `evals/schema.py` | 🟡 schema supports it |
| P5.3 | Report successes, unsafe outcomes, handoff behavior, latency and cost, with sample sizes and limitations | Metrics module; report template | `evals/metrics.py` | 🟡 |
| P6.1 | Tracing, bounded retries, safe fallback, reproducible setup | OpenTelemetry → Langfuse; retries with jitter; degraded mode = handoff; `make up` | `architecture.md` | 🟡 design |
| P6.2 | Explain capacity limits, monitoring, access controls, data retention, remaining deployment work | Covered in the architecture and mapping docs | `architecture.md`, `poc_to_prod.md` | 🟡 design |
| P6.3 | Explanations from sources, policy rules and execution records; not chain of thought | Rule ids + tool results + traces | `dispute_policy.md` | 🟡 |

## Boundaries (PS)

| ID | Requirement | Our answer | Where | Status |
|---|---|---|---|---|
| B1 | Only organizer-approved data and permitted resources | LATAM Bank dataset + team-generated cases only | — | ✅ |
| B2 | Label inputs as real, de-identified, synthetic or team-generated | `provenance` on every case; synthetic policy labeled | `evals/schema.py` | 🟡 |
| B3 | No private records, credentials or restricted data in public submissions or external model requests | `.env` never committed; minimization toward models; Bedrock in our account | AGENTS rules 6-7 | 🟡 |
| B4 | Sandbox services and mock tools allowed if contracts and limits are documented | Tool contracts documented with the code | `backend/tools` | ⬜ |
| B5 | Authentication with a trusted test session; an id alone is not identity | Test sessions + simulated OTP; Cognito in production | `backend/identity` | ⬜ |
| B6 | Access and action permissions enforced in the service or tool layer | Session-scoped tools, denial tests | AGENTS conventions | ⬜ |
| B7 | Credit workflows: separate risk and policy; no invented rules | Not our workflow; the same principle applies to disputes | — | ✅ n/a |
| B8 | No live lending decisions or money movement | The system never moves money or grants refunds | `dispute_policy.md` | ✅ by design |

## Evaluation evidence (PS)

| ID | Requirement | Our answer | Where | Status |
|---|---|---|---|---|
| E1 | Baseline vs. proposed on the same held-out workload | Always escalate, rules bot, our system | `evals.md` | ⬜ |
| E2 | Report number and mix of cases, label quality, model and prompt versions, repeated-run variability | Run report fields; trials × cases; pass^k | `evals.md` | ⬜ |
| E3 | Include failures in the results | Failure taxonomy in every report | `/error-analysis` skill | ⬜ |
| E4 | Model judge: document the rubric, validate a sample against human or deterministic judgments | Judge validated against deterministic cases | `evals.md` | ⬜ |
| E5 | Safe automated resolution rate over all in-scope cases, plus share attempted | Metric definition | `evals.md` | 🟡 |
| E6 | Containment (not proof of resolution) | Reported separately | `evals.md` | 🟡 |
| E7 | Escalation quality: missed and unnecessary transfers | Metric definition | `evals.md` | 🟡 |
| E8 | Unsafe outcomes with counts and denominators; zero observed ≠ zero risk | `Rate` + zero-event upper bound | `evals/metrics.py` | ✅ |
| E9 | p50/p95 latency; cost per attempted case and per successful resolution; "not defined" when there are none | `percentile`, `cost_per` | `evals/metrics.py` | ✅ |
| E10 | Compare by language and customer segment; small samples; investigate disparities | Slices in tags | `evals/schema.py` | 🟡 |
| E11 | Label offline, simulated and projected numbers separately; offline ≠ production improvement | Reporting rule | `evals.md` principle 8 | 🟡 |

## Judging criteria (KO)

| ID | Criterion | Our answer | Status |
|---|---|---|---|
| J1 | Project rationale and documentation | README reading order, ADRs, this page | 🟡 |
| J2 | AI engineering: backend, frontend, deployment | FastAPI, Next.js, POC on AWS + target architecture | 🟡 skeletons |
| J3 | Data analytics: data quality and insights | `known_issues.md` + workflow evidence + results by slice | 🟡 |
| J4 | Data engineering: extraction and transformation | Bronze → silver pipeline with contracts | ✅ |
| J5 | ML: model selection, optimization, implementation, **tracking** | Router ladder; experiment tracking still to choose (MLflow self-hosted, or Langfuse datasets) | ⚠️ tracking not chosen |
