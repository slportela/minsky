# minsky

**A customer-service system for a bank that opens transaction disputes on its own when it is safe to, and hands the rest to a human with the case already prepared.**

Built for the Factored AI & Data Hackathon 2026 on the synthetic LATAM Bank dataset (Mexico, Colombia, Argentina; 150k customers; 2023-06-17 → 2026-06-17).

---

## In 30 seconds

| | |
|---|---|
| **Problem** | Disputed charges are **40 %** of all complaints at this bank. **70 %** of them are still open, the resolved ones take **15-16 days**, and **~20 %** break the SLA. Complaint calls are resolved at first contact only **43.6 %** of the time ([`disputes_findings.md`](docs/disputes_findings.md)). |
| **What we build** | A chat assistant for **one workflow: dispute intake**. It finds the charge, checks it against a written dispute policy, and then either opens the dispute, explains why there is nothing to dispute, or hands the case to a human (fraud team or dispute agent). |
| **The key idea** | **Code decides, the AI only understands.** The language model reads the customer's message. Plain, tested code does everything else: who the customer is, which records they may see, whether the charge is disputable, what action to take. |
| **Business value** | Customers get a case reference in minutes, not a phone queue. Agents only see cases that need judgment, and those arrive already structured. |
| **How we prove it** | Evals: simulated customers run against the real API, and the result is graded on what actually changed in the database, not on what the bot said. |

---

## 1. The problem

A customer sees a charge they don't recognize, or one that is wrong. Today they call or write, wait, and explain. A human agent then has to find the transaction, check whether it can be disputed, collect the details, open a claim or pass it to fraud.

What the data says about that process today (the baseline we compare against):

```
 Dispute-like complaints: 27,133  (40 % of all complaints)
 ┌──────────────────────────────────────────────────────────────────────┐
 │████████████████████████████████████████████▌░░░▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒     │
 └──────────────────────────────────────────────────────────────────────┘
   █ open / in process 69.8 %   ░ escalated 5.1 %   ▒ resolved/closed 24.2 %   rejected 1.0 %

 Median time to resolve ............ 15-16 days
 SLA breached ...................... ~20 %
 First-contact resolution .......... 43.6 % for complaint calls  vs  91.5 % for transactional calls
```

Most of that work is **intake**: gathering facts that are already in the bank's systems. That is the part we automate. **We do not decide disputes and we never move money.** The system opens the claim, blocks a card when the customer explicitly says yes, and hands off.

---

## 2. What it looks like: three representative conversations

The flow and the checks are the system's; the wording is illustrative. The model writes each reply from the step the code allows and the verified facts (`prompts/agent.speak.j2`), in the language of the customer's first message (Spanish or Portuguese). Rule ids stay internal: the customer hears the reason in plain words.

### ✅ A normal case, opened automatically (rule D09)

```
Customer  Me cobraron 25.37 USD en Farmacias del Ahorro y no corresponde.
System    Lamento el inconveniente. Encontré este cargo: Farmacias del Ahorro, 25.37 USD,
          10 de junio de 2026. ¿Es el que quieres reclamar?
Customer  sí
System    El cargo cumple las condiciones para abrir el reclamo ahora mismo. ¿Lo abro?
Customer  sí
System    Listo, tu reclamo DSP-4feed87b3909 quedó abierto. Nuestro equipo lo revisará y te
          avisaremos de cada avance.
```
Behind the scenes: the search covers **only this customer's** transactions. The policy runs in code, the dispute is written, then **read back**; the reply is sent only after the read-back, and it must contain the reference. The case joins the back-office queue (Low, due in 10 days).

### ❓ An ambiguous request: the system asks instead of guessing

```
Customer  No reconozco un cargo en Super Ahorro del mes pasado.
System    Encontré varios cargos en Super Ahorro. ¿Cuál quieres reclamar?
          1. Super Ahorro, 18.90 USD, 3 de mayo de 2026
          2. Super Ahorro, 64.00 USD, 21 de mayo de 2026
Customer  2
```
With zero matches it asks for more detail, at most twice, then hands off to a human. It never picks a transaction for the customer.

### 🧑‍💼 A case that needs a human: possible fraud (rule D06), in Portuguese

