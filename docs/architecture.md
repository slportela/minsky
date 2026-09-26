# Target architecture (production)

> **Status: proposed, the north star.** Nothing here is built yet, and every choice can change as we learn. What we run for the hackathon is a POC on one EC2 host ([`poc_to_prod.md`](poc_to_prod.md) maps it, piece by piece, to this design). Requirements this must satisfy: [`requirements.md`](requirements.md).

## Principles

1. **Code decides, models assist.** Policy, permissions and actions are deterministic code; models understand and phrase.
2. **Same images everywhere.** The containers built for the POC are the ones production runs; only configuration and surroundings change (12-factor).
3. **Adapters at the edges.** Identity, queue, storage and model access sit behind interfaces, so the POC uses simple implementations and production uses managed services, without rewriting the core.
4. **Private by default.** No public database, no public tasks, and no internet path to the models (VPC endpoints).
5. **Degrade to a human, never to a guess.** When models, tools or data are unavailable, the system hands off with context.
6. **Everything observable and auditable.** Every turn, model call, tool call and policy decision is traced; the audit trail is append-only.

## Environments

```
 ┌────────────┐     ┌──────────────────────┐     ┌──────────────┐     ┌──────────────┐
 │ local dev  │ ──▶ │ POC / demo           │     │ staging      │ ──▶ │ production   │
 │ laptop,    │     │ 1 EC2 + compose      │     │ same design  │     │ this document│
 │ compose    │     │ hackathon only       │     │ as prod,     │     │              │
 │            │     │ NOT the final design │     │ smaller      │     │              │
 └────────────┘     └──────────────────────┘     └──────────────┘     └──────────────┘
                     built for the hackathon      described, not built  described, not built
```

In production, each environment runs in its own AWS account under AWS Organizations, with guardrail policies (SCPs) and centralized logging.

## Deployment view

```
                                   customers                 bank staff (dispute / fraud agents)
                                       │                                   │
                                       ▼                                   ▼
                          Route 53 ─▶ CloudFront (+ AWS WAF, Shield) ─▶ static assets (S3)
                                       │ /api, pages
 ┌──────────────── VPC (3 AZs) ────────┼──────────────────────────────────────────────────────────┐
 │ public subnets                      ▼                                                          │
 │                          Application Load Balancer (TLS, ACM)            NAT (egress only)     │
 │ ─────────────────────────────────────┼──────────────────────────────────────────────────────── │
 │ private app subnets                  │                                                         │
 │        ┌──────────────── ECS Fargate (autoscaling, ≥2 tasks per service, spread across AZs) ─┐ │
 │        │  web (Next.js)       api (FastAPI)          worker (async jobs)    ADOT collector   │ │
 │        └──────────────────────┬─────────┬───────────────┬──────────────────────┬──────────────┘ │
 │                               │         │               │                      │                │
 │                   RDS Proxy ◀─┘         │           SQS (+DLQ)           Langfuse on ECS        │
 │ ────────────────────┼───────────────────┼─────────────────────────────────────────────────────  │
 │ isolated data       ▼                   │                                                        │
 │ subnets   Aurora PostgreSQL             │                                                        │
 │           (writer + reader, Multi-AZ,   │                                                        │
 │            KMS, PITR, RLS)              │                                                        │
 │                                         │                                                        │
 │ VPC endpoints: Bedrock runtime · S3 · ECR · Secrets Manager · KMS · SQS · CloudWatch · STS       │
 └─────────────────────────────────────────┼───────────────────────────────────────────────────────┘
                                           ▼
     Amazon Bedrock (models, via PrivateLink)     Cognito (customer pool + staff pool / bank IdP)
     S3 lake: bronze · silver · gold (KMS, versioned)     Secrets Manager · KMS
     EventBridge Scheduler ─▶ ECS task: ingest + dbt ─▶ gold ─▶ Aurora read models
     CloudWatch (metrics, logs, alarms) · X-Ray · CloudTrail · GuardDuty · Security Hub
     Audit log export ─▶ S3 Object Lock (compliance mode)
```

## Components

