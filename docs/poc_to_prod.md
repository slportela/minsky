# POC → production mapping

> The hackathon POC runs on **one EC2 host with Docker Compose**. It is a demo environment, **not the final architecture**. The target is [`architecture.md`](architecture.md). This page says, for each piece, what the POC uses, what production uses, which best practices apply, and what changes in our code (ideally only configuration).

## At a glance

```
 POC (1 EC2, compose)                         PRODUCTION (proposed)
 ────────────────────                         ─────────────────────
 CloudFront + caddy ───────────────────────▶   Route 53 + CloudFront + WAF + ALB + ACM
 web container ───────── same image ──────▶   ECS Fargate service "web" (≥2 tasks, autoscaling)
 api container ───────── same image ──────▶   ECS Fargate service "api" (≥2 tasks, autoscaling)
 (in-process jobs) ───────────────────────▶   SQS + DLQ + ECS "worker" service
 postgres container ──────────────────────▶   RDS PostgreSQL Multi-AZ + RDS Proxy
 trace tables + OTel (+ Phoenix, optional) ▶   same tables + OTel → CloudWatch/X-Ray (+ LLM UI if needed)
 test sessions + OTP mock ────────────────▶   Cognito (customer pool, staff pool → bank IdP)
 .env on the host ────────────────────────▶   Secrets Manager + KMS; SSM Parameter Store
 instance role (one for everything) ──────▶   one IAM task role per service, least privilege
 default VPC, public instance ────────────▶   dedicated VPC, 3 AZs, private + isolated subnets
 Bedrock over the internet (TLS) ─────────▶   Bedrock through a VPC interface endpoint
 make pipeline on a laptop ───────────────▶   EventBridge Scheduler → ECS task (ingest + dbt)
 container logs on disk ──────────────────▶   CloudWatch Logs + X-Ray + alarms, CloudTrail
 manual deploy over SSM ──────────────────▶   CI/CD: build → scan → eval gate → blue/green
 EBS snapshots ───────────────────────────▶   RDS PITR + cross-region snapshot copies
```

## Component by component

| Component | POC | Production | Best practices | Code change |
|---|---|---|---|---|
| Entry / TLS | CloudFront default certificate → private HTTP Caddy (ADR 0010); direct-host mode retained | CloudFront + WAF + ALB (ACM certificates), Route 53 | WAF managed rules + rate limiting; TLS 1.2+; HSTS; ALB access logs to S3 | None |
| Web | `web` container | ECS service, same image | ≥2 tasks across AZs; health checks; static assets cached at CloudFront | None |
| API | `api` container | ECS service, same image | Stateless; ≥2 tasks; autoscaling on requests/CPU; graceful shutdown; per-service task role | None (config) |
| Async jobs | In-process | SQS + DLQ, `worker` service | Idempotent consumers; DLQ alarms; visibility timeout > job time | Queue adapter: in-process → SQS |
| **Database** | `postgres` container, volume on EBS | **RDS PostgreSQL Multi-AZ + RDS Proxy** | See the next section | `DATABASE_URL`; migrations as a deploy step |
| Observability | Trace/audit tables in postgres + OpenTelemetry; optional Phoenix container | Same tables; OpenTelemetry via ADOT → CloudWatch + X-Ray; LLM UI (Phoenix/Langfuse) on ECS if needed | PII redaction before export; SLO alarms; trace retention policy | Exporter endpoint (config) |
| ML tracking | Committed training reports (`ml/reports/`) | Same reports; MLflow on ECS (RDS + S3) if experiments grow | Every served model traced to its report (git SHA, data snapshot, metrics) | None, or the tracking URI |
| Identity | Test sessions + simulated OTP | Cognito (customers: MFA/step-up; staff: federation to the bank IdP) | Short-lived tokens; step-up before sensitive actions; no identity from conversation text | Identity adapter: mock → Cognito |
| Secrets | `.env` on the host | Secrets Manager (rotation) + KMS; SSM for config | Nothing secret in images, env files or logs | Read from the environment as today; injected by ECS |
| Network | CloudFront mode: dedicated VPC, private instance, CloudFront-only port 80, NAT egress; single AZ | Dedicated VPC, 3 AZs; public (ALB, NAT), private (tasks), isolated (database) subnets | Security groups by role; no public IPs on tasks or database; VPC endpoints; flow logs | None |
| Models | Bedrock via the instance role, over the internet (TLS) | Bedrock via a VPC endpoint; IAM limited to the allowed models; inference profiles | Quota monitoring; circuit breaker + fallback model; invocation logging disabled or redacted | None (config) |
| Lake | Our S3 bucket (bronze/silver) | Same bucket design, per-environment buckets | SSE-KMS, versioning, lifecycle, Block Public Access, access logs; Glue catalog | None |
| Pipeline | `make pipeline` on a laptop | EventBridge Scheduler → ECS task; freshness checks + alarms | Idempotent runs (already: manifest + verified copy); failures alarm; retries bounded | None (same code, scheduled) |
| Deploy | `git pull` + `docker compose up` over SSM | CI/CD: build → ECR scan → eval gate → blue/green (CodeDeploy) | Immutable image tags; automatic rollback on alarms; OpenTofu with remote state and locking | None |
| Security posture | Instance role, IMDSv2, no SSH | + GuardDuty, Security Hub, Config, org CloudTrail, Inspector | Separate accounts per environment; SCPs | None |
| Backups / DR | EBS snapshots | RDS PITR + snapshot copies to a second region; IaC to rebuild | Proposed RPO ≤ 5 min, RTO ≤ 1 h; test restores | None |

