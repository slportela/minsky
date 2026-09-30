# The challenge, in one page

Read this first. Details: the brief in `kickoff_docs/`, every requirement traced in [`requirements.md`](requirements.md), how we measure in [`evals.md`](evals.md), how we work in [`../AGENTS.md`](../AGENTS.md).

## The problem

Build an **AI-first customer-service system for a bank**: not a chatbot, a system that can **understand → decide → act → verify → escalate**. It must know when *not* to act. The data is the synthetic LATAM Bank dataset (Mexico, Colombia, Argentina; 3 years; 13 tables: customers, products, transactions, call-center interactions and transcripts, complaints, surveys, …).

Organizer's final takeaway: *"Build something that works, prove that it works, and know when it should not act. And show us what it would take to make it real."*

## Scope: one workflow, done deeply

We pick **one** workflow. The brief's examples (not tracks; more workflows earn nothing):

| Workflow | Fit with the data | Watch out for |
|---|---|---|
| Account / payment inquiries | Transactions, products, balances | Easy to automate, but shallow |
| Card-service support | Card products, blocked/declined transactions | Blocking a card is an action that needs confirmation |
| **Transaction-dispute intake** | Transactions (status, fraud flags) + complaints (amounts, SLA, resolution, compensation) give a baseline | Needs a clear synthetic dispute policy |
| Credit-product info & eligibility | Products, credit score, income | Extra rules: separate risk model and eligibility policy; the model must never approve credit |

**Decided: transaction-dispute intake** (ADR 0005). What we build: [`solution.md`](solution.md). The data analysis still has to confirm the choice with numbers (volume, cost, pain).

Whatever we pick, the demo must show:
- a normal case resolved automatically,
- an ambiguous or unsupported request (the system asks or declines),
- a case that needs a human (a structured handoff),
- conversations in **Spanish and Portuguese**. The data is Spanish only; that is a reported limitation.

## What we must deliver (deadline 2026-10-05)

1. A public GitHub repo `factored-hackathon-2026-<team>`.
2. A link to the deployed system (on AWS; it must keep working until 2026-10-16).
3. 4-6 slides.
4. A short video: demo plus the key architecture decisions.

Send everything to hackathon.admin@factored.ai.

## What is scored

The system must work first. Then:

| # | The brief asks for | In practice |
|---|---|---|
| 1 | A problem backed by data | Analysis of contact reasons, demand and data quality that justifies the workflow; a baseline of today's (human) service |
| 2 | A working AI system | Keeps context, clarifies, answers only from permitted records or policy, reports only verified actions |
| 3 | Controlled automation | What it answers, what needs confirmation, when it abstains or escalates. **Permissions and policy enforced in code, not in prompts.** The human gets request, verified facts, actions, evidence, open questions |
| 4 | Data and ML rigor | Repeatable pipeline with contracts, quality checks, lineage and freshness; **at least one learned component compared against a baseline**, with valid labels, no leakage and justified splits |
| 5 | Measured quality and failures | Held-out evals including bad data, expired sessions, unauthorized access, prompt injection, tool failures, multilingual ambiguity; success, unsafe outcomes, handoffs, latency and cost, with sample sizes |
| 6 | A route to production | Tracing, bounded retries, safe fallback, reproducible setup; capacity, monitoring, access control, retention; honest list of remaining work |

Judges look across AI engineering (backend, frontend, deployment), ML, data engineering, data analysis, and overall rationale and documentation.

## What we need to build

```
  customer (ES / PT)                                          human agent
        │                                                          ▲
        ▼                                                          │ structured handoff
  ┌──────────┐    ┌────────────────── the system ──────────────────┴──┐
  │ chat UI  │───▶│ auth ─▶ understand ─▶ decide ─▶ act ─▶ verify ─▶ reply │
  └──────────┘    │         (LLM +       (policy   (tools  (read-back)    │
                  │          learned      in code)  with                │
                  │          router)                permissions)        │
                  └──────────┬───────────────────────┬───────────────────┘
                             │                       │
                   data pipeline (S3 →          traces + audit log
                   bronze → silver → gold)      (postgres + OpenTelemetry)
                             ▲
                             │
        ┌────────────────────┴──────────────────────────────────────────┐
        │ EVALS: simulated customers × golden cases → end-state grading │
        │ → pass^k, safety, escalation, latency, cost, by language      │
        └───────────────────────────────────────────────────────────────┘
```

| Workstream | Output |
|---|---|
| **Data** | Pipeline to silver (exists) and gold read models for the workflow; data-quality report (`known_issues.md`) |
| **Analysis** | Evidence for disputes with numbers; baseline of the current service; later, the business case |
| **Policy & tools** | Written policy for the workflow (synthetic, clearly labeled); mock bank tools with permission checks and an audit log; test identity sessions |
| **AI system** | Orchestrator (code decides, LLM understands and phrases), learned router, handoff, guardrails, ES/PT |
| **Evals** | Golden cases, user simulator, graders, baselines, reports ([`evals.md`](evals.md)) |
| **Platform** | POC on AWS (EC2 + compose), target production architecture and its mapping, Bedrock, tracing, runbook |
| **Story** | Slides, video, system card, limitations |

## Stack constraints

- **Everything runs in our AWS account** (ADR 0001): S3 for data, Bedrock for models (Claude, and others available there), AWS for hosting. Any extra tool is self-hosted. No SaaS outside AWS.
- Organizer credentials are read-only and only used to read the source dataset.

## Plan

| Days | Focus |
|---|---|
| Sep 26-27 | Data evidence for disputes; policy review ([`dispute_policy.md`](dispute_policy.md)); gold tables; first 20-50 eval cases |
| Sep 28-30 | Tools + permissions + orchestrator; router ladder (the learned component); eval runner and simulator |
| Oct 1-2 | Full eval runs, error analysis, fixes; Portuguese and red-team suites; handoff console |
| Oct 3 | AWS deployment, tracing, runbook |
| Oct 4-5 | Final test-split run, slides, video, system card; submit |

## Open decisions

1. **Confirm the workflow with data.** Dispute intake is chosen (ADR 0005); the analysis must back it with numbers.
2. **Eval harness.** τ²-bench or our own runner, decided by a half-day trial (ADR 0002).
3. **Models on Bedrock.** Check which ones are available in our region: Claude is; Gemini and OpenAI's proprietary GPT models may not be. Then choose per step by eval results.
4. **CI.** Local `make ci`, or GitHub Actions: GitHub is required for the submission anyway.