| Area | Proposed | Why | Alternatives kept open |
|---|---|---|---|
| Edge | CloudFront + WAF (managed rules, rate limits, bot control) + ACM + Shield Standard | Blocks abuse before it reaches the app; TLS everywhere | API Gateway in front of the API |
| Frontend | Next.js on ECS (server rendering) + static assets on S3/CloudFront | Same image as the POC | Static export to S3 only, if no server rendering is needed |
| API | FastAPI on ECS Fargate, stateless, ≥2 tasks across AZs, autoscaling on requests and CPU | No servers to patch; scales with traffic | EKS if the bank standardizes on Kubernetes |
| Async work | SQS + DLQ, worker service on ECS | Handoff events, summaries, online-eval sampling, off the request path | EventBridge for fan-out |
| Transactional data | Aurora PostgreSQL behind RDS Proxy | Managed HA, PITR, reader endpoint; details in [`poc_to_prod.md`](poc_to_prod.md) | RDS PostgreSQL Multi-AZ (cheaper at low scale) |
| Lake | S3 bronze/silver/gold, SSE-KMS, versioning, lifecycle, Block Public Access; Glue catalog + Athena for analysis | Cheap, durable, auditable history | Lake Formation for fine-grained access |
| Pipeline | EventBridge Scheduler → ECS task (ingest + dbt); freshness checks with alarms; gold loaded to Aurora | The same code as `make pipeline`, scheduled | Step Functions (retries, branching) or MWAA at larger scale |
| Models | Bedrock through a VPC endpoint; IAM restricted to the allowed models; cross-region inference profiles for throughput | No internet path; quotas and access controlled by IAM | Provisioned throughput for predictable load |
| Learned router | Packaged in the API image (small model, CPU) | No extra hop | SageMaker endpoint if the model grows |
| Identity | Cognito: customer pool (MFA/step-up OTP), staff pool federated to the bank's IdP | The brief: a customer number alone does not prove identity | The bank's existing IdP directly (OIDC) |
| Secrets and keys | Secrets Manager (rotation), KMS customer-managed keys per data class | No secrets in images or env files | SSM Parameter Store for non-secrets |
| Observability | OpenTelemetry (ADOT) → Langfuse (LLM traces, evals) + CloudWatch/X-Ray (infra); dashboards, SLO alarms | One trace per turn: the audit record of what the system did | Other OpenTelemetry backends |
| Audit | Append-only audit table + daily export to S3 Object Lock | Tamper-evident history of every action and decision | |
| CI/CD | Build → scan (ECR, Inspector) → **eval gate** → blue/green deploy (ECS + CodeDeploy); OpenTofu with remote state and locking | A deploy cannot lower the eval results | GitHub Actions with OIDC to AWS, or CodePipeline |
| Security posture | GuardDuty, Security Hub, AWS Config, CloudTrail (org trail), least-privilege task roles | Detection and compliance evidence | |

## Cross-cutting concerns

### Security and privacy
- **Authentication**: Cognito tokens are validated by the API; step-up OTP before sensitive actions such as a card block.
- **Authorization** happens in the tool layer on every call: the customer id comes from the token, never from the conversation. Row-Level Security in Aurora is a second line.
- **Data minimization toward models**: only the fields a step needs are sent. Account numbers are masked, and identifiers are pseudonymized where possible.
- **Encryption**: TLS in transit, KMS at rest everywhere. PII is redacted from logs and traces.
- **Threats** are mapped to the OWASP Top 10 for LLM applications: prompt injection, sensitive information disclosure and excessive agency are covered by the design (code decides, tools are scoped) and by the red-team evals.

### Reliability
- Multi-AZ for every stateful service. Proposed targets: **RPO ≤ 5 min** (Aurora PITR), **RTO ≤ 1 h** (restore plus redeploy through IaC).
- Bounded retries with jitter and timeouts on every external call. A circuit breaker on Bedrock, with a fallback model (another model or region through inference profiles).
- Idempotency keys on every write (dispute creation, card block).
- **Degraded mode**: if models are unavailable, the system hands off to a human with the facts collected so far, and tells the customer.

### Scalability and capacity
- The API is stateless; conversation state lives in Aurora (or ElastiCache later, if latency requires it).
- **The binding limit is model throughput** (Bedrock tokens and requests per minute), not compute. Capacity is estimated as `peak conversations/min × turns × tokens/turn` against the quotas, then load-tested.
- Aurora readers serve the read models; RDS Proxy absorbs connection spikes when ECS scales out.

### Data retention (to define with compliance)
Proposed defaults:

| Data | Proposed retention |
|---|---|
| Conversation transcripts | N days (short), then deleted |
| Traces | 30-90 days |
| Audit log and dispute cases | Regulatory period, in Object Lock |
| Model-provider logging | Disabled or redacted |

### Cost
- The main cost driver is model tokens; prompt caching and small models for routine steps keep it down.
- Fixed costs: Aurora, NAT, ALB, and Langfuse's stores.
- Cost per attempted case and per successful resolution is a tracked metric (see [`evals.md`](evals.md)).

## Open questions

- Aurora vs. RDS PostgreSQL at our scale.
- Langfuse's own stores in production (managed ClickHouse is not an AWS service; self-managed on ECS/EC2 or an alternative backend).
- CI/CD runner: GitHub Actions with OIDC, or CodePipeline.
- Whether staging is worth building for the hackathon (probably not: described only).