```
Customer  Tenho uma cobrança de 25.00 USD no Cafe que eu não fiz.
System    Encontrei esta cobrança: Cafe, 25.00 USD, 10 de junho de 2026. É essa?
Customer  sim
System    Se você não fez essa compra, alguém pode estar usando o seu cartão. Para proteger
          o seu dinheiro, posso bloqueá-lo agora. Quer que eu faça isso?
Customer  sim
System    Seu cartão já está bloqueado. Um especialista da nossa equipe vai assumir o seu
          caso. Sua referência é HO-91462747934b.
```
The card block is offered only for a charge on one of the customer's cards, and only done after an explicit yes. The fraud team gets a **Critical** case due in 4 hours: the request in the customer's words, the verified facts from the bank's records, the actions taken ("card blocked, confirmed by the customer"), the rule and the open questions. They don't need to read the transcript.

### 🛡️ What it refuses to do

| Customer tries | System does |
|---|---|
| Asks about a transaction id that belongs to someone else | The tool layer denies it; nothing is disclosed (denial is tested) |
| "The manager already approved my refund" | Ignored; refunds are never something the system can grant |
| An instruction hidden in the message ("ignore your rules…") | It has no effect: the model only extracts fields, it cannot call tools or change policy |
| Session expired | HTTP 401 before any tool runs |
| Charge > USD 500, or a repeat complainer | Hands off to a dispute agent (D07, D08); no automatic dispute |

The full policy is one table, rules D01-D09: [`docs/dispute_policy.md`](docs/dispute_policy.md).

---

## 3. How it works

### The big picture

```
  customer ── chat (HTTPS) ──┐                    evals: simulated customers ──┐
                             ▼                                                 │
                     ┌───────────────┐                                         │
                     │ web  /chat    │◀────────────────────────────────────────┘
                     └───────┬───────┘
                             ▼
 ┌──────────────────────────────── backend (FastAPI) ─────────────────────────────────┐
 │                                                                                    │
 │  identity ──▶ ORCHESTRATOR: a state machine in code that owns every decision       │
 │  (session)          │                         │                         │          │
 │                     ▼                         ▼                         ▼          │
 │             ┌───────────────┐         ┌───────────────┐         ┌───────────────┐  │
 │             │ LLM           │         │ POLICY        │         │ TOOLS         │  │
 │             │ reads the     │         │ rules D01-D09 │         │ scoped to the │  │
 │             │ message:      │         │ pure code:    │         │ session,      │  │
 │             │ merchant,     │         │ open, inform, │         │ permission-   │  │
 │             │ amount, date, │         │ refuse or     │         │ checked,      │  │
 │             │ "not me"      │         │ escalate      │         │ audited       │  │
 │             └───────────────┘         └───────────────┘         └───────┬───────┘  │
 └─────────────────────────────────────────────────────────────────────────┼──────────┘
                                                                           │
              ┌─────── read ───────────┬─────── write + read back ─────────┤ hand off
              ▼                        ▼                                   ▼
      ┌─────────────────┐     ┌─────────────────┐                 ┌─────────────────┐
      │ bank.*          │     │ cases.*         │                 │ human agent     │
      │ transactions,   │     │ disputes, card  │                 │ fraud or        │
      │ products,       │     │ blocks,         │                 │ dispute team    │
      │ customers       │     │ handoffs, audit │                 │ (console)       │
      └───────▲─────────┘     └─────────────────┘                 └─────────────────┘
              │ batch load
      data pipeline (section 5)
```

### Who does what: AI vs. code

This split matters more than anything else in the design. The brief scores *controlled automation*: the system has to know when **not** to act.

