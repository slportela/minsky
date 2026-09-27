# minsky

Factored AI & Data Hackathon 2026: an **AI-first banking customer-service system** for **transaction-dispute intake** on the LATAM Bank dataset (synthetic; Mexico, Colombia, Argentina; 2023-06-17 to 2026-06-17). Submission deadline 2026-10-05.

## Read in this order

```
  1. docs/challenge.md       what we must build and how it is scored (one page)
          │
  2. docs/solution.md        what we build for disputes: flow, AI vs. code, components
          │                         (checked against docs/requirements.md: every requirement → status)
  3. docs/architecture.md    target production architecture (proposed) · docs/poc_to_prod.md: POC → prod
          │
  4. AGENTS.md               how we work: rules, commands, conventions (agents and humans)
          │
  5. docs/evals.md           how we prove it works
          │
  6. infra/README.md         how the POC runs: local and the AWS demo (not the production design)
          │
  7. docs/adr/               decisions and why · docs/dispute_policy.md · docs/known_issues.md
```

## Repository map

```
minsky/
├── README.md · AGENTS.md          entry point · rules for agents and humans (CLAUDE.md imports AGENTS.md)
├── compose.yaml                   the whole stack on one machine (compose.local.yaml adds laptop settings)
├── Makefile                       every command: make help
│
├── backend/                       FastAPI service (Python, uv workspace member)
│   └── src/minsky_api/
│       ├── api/                   HTTP routers: thin, no decisions
│       ├── agent/                 orchestrator: the dispute state machine (owns every decision)
│       ├── policy/                pure policy rules D01-D09 (no I/O), unit-tested rule by rule
│       ├── tools/                 mock core banking: session-scoped, permission-checked, audited
│       ├── router/                learned intent/reason classifier (inference)
│       ├── llm/                   Bedrock access: pinned models, prompts from prompts/, retries
│       ├── identity/              test sessions + simulated OTP (Cognito in production)
│       ├── guardrails/            input signals · output grounding and language checks
│       ├── store/                 postgres: bank.* read models, cases.* writes
│       └── observability/         OpenTelemetry + our trace/audit tables
├── frontend/                      Next.js: /chat (customers) · /console (agents)
├── ml/                            router training and L1 evaluation (the learned component)
├── pipeline/                      data: organizer S3 → bronze → silver (dbt-duckdb) → our S3
│   ├── data_dictionary.py         the contract for silver
│   ├── transform/                 dbt project (silver generated from the dictionary)
│   └── notebooks/                 evidence behind docs/known_issues.md
├── evals/                         eval harness: case schema, set checks, metrics, cases/{dev,val,test}
├── prompts/                       versioned prompts
├── tests/                         eval-harness tests (backend tests in backend/tests; `make test` runs both)
├── infra/
│   ├── caddy/                     TLS + routing for local and demo
│   └── tofu/                      OpenTofu: modules/ + envs/demo (POC; production: docs/architecture.md)
├── docs/                          challenge · requirements · solution · architecture · poc_to_prod ·
│                                  evals · dispute_policy · known_issues · adr/
├── kickoff_docs/                  organizer documents (read-only)
└── .claude/ · .github/            Claude Code settings and skills · PR template
```

## Status

| Built | Next |
|---|---|
| Data pipeline to silver; data issues documented | Evidence for disputes from the data; gold read models |
| Dispute policy (code + tests + doc) | Tools, identity, orchestrator, LLM steps, guardrails |
| Backend and frontend skeletons, compose stack, POC IaC (verified locally: all services healthy, `tofu validate` passes) | Chat and console UIs, trace view, POC deployment |
| Target architecture and POC → production map (proposed) | Production IaC modules (plan only), if time allows |
| Eval strategy, case schema, set checks, metrics, 5 example cases | Eval runner, customer simulator, graders, router ladder |

## Quick start

Needs: `uv`, Node.js 22+, Docker, OpenTofu (only for the AWS demo).

```bash
cp .env.example .env   # organizer (read-only) credentials, our bucket, AWS_PROFILE (Bedrock access)
make setup             # Python + frontend dependencies, git hooks
make ci                # lint, format, types, all tests, eval-case checks, frontend types: must pass before any PR
make up                # full stack → https://localhost  (health: https://localhost/api/health)
make down              # stop it (data is kept)
make help              # all targets
```

- `make up` serves the web app and API through Caddy with a self-signed certificate. It exposes Postgres on `localhost:5433` (`POSTGRES_HOST_PORT`).
- Optional trace viewer: `docker compose --profile observability up -d phoenix` → http://localhost:6006 (set `OTEL_EXPORTER_OTLP_ENDPOINT=http://phoenix:6006` in `.env`).

## Data pipeline

```
 organizer S3 ──bronze──▶ our S3 /bronze ──mirror──▶ data/bronze ──silver──▶ data/lake/silver ──publish──▶ our S3 /silver
 (read-only)   verified    + run manifest   (local)                (dbt-duckdb,                (local)
               copy                                                 ~90 s rebuild)
```

- `make pipeline` runs all four steps. Bronze, mirror and publish are incremental.
- dbt reads local files: reading ~1k small files from S3 takes minutes per table.
- Silver models and `_silver.yml` are generated from `pipeline/data_dictionary.py` by `pipeline/transform/generate_silver.py`. Don't edit them by hand.
- Test policy:
  - **error** = what silver guarantees (primary keys, casts);
  - **warn** = source issues against the dictionary, also flagged per row in `_dq_issues`.
- `make docs` serves dbt docs with lineage.
