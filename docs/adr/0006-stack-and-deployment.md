# 0006. Stack, POC deployment and target architecture

- Status: proposed
- Date: 2026-09-26

## Context
We need a backend, a UI for customers and agents, a database for read models and writes, and infrastructure as code. Everything runs on AWS (ADR 0001). The demo must be cheap but reachable until 2026-10-16. We still need to show what production would look like.

## Decision
| Layer | Choice |
|---|---|
| Backend | FastAPI + Pydantic + SQLAlchemy (Python, `backend/`, a uv workspace member) |
| Frontend | Next.js (React, TypeScript, App Router), one app with `/chat` and `/console` (`frontend/`) |
| Database | PostgreSQL: `bank.*` read models loaded from the lake, `cases.*` writes |
| POC runtime (hackathon only) | Docker Compose on one EC2: caddy, web, api, postgres (+ Langfuse) |
| IaC | OpenTofu: modules + `envs/demo` (applied); production modules later (plan only) |
| Target production | ECS Fargate, Aurora PostgreSQL + RDS Proxy, CloudFront + WAF, Cognito, Bedrock via VPC endpoint (`docs/architecture.md`) |

## Consequences
- The POC is **not** the production design. The same images run on a laptop, the demo host and ECS; `docs/poc_to_prod.md` states what production adds for each component.
- The demo is single-instance: no high availability, and deploys are manual. It is documented as a limit in `infra/README.md`.
- Two languages in the repo (Python, TypeScript), each with its own lint and typecheck in `make ci`.
