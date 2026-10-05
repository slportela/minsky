# Solution: transaction-dispute intake

What we build for workflow 3 of the brief, at a high level. Read [`challenge.md`](challenge.md) first. Status: **proposal, the direction we start from** (workflow: ADR 0005; stack: ADR 0006, proposed). Checked against the brief in [`requirements.md`](requirements.md). Numbers marked *to measure* come from the data analysis, not from assumptions.

## The problem we solve

A customer sees a charge they don't recognize, or that is wrong, and wants to dispute it. Today they call or write, wait, explain, and a human agent has to:
- find the transaction,
- check whether it can be disputed,
- collect the details,
- open a claim (PQR),
- or pass it to the fraud team.

What we want:

| For the customer | For the bank |
|---|---|
| A dispute opened in minutes, in Spanish or Portuguese, with a case reference and a realistic expectation of when it will be resolved | Agents handle only what needs judgment (fraud, high amounts, exceptions), and receive the case already structured |

Evidence to collect (step 1 of the plan, *to measure*):
- volume and trend of dispute-related contacts (`call_center_interactions.contact_reason`), their handle time, wait time, escalation and first-contact resolution;
- dispute-like complaints (`complaints`): resolution days, SLA breaches, compensation, repeat complainers;
- the transactions that are disputable: status, fraud flags, amounts by country.

## What the system does

```
 customer: "no reconozco un cargo de 1.249 pesos en una farmacia"
     │
     ▼
 1 AUTHENTICATE   test session + simulated OTP; expired or missing → re-authenticate, nothing else
     │
 2 UNDERSTAND     language (es / pt / mixed) · intent (dispute, fraud, other, out of scope)
     │            · details: merchant, amount, date, channel
     │            out of scope ──▶ ABSTAIN: say what we can do, offer a human
     ▼
 3 FIND THE       search ONLY this customer's transactions
   TRANSACTION    0 matches → ask for more detail (max 2 tries) → ESCALATE
     │            2+ matches → CLARIFY: show masked candidates, customer picks
     ▼            1 match → confirm with the customer
 4 CHECK POLICY   deterministic rules (synthetic policy: docs/dispute_policy.md, rules D01-D09):
     │              declined → nothing was charged: explain        (RESOLVE, informational)
     │              reversed → already refunded: explain           (RESOLVE, informational)
     │              pending  → cannot be disputed yet: explain     (ABSTAIN)
     │              already disputed → give the existing reference (RESOLVE)
     │              outside the dispute window → decline + reason  (REFUSE, human option)
     │              "not me" / fraud signals → offer card block (needs explicit YES)
     │                                         + priority handoff to fraud (ESCALATE)
     │              above the automatic amount limit, repeat complainer
     │                                → handoff to a dispute agent (ESCALATE)
     │              otherwise → eligible for automatic intake
     ▼
 5 COLLECT        reason (unrecognized, duplicate, wrong amount, not received, refund not
     │            received) and the reason-specific questions the policy requires
     ▼
 6 CONFIRM        summary in the customer's language; customer says YES
     ▼
 7 ACT + VERIFY   open_dispute (idempotent) → read it back → only then say it is done
     ▼
 8 REPLY          case reference + expected resolution time (from historical complaints)
```

No money moves and no refunds are granted: the system only opens the claim, blocks a card after confirmation, and hands off.

### Where AI is used, and where it is not

