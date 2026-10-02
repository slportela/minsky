# Requirements traceability

Every requirement in the brief (`kickoff_docs/Factored AI & Data Hackathon 2026.pdf`, "PS") and the kickoff deck ("KO"), with how we plan to meet it, where it lives, and its status. **Check every proposal against this page**, and update it whenever the plan or the build changes.

Status: ✅ done · 🟡 partial · ⬜ planned · ⚠️ risk or gap

## Scope and deliverables

| ID | Requirement | Our answer | Where | Status |
|---|---|---|---|---|
| S1 | Working AI-first customer-service system, not a chatbot (PS, KO) | Dispute intake: understand → decide → act → verify → escalate | `solution.md`, `backend/agent`, `backend/api` | 🟡 orchestrator + HTTP API; integrated live smoke pending |
| S2 | One coherent workflow; more workflows earn nothing (PS) | Transaction-dispute intake | ADR 0005 | ✅ |
| S3 | Normal resolution path (PS, KO) | Rule D09: automatic intake with confirmation | `dispute_policy.md`, `backend/agent` | 🟡 D09 confirmation + verified open; live evaluation pending |
| S4 | Ambiguous or unsupported request (PS, KO) | Several candidate transactions → clarify; out of scope → abstain | `solution.md`, `backend/agent` | 🟡 clarify with accumulated filters and out-of-scope handoff; live evaluation pending |
| S5 | Case requiring a human (PS, KO) | D06 fraud, D07 above limit, D08 repeat complainer → structured handoff | `dispute_policy.md`, `backend/agent` | 🟡 fraud/agent handoff implemented; complete payload + console pending |
| S6 | Interactions in Spanish **and Portuguese**; report language and data limitations (PS, KO) | es + pt suites; Portuguese is generated and labeled; limitation stated | `evals.md`, `known_issues.md` | ⚠️ Spanish replies only; Portuguese implementation pending; no pt source data |
| S7 | Working prototype + evidence of production readiness + honest remaining work (PS) | POC on EC2; target architecture; POC → production map | `architecture.md`, `poc_to_prod.md` | 🟡 docs |
| D1 | Public repo `factored-hackathon-2026-<team>` (KO) | Rename or new repo before submission; remove private links from history | — | ⚠️ history has a private link |
| D2 | Link to the deployed tool (KO) | Demo on EC2, up until 2026-10-16 | `infra/README.md` | ⬜ |
| D3 | 4-6 slides (KO) | — | — | ⬜ |
| D4 | Short video: working solution + core architecture decisions (KO) | — | — | ⬜ |

## What the solution should demonstrate (PS 1-6)