## The database: postgres container → RDS PostgreSQL

In the POC one `postgres` container holds two schemas:
- `bank.*`: read models loaded from the lake;
- `cases.*`: our writes (disputes, handoffs, sessions, audit log).

In production:

| Concern | Production practice |
|---|---|
| Engine | RDS PostgreSQL, same major version as the POC image; Multi-AZ. Aurora PostgreSQL if we need faster failover, many readers or storage autoscaling (the design is the same) |
| Availability | Multi-AZ with automatic failover to a standby in another AZ; a read replica for read models or analysts if load requires it |
| Network | Isolated subnets, not publicly accessible; security group allows only the api/worker tasks and RDS Proxy |
| Connections | **RDS Proxy** between ECS and the database: pools connections when tasks scale out, shortens failover |
| Credentials | IAM database authentication or Secrets Manager with automatic rotation; no static passwords |
| Roles | `app_read` (SELECT on `bank.*`), `app_write` (`cases.*`), `migrator` (DDL, used only by the migration task), `analyst` (read replica only) |
| Tenant isolation | **Row-Level Security** on customer-scoped tables, keyed by a session setting the API sets from the token: a second line behind the tool-layer checks |
| Encryption | KMS customer-managed key at rest; TLS enforced (`rds.force_ssl`) |
| Backups | Automated backups with point-in-time recovery (7-35 days); snapshots copied to a second region; restore tested |
| Migrations | Alembic, run as a one-off ECS task before each deploy; backward-compatible changes only (expand → migrate → contract) |
| Read models | Loaded by the pipeline task after each run; served from the primary (or a read replica under load); freshness recorded in a table and alarmed |
| Audit log | Append-only table (no UPDATE/DELETE grants) plus daily export to S3 Object Lock |
| Monitoring | Performance Insights; `pgaudit`; slow-query logging; alarms on CPU, connections, free memory, replica lag, storage |
| Operations | Deletion protection; maintenance window; minor-version auto-upgrade; parameter groups in IaC |
| Retention | Per-table policy (transcripts short, cases and audit per regulation), enforced by scheduled jobs |

**What must hold in the POC so this is only a configuration change:**
- one `DATABASE_URL`;
- the `bank`/`cases` schemas and the roles above created by migrations, not by hand;
- the RLS policies written and tested in the POC too;
- no reliance on superuser features.

## What the POC does not have (say it in the demo)

| Missing | Consequence |
|---|---|
| High availability | One instance, one AZ: an instance failure means downtime and a restore from snapshot |
| Autoscaling | Capacity is fixed by the instance size |
| Automated deploys and eval gate in CI | Deploys are manual |
| Managed identity | Test identity only |
| Private networking | Public instance; no VPC endpoints |
| Security posture services | No WAF, GuardDuty or Security Hub |
| Operations | No tested disaster recovery; no on-call alarms |

## Rules to keep the path open

- 12-factor: all configuration from the environment; no host paths or hostnames in code.
- Adapters for identity, queue and model access; the POC implementations are the simple ones.
- Migrations own the database schema, roles and RLS.
- Images are built once and promoted, never rebuilt per environment.