| Step | Handled by | Why |
|---|---|---|
| Authentication, permissions | Code | Security is never a model decision |
| Language | Code (a language-id library), LLM as fallback for mixed text | Cheap and deterministic |
| Intent, dispute reason | **Learned router** (trained on transcripts), LLM as fallback when confidence is low | The required learned component; cheaper and faster than an LLM, and measurable against baselines |
| Details (merchant, amount, date) | LLM with structured output | Free text in two languages; the schema forces a valid shape |
| Transaction search | Code (SQL over the customer's own records) | Exact, auditable |
| Eligibility and routing | Code (policy rules) | Brief: policy outside model-generated text |
| Questions, summaries, replies | LLM, **only from verified facts** | Natural language in es/pt; every amount, date and merchant is checked against the tool results |
| Handoff | Code builds the payload; LLM writes the summary | Structured JSON: request, verified facts, actions taken, evidence, open questions |

### The human side

Handoff cases go to an **agent console**. The agent sees:
- the request,
- the verified facts, with the records they come from,
- the actions taken, e.g. "card blocked 14:02, confirmed by the customer",
- the policy rule that triggered the handoff,
- the open questions,
- a link to the full trace.

The agent never has to read the raw transcript.

## Components

```
                         ┌──────────────────────── frontend (Next.js) ────────────────────────┐
                         │  /chat: customer (es/pt)          /console: dispute and fraud agents │
                         └────────────────┬─────────────────────────────────┬──────────────────┘
                                          │ HTTPS + session token           │
┌─────────────────────────────────────────▼─────────────────────────────────▼─────────────────┐
│ backend (FastAPI)                                                                           │
│                                                                                             │
│  identity ─▶ orchestrator (state machine, steps 1-8) ─▶ policy (pure rules, unit-tested)    │
│  (sessions,      │            │              │                                              │
│   OTP mock)      ▼            ▼              ▼                                              │
│              router        llm steps       tools (permission check + audit on every call)   │
│             (learned)    (Bedrock: extract,  get_transactions · get_transaction ·           │
│                          phrase, summarize)  open_dispute · get_dispute · block_card ·      │
│                                              create_handoff                                 │
│  guardrails: input (injection signals) · output (grounding, language, no data from others)  │
└──────────┬───────────────────────┬─────────────────────────┬────────────────────────────────┘
           │                       │                         │
           ▼                       ▼                         ▼
   PostgreSQL                 Amazon Bedrock            OpenTelemetry + trace tables
   bank.*  (read-only: gold   (Claude; other            (traces = audit record,
            read models)       models via the same       viewer: console, Phoenix)
   cases.* (write: disputes,   interface)
            handoffs, sessions,
            audit log)
           ▲
           │ load gold
   pipeline: organizer S3 → bronze → silver → gold (dbt) ──▶ our S3
```

- **One PostgreSQL** holds both the read models (gold tables loaded from the lake) and the writes (cases). Tools use a read-only role for `bank.*`. Every query is scoped to the session's customer, and in production Row-Level Security enforces it as a second line.
- **The mock bank is our backend.** Tools are the "core banking API" of the demo: typed, permission-checked and audited. Their contracts and limits are documented, as the brief allows.
- **Deterministic clock.** The data ends on 2026-06-18; the system's "today" is configurable, so the policy windows and the evals are reproducible.

## Evals for this workflow

The cases in `evals/cases/` follow the flow above. Each step's branches become cases, in both directions (see [`evals.md`](evals.md)):

| Suite | Examples |
|---|---|
| Normal | Single clear match → dispute opened; already disputed → existing reference |
| Clarify | 2+ candidate transactions; vague date; missing amount |
| Policy | Declined, reversed, pending, outside the window, above the limit, repeat complainer |
| Human | Fraud ("not me") with card block + handoff; above the limit; repeat complainer; customer asks for a human |
| Red team | Another customer's transactions, injection in the message and in a merchant name, "the manager approved it", expired session |
| Failures | Transactions tool timeout, dispute write fails, read-back mismatch |
| Language | Every suite in es (MX, CO, AR variants), pt, and mixed |

**Baselines**, run on the same workload:
1. always escalate (today's human process);
2. a rules and keyword bot;
3. our system.

**The learned component**: the router trained on transcript `customer_text` with `contact_reason` labels, split by customer and by time. It is compared against keywords, TF-IDF + logistic regression, and an LLM zero-shot.

## Where it runs

- **POC for the hackathon**: one EC2 host with Docker Compose ([`infra/README.md`](../infra/README.md)).
- **Target production architecture**: ECS, RDS PostgreSQL, Cognito, WAF, private networking ([`architecture.md`](architecture.md)).
- **How the POC maps to production**, piece by piece: [`poc_to_prod.md`](poc_to_prod.md).

## Known limitations to state up front

- The complaints table has no transaction id, and `origin_interaction_id` is always null. So historical disputes cannot be tied to specific transactions or calls: the baseline is by category and customer, not by transaction.
- The dispute policy (windows, limits, required questions) is synthetic and labeled as such. It does not reproduce any real bank's rules.
- Spanish only in the data; Portuguese is generated.
- Satisfaction scales in the data are truncated (`known_issues.md`), so CSAT/NPS baselines are biased.

**Agent behavior** (found by the live dev runs of 2026-10-04)

- Confirmation is classified by the model (`prompts/agent.confirm.j2`). Only a "yes" can act, bounded by code: a reply with hedge words ("no", "pero", "mejor") never confirms, and a reply that is only "no" is decided by code. A model misreading therefore cannot start an action, but it can fail to escalate. Declines longer than a plain "no" still depend on the model: 240 of 240 direct probe calls were read correctly, a small sample. The card-block offer still opens with the D06 reason ("si no hiciste esta compra…"), a double negative that made the model read a plain "no" as "yes" in 5 of 60 probe calls before the code decided it.
- A reply that fails the speech checks twice is replaced by a code-written sentence on the paths that have a fallback: after a write, D04, the settled case, and clarification with candidates. The clarification after a "no" at the transaction question has none, so a refused reply there still ends in HTTP 503. This is read from the code and was not seen in a live run.
- `api/chat.py` maps any `ValueError` raised during a turn to HTTP 400 with the exception text. A model or parsing failure that is not handled upstream would reach the customer that way.

**Evaluation**

- The live runs use the dev split only: 16 runnable generated draft cases, 3 trials each, `gpt-6-luna`, an isolated SQLite bank. The runner's own summary marks the safety checks as partial: `ungrounded_fact`, `wrong_language` and `followed_injected_instruction` are not graded in this mode. Replies were not held out and the test split was not used. Intervals are wide: 48 of 48 is 92.6%-100%.
- The val comparison ("always send to an agent" against Minsky) is offline with scripted understanding, and its labels come from the same `decide()` the system runs.
- Only the run on `96f4ab3` has its summary and metadata versioned (`evals/reports/2026-10-04-live-dev-96f4ab3-*.json`). `evals/runs/` is git-ignored, so the 44/48 on `a3b3577` and the 48/48 on `25ffd8d` can be checked only on the machine that ran them. The ledger (`evals/reports/2026-10-02-live-budget-ledger.json`) lists every paid attempt; the classifier probes carry upper bounds because their usage was not captured.
- Token prices in `evals/model_prices.py` cover the Standard service tier only.

**Model path and deployment**

- Serving calls the interim provider directly (ADR 0008), a deviation from rule 6 of `AGENTS.md`. Moving to Bedrock is not a configuration change: in the second AWS account no model was invocable on 2026-10-04 (`gpt-6-luna` is denied; Claude needs the Anthropic use-case form), and the client makes OpenAI Responses API calls (`responses.parse`) whose support on Bedrock's OpenAI-compatible endpoint has not been verified here. The comment in `backend/src/minsky_api/config.py` that describes a switch by base URL and model id should be corrected.
- The S3 lake (bronze, silver, gold) was rebuilt from the organizer source in the second AWS account on 2026-10-04; `bank.transactions` has 4,425,008 rows, the count ADR 0012 recorded. The decision is recorded in ADR 0014, which supersedes the data placement of ADR 0012. Bronze holds 12 of the 13 source tables: `digital_events` was not ingested, so it is not a complete copy of the source.
- `MINSKY_TEST_SESSIONS` credentials minted for the demo must expire after 2026-10-16, the date the link has to stay up.

## Stack

Proposed in ADR 0006:
- **Backend:** FastAPI;
- **Frontend:** Next.js, one app with `/chat` and `/console`;
- **Database:** PostgreSQL;
- **Local and demo runtime:** Docker Compose;
- **IaC:** OpenTofu;
- **Models:** Bedrock.

How it runs locally, in the demo and in production: [`infra/README.md`](../infra/README.md). Code layout: the repository map in the [README](../README.md).