| ID | Requirement | Our answer | Where | Status |
|---|---|---|---|---|
| P1.1 | Analyze contact reasons, demand patterns, data quality, operational constraints | Data quality vs. the dictionary; dispute volume, backlog, resolution time, SLA breach and first-contact resolution | `known_issues.md`, `disputes_findings.md` | 🟡 quality and dispute evidence done; demand patterns pending |
| P1.2 | Use the evidence to prioritize the workflow and define customer and business outcomes | Disputes are 40 % of complaints, 70 % still open, 15-16 days to resolve, ~20 % SLA breach; outcomes in `solution.md` | ADR 0005, `disputes_findings.md` | 🟡 evidence done; ADR 0005 to cite it |
| P2.1 | Maintain conversational context | Orchestrator state per conversation (`ConversationStore` + phases) | `backend/agent` | 🟡 isolated state, serialized turns, recoverable retries; process-local only |
| P2.2 | Clarify ambiguity | Clarify with customer-owned candidates (merchant, amount, date, transaction ref; no full card number) | `solution.md`, `backend/agent` | 🟡 filters retained after empty/multiple matches; new id/reset starts a search; live evaluation pending |
| P2.3 | Ground answers in permitted account, transaction or policy information | Read models in Postgres `bank.*`; tools scoped to the session; output grounding check | `read_models.md`, `backend/tools`, `guardrails` | 🟡 read models done |
| P2.4 | Use tools when they serve the workflow; report only verified outcomes | Read-back after every write in orchestrator | AGENTS rule 2, `backend/agent` | 🟡 |
| P3.1 | Define what it answers, what needs confirmation, when to abstain or transfer | Policy D01-D09; confirmation for dispute and card block | `dispute_policy.md` | 🟡 |
| P3.2 | Enforce permissions and policy outside model-generated text | Pure policy module; tool-layer checks; RLS in production | `backend/policy` | 🟡 policy done |
| P3.3 | Give the human the request, verified facts, actions, evidence, open questions | Structured handoff JSON + console | `solution.md`, `frontend` | 🟡 basic handoff; summary, evidence, open questions, trace + console pending |
| P4.1 | Repeatable data preparation with contracts, quality checks, lineage | Dictionary → generated silver models + tests; gold read models with contracts; dbt lineage; bronze manifest | `pipeline/`, `read_models.md` | ✅ |
| P4.2 | Update and freshness policy | Incremental bronze; atomic gold loads recorded in `ops.load_runs`; freshness alarm in production | `pipeline/`, `read_models.md`, `architecture.md` | 🟡 recorded, not alarmed |
| P4.3 | If data is static, demonstrate update correctness with a labeled test fixture | Late and corrected partition fixture | — | ⬜ |
| P4.4 | Evaluate at least one learned component against a baseline | Router ladder | `ml/README.md` | ⬜ |
| P4.5 | Valid labels, no leakage, justified representations, metrics, thresholds, splits | Customer + time splits; leakage check; threshold on val | `ml/README.md`, `evals.md` | ⬜ |
| P5.1 | Evaluate on held-out cases | Locked test split | `evals.md` | 🟡 harness partial |
| P5.2 | Cover incorrect or missing data, expired sessions, unauthorized access, prompt injection, tool failures, multilingual ambiguity | Red-team and failure suites; fault injection in cases | `evals/schema.py` | 🟡 schema supports it |
| P5.3 | Report successes, unsafe outcomes, handoff behavior, latency and cost, with sample sizes and limitations | Metrics module; report template | `evals/metrics.py` | 🟡 |
| P6.1 | Tracing, bounded retries, safe fallback, reproducible setup | OpenTelemetry + trace/audit tables (ADR 0007); retries with jitter; degraded mode = handoff; `make up` | `architecture.md`, `backend/agent`, `backend/api`, `backend/README.md` | 🟡 turn/clarification limits, retry recovery, explicit local dotenv loading; OTel + safe fallback pending |
| P6.2 | Explain capacity limits, monitoring, access controls, data retention, remaining deployment work | Workload tiers derived from the dataset; Bedrock quotas as the binding limit; architecture and mapping docs | `architecture.md`, `poc_to_prod.md` | 🟡 design |
| P6.3 | Explanations from sources, policy rules and execution records; not chain of thought | Rule ids + tool results + traces | `dispute_policy.md` | 🟡 |

## Boundaries (PS)

| ID | Requirement | Our answer | Where | Status |
|---|---|---|---|---|
| B1 | Only organizer-approved data and permitted resources | LATAM Bank dataset + team-generated cases only | — | ✅ |
| B2 | Label inputs as real, de-identified, synthetic or team-generated | `provenance` on every case; synthetic policy labeled | `evals/schema.py` | 🟡 |
| B3 | No private records, credentials or restricted data in public submissions or external model requests | `.env` never committed; read models exclude documents, contact details, income and score; Bedrock in our account | AGENTS rules 6-7, `read_models.md` | 🟡 |
| B4 | Sandbox services and mock tools allowed if contracts and limits are documented | Tool contracts in `tools/bank.py`; six session-scoped tools + denial tests | `backend/tools` | 🟡 |
| B5 | Authentication with a trusted test session; an id alone is not identity | `ToolSession` for tools; API-key + simulated OTP later; Cognito in production | `backend/identity`, ADR 0009 | 🟡 server-provisioned expiring bearer sessions; OTP/Cognito pending |
| B6 | Access and action permissions enforced in the service or tool layer | Session-scoped tools, denial tests (`test_tools_bank.py`) | `backend/identity`, `backend/tools` | 🟡 trusted HTTP identity + per-tool ownership checks; RLS pending |
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
| E12 | State the workload, sample size and cost assumptions (PS) | Workload tiers from dataset rates (assumptions, labeled) | `architecture.md` "Workload and capacity" | 🟡 |
| E10 | Compare by language and customer segment; small samples; investigate disparities | Slices in tags | `evals/schema.py` | 🟡 |
| E11 | Label offline, simulated and projected numbers separately; offline ≠ production improvement | Reporting rule | `evals.md` principle 8 | 🟡 |

## Judging criteria (KO)

| ID | Criterion | Our answer | Status |
|---|---|---|---|
| J1 | Project rationale and documentation | README reading order, ADRs, this page | 🟡 |
| J2 | AI engineering: backend, frontend, deployment | FastAPI, Next.js, POC on AWS + target architecture | 🟡 skeletons |
| J3 | Data analytics: data quality and insights | `known_issues.md` + `disputes_findings.md` + results by slice | 🟡 |
| J4 | Data engineering: extraction and transformation | Bronze → silver → gold pipeline with contracts, loaded atomically into Postgres | ✅ |
| J5 | ML: model selection, optimization, implementation, **tracking** | Router ladder; every training run writes a committed report (git SHA, data snapshot, splits, params, metrics, model hash) (ADR 0007) | ⬜ |