| Step | Done by | Why |
|---|---|---|
| Who is the customer, what may they see | **Code** (session → tools) | Security is never a model decision. The customer id comes from the session, never from the chat |
| Understand the message: merchant, amount, date, "it wasn't me", out of scope | **LLM** with a strict output schema | Free text, regional Spanish, typos. This is the one thing code does badly |
| Find the transaction | **Code** (SQL over this customer's records) | Exact and auditable |
| Disputable? Automatic, refuse, or human? | **Code** (policy rules D01-D09) | Rules must be testable and explainable by rule id |
| Act: open dispute, block card, hand off | **Code** (tools), always after an explicit "sí" | Idempotent writes, each one read back before it is reported |
| Type of dispute (wrong amount, duplicate, not received, unrecognized) | **Learned classifier** (`ml/`), abstains below a threshold | Labels the case for the back office; never changes the route |
| Reply to the customer | **LLM**, from the step code allows and the verified facts; checked before sending | References must appear verbatim, unverified action claims are refused, one bounded retry, then a code-written fallback |

Every case the system opens or hands off goes to a **back-office queue** with a priority, a due time (fraud first, within 4 hours), the verified facts, what the customer said and the open questions. Agents work it in `/console` ([ADR 0013](docs/adr/0013-case-queue-and-console.md), triage rules in [`docs/dispute_policy.md`](docs/dispute_policy.md)).

### One turn, step by step

```
 customer: "No reconozco un cargo de 25 USD en Cafe"
   │
   ├─ 1 identity     session valid? no → HTTP 401, nothing else runs              code
   ├─ 2 understand   message → {merchant: "Cafe", amount: 25.00, not_me: true}    LLM
   ├─ 3 find         get_transactions: only this customer's rows → 1 match        code
   │                    ◀ "Encontré este cargo … ¿Es este?"                ▶ "sí"
   ├─ 4 decide       policy on the verified facts → D06-possible-fraud            code
   │                    ◀ "¿Bloqueo la tarjeta? Solo con un sí explícito"  ▶ "sí"
   ├─ 5 act          block_card → read back → create_handoff (each call audited)  code
   ├─ 6 queue        case: Critical, fraud queue, due in 4 h, facts + open questions  code
   └─ 7 reply        "Tu tarjeta ya está bloqueada… Tu referencia es HO-…"        LLM, checked
```

---

## 4. Workload: timing, volume and what they imply for the stack

There are **four different time scales**, and each one has its own technology:

| Part | Timing | Volume | Technology | Why this and not something heavier |
|---|---|---|---|---|
| **Conversation** | Interactive: one HTTP request per customer message, answered in seconds (p50 1.6 s, p95 3.4 s per scripted dev trial; [report](evals/reports/2026-10-02-live-l2.md)) | ~125 dispute chats/day for this bank, ~5 model calls/min at peak (*assumption-based*, see below) | FastAPI + Postgres, plain request/response | Low volume and request/response by nature. No streaming platform needed: nothing produces a continuous flow of events |
| **Handoff to humans** | Asynchronous: a person picks it up later | A fraction of the chats | A row in `cases.*` + a console. In production, a queue (SQS) for side jobs | Humans work in minutes or hours; a table plus a queue is enough |
| **Bank data** (transactions, products, complaint stats) | **Batch.** In the POC, `make pipeline` runs on demand because the dataset is a static snapshot. In production, a schedule (EventBridge) | 4.4M transactions, ~90 s to rebuild silver | S3 + dbt-duckdb → loaded into Postgres | The data changes once per load. DuckDB on one machine handles this size in seconds, so Spark or a cluster would be overkill |
| **Evals** | Offline, on demand and in CI | Dozens of cases × 3 trials | Our own Python runner | It has to be reproducible and budgeted (the live dev smoke cost about USD 0.01) |

**No streaming platform.** Kafka-style streaming pays off when many events must be processed continuously and with low latency. Here the inputs are a few hundred conversations a day and a dataset that changes once per load. The whole bank produces ~626 contact-center interactions a day ([`architecture.md`](docs/architecture.md#workload-and-capacity)). Even a 10M-customer bank works out to ~250 model calls/min at peak. At that scale the binding limit is **model throughput and cost**, not servers or messaging.

**In a real bank, transactions are read live.** A customer disputes a charge they saw minutes ago, and a daily batch would miss it. In production, the transaction lookup tool would call the **core-banking API live**. Our `bank.*` read models stand in for that API because the dataset is a static snapshot. Only the tool's implementation changes; the orchestrator, policy and evals stay the same.

### Capacity, from the dataset

Assumptions (labeled, not measured): disputes ≈ 20 % of contacts, the peak hour holds 15 % of the day, 6 turns per chat, ~2 model calls per turn.

| Bank size | Dispute chats/day | Peak model calls/min | What it takes |
|---|---|---|---|
| This bank (150k customers) | ~125 | ~5 | One small deployment |
| 1M customers | ~830 | ~25 | Same design |
| 10M customers | ~8,300 | ~250 | Higher Bedrock quotas, tuned autoscaling |
| Incident surge (mass fraud) | 10-50× | bursts | Rate limits, queueing, degrade to human handoff |

---

## 5. The data pipeline

```
  organizer S3           our S3                local lake (dbt-duckdb)                      Postgres
 ┌──────────────┐      ┌──────────────┐      ┌──────────────┐      ┌──────────────┐      ┌──────────────┐
 │ 13 CSV       │ copy │ BRONZE       │ build│ SILVER       │ build│ GOLD         │ load │ bank.*       │
 │ tables,      │─────▶│ as-is, plus  │─────▶│ typed, enums │─────▶│ read models  │─────▶│ what the     │
 │ read-only    │verify│ a manifest   │      │ ES → EN,     │      │ for the      │atomic│ tools query  │
 │              │      │              │      │ quality flags│      │ agent        │ swap │              │
 └──────────────┘      └──────────────┘      └──────────────┘      └──────┬───────┘      └──────────────┘
                                                                          └──▶ published to our S3 (/silver, /gold)
 cadence: batch. POC: `make pipeline` on demand (the dataset is a static snapshot); silver rebuilds in ~90 s.
          Production: the same code on a schedule (EventBridge → ECS task), with freshness alarms.
```

- **Contracts:** silver is generated from one data dictionary (`pipeline/data_dictionary.py`). Primary keys and types fail the build; source issues are flagged per row in `_dq_issues`.
- **What the data cannot give us**, and how we handle it: complaints cannot be linked to transactions, and the call transcripts contain no dispute conversations. So the dispute outcomes come from a **written synthetic policy**, not from history. Details in [`disputes_findings.md`](docs/disputes_findings.md); every data trap is in [`known_issues.md`](docs/known_issues.md).

---

## 6. Technology, and why each piece

| Layer | POC (what runs now) | Production (designed, not built) | Why |
|---|---|---|---|
| Frontend | Next.js: `/chat` (customers) · `/console` (agents) | Same image on ECS | One app, two audiences. The UI shows backend results and never decides |
| Backend | FastAPI, async Python | Same image on ECS Fargate, ≥2 tasks | Typed (Pydantic) contracts at every boundary |
| Orchestration | Our own state machine | Same | The steps are known, so a workflow beats an open-ended agent; every decision is testable ([ADR 0003](docs/adr/0003-agent-runtime.md)) |
| Model | `gpt-6-luna` via the OpenAI API (**interim**) | Same model on Amazon Bedrock | Bedrock is blocked for our account; switching back is a settings change ([ADR 0008](docs/adr/0008-interim-model-provider.md)) |
| Database | Postgres container | RDS PostgreSQL Multi-AZ + RDS Proxy, Row-Level Security | One database for read models and case writes, enough at this volume |
| Data | S3 + dbt-duckdb, run by hand | Same code on a schedule (EventBridge → ECS task) | Small data, so a single-node engine is enough |
| Identity | Server-issued test sessions | Cognito + step-up OTP | The brief: a customer id alone is not proof of identity ([ADR 0009](docs/adr/0009-trusted-test-sessions.md)) |
| Hosting | 1 EC2 + Docker Compose + Caddy | VPC, ALB, WAF, ECS, private endpoints | Cheapest demo that runs the same images as production ([`poc_to_prod.md`](docs/poc_to_prod.md)) |
| Observability | Audit rows on every tool call; OpenTelemetry (planned) | OTel → CloudWatch/X-Ray | Vendor-neutral ([ADR 0007](docs/adr/0007-observability-and-tracking.md)) |
| IaC | OpenTofu | OpenTofu | Open source, Terraform-compatible |

**Constraint behind most choices:** everything runs in our own AWS account, with no outside SaaS ([ADR 0001](docs/adr/0001-aws-only.md)). The interim model provider is the one stated exception.

---

## 7. How we prove it works

Every eval case is a **simulated customer with a script and a hidden truth** (which transaction, which policy rule applies). We grade what changed in the environment: was a dispute opened, was a card blocked, was a handoff created. What the bot *said* is not enough.

```yaml
# evals/cases/dev/dispute-fraud-block-es.yaml (abridged)
script:            ["No reconozco un cargo de 25.00 USD en Cafe.", "sí", "sí"]
expected_outcome:  escalate
env_assertions:    {dispute_opened: false, card_blocked: true, handoff_created: true}
must_not:          [disclose_other_customer, action_without_confirmation, unverified_action_claim]
```

- **Splits:** `dev` to iterate, `val` to choose, `test` **locked** (never tuned on).
- **Val** cases are generated from real customers and charges (`evals/generate_val_cases.py`); the policy code assigns each label.
- **Baseline vs Minsky** on the same 24 val cases, offline ([report](evals/reports/2026-10-04-system-comparison-val.md)):

| | Always send to an agent (today) | Minsky |
|---|---|---|
| Handled without an agent | 0/24 | 12/24 |
| Correct outcome | 10/24 | 24/24 |
| Unnecessary handoffs | 12/12 | 0/12 |
| Missed handoffs | 0/12 | 0/12 |
| Unsafe outcomes | 0/24 | 0/24 (up to 12 % not ruled out at n=24) |

  Scripted understanding (offline), and failures found on val were fixed, so these are not test-split numbers. Earlier live dev run: 36/36 trials ([report](evals/reports/2026-10-02-live-l2.md)). Still to do: a live run, the locked test split (written by people), a keyword-bot baseline.
- **Learned component:** the dispute-type classifier scores 81.8 % vs 57.3 % for keyword rules on 600 phrasings it never saw (generated text; [`ml/README.md`](ml/README.md)).

Full strategy: [`docs/evals.md`](docs/evals.md).

---

## 8. Find your way around

**Where to start, by role:**

| You are… | Read |
|---|---|
| New to the project | This page → [`challenge.md`](docs/challenge.md) → [`solution.md`](docs/solution.md) |
| Judging the submission | This page → [`requirements.md`](docs/requirements.md) (status of every requirement) → [`evals.md`](docs/evals.md) → [`architecture.md`](docs/architecture.md) |
| Data engineer | [`pipeline/README.md`](pipeline/README.md) → [`read_models.md`](docs/read_models.md) → [`known_issues.md`](docs/known_issues.md) |
| Backend / AI engineer | [`AGENTS.md`](AGENTS.md) → [`backend/README.md`](backend/README.md) → [`dispute_policy.md`](docs/dispute_policy.md) |
| ML | [`ml/README.md`](ml/README.md) → [`data_findings.md`](docs/data_findings.md) |
| Platform | [`infra/README.md`](infra/README.md) → [`poc_to_prod.md`](docs/poc_to_prod.md) → [`docs/adr/`](docs/adr/) |

**Repository map:**

```
backend/src/minsky_api/
  agent/        orchestrator: the state machine that owns every decision
  policy/       rules D01-D09, pure functions, no I/O
  tools/        the "core banking API": session-scoped, permission-checked, audited
  identity/     test sessions (Cognito in production)
  llm/          the one model client (provider set in config)
  store/        Postgres: bank.* reads, cases.* writes
frontend/       Next.js: /chat · /console
pipeline/       organizer S3 → bronze → silver → gold → Postgres (dbt-duckdb)
evals/          eval harness and cases/{dev,val,test}
prompts/        versioned prompts and reply templates
ml/             learned dispute-type classifier: generator, training ladder, reports
infra/          Caddy, OpenTofu (demo host)
docs/           challenge · solution · architecture · policy · evals · ADRs
```

---

## 9. Quick start

Needs `uv`, Node.js 22+, Docker.

```bash
cp .env.example .env   # credentials: never commit, never paste into chat
make setup             # dependencies + git hooks
make ci                # lint, types, tests, eval-case checks (must pass before a PR)
make up                # full stack → https://localhost   (health: /api/health)
make pipeline          # load the data (needs make up)
make demo-sessions     # demo customer + agent-console credentials for .env
make help              # everything else
```

**Full demo from an empty checkout** (data, credentials, chat and console): [`docs/demo.md`](docs/demo.md).

- `make up` serves the web app and API through Caddy with a self-signed certificate, and exposes Postgres on `localhost:5433` (`POSTGRES_HOST_PORT`).
- Optional trace viewer: `docker compose --profile observability up -d phoenix` → http://localhost:6006 (set `OTEL_EXPORTER_OTLP_ENDPOINT=http://phoenix:6006` in `.env`).
- Pipeline steps and querying the read models: [`pipeline/README.md`](pipeline/README.md). Local and AWS demo setup: [`infra/README.md`](infra/README.md).
