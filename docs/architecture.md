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
 │                   RDS Proxy ◀─┘         │           SQS (+DLQ)           OTLP backends          │
 │ ────────────────────┼───────────────────┼─────────────────────────────────────────────────────  │
 │ isolated data       ▼                   │                                                        │
 │ subnets   RDS PostgreSQL                │                                                        │
 │           (Multi-AZ, KMS, PITR, RLS,    │                                                        │
 │            read replica if needed)      │                                                        │
 │                                         │                                                        │
 │ VPC endpoints: Bedrock runtime · S3 · ECR · Secrets Manager · KMS · SQS · CloudWatch · STS       │
 └─────────────────────────────────────────┼───────────────────────────────────────────────────────┘
                                           ▼
     Amazon Bedrock (models, via PrivateLink)     Cognito (customer pool + staff pool / bank IdP)
     S3 lake: bronze · silver · gold (KMS, versioned)     Secrets Manager · KMS
     EventBridge Scheduler ─▶ ECS task: ingest + dbt ─▶ gold ─▶ Postgres read models
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
| Transactional data | RDS PostgreSQL Multi-AZ behind RDS Proxy | Managed HA and PITR, sized for the workload below; details in [`poc_to_prod.md`](poc_to_prod.md) | Aurora PostgreSQL if we need fast failover, many readers or storage autoscaling |
| Lake | S3 bronze/silver/gold, SSE-KMS, versioning, lifecycle, Block Public Access; Glue catalog + Athena for analysis | Cheap, durable, auditable history | Lake Formation for fine-grained access |
| Pipeline | EventBridge Scheduler → ECS task (ingest + dbt); freshness checks with alarms; gold loaded to Postgres | The same code as `make pipeline`, scheduled | Step Functions (retries, branching) or MWAA at larger scale |
| Models | Bedrock through a VPC endpoint; IAM restricted to the allowed models; cross-region inference profiles for throughput | No internet path; quotas and access controlled by IAM | Provisioned throughput for predictable load |
| Learned router | Packaged in the API image (small model, CPU); the model file is named in config and traced to its training report | No extra hop; every deploy knows which model it runs | SageMaker endpoint if the model grows |
| ML tracking | Committed training reports (ADR 0007) | Reproducible runs, readable in the repo | MLflow on ECS (RDS + S3) when experiments grow |
| Identity | Cognito: customer pool (MFA/step-up OTP), staff pool federated to the bank's IdP | The brief: a customer number alone does not prove identity | The bank's existing IdP directly (OIDC) |
| Secrets and keys | Secrets Manager (rotation), KMS customer-managed keys per data class | No secrets in images or env files | SSM Parameter Store for non-secrets |
| Observability | OpenTelemetry (ADOT) → CloudWatch + X-Ray; execution records in Postgres; dashboards, SLO alarms (ADR 0007) | One trace per turn; any OTLP backend plugs in by configuration | Phoenix or Langfuse on ECS for an LLM-specific UI |
| Audit | Append-only audit table + daily export to S3 Object Lock | Tamper-evident history of every action and decision | |
| CI/CD | Build → scan (ECR, Inspector) → **eval gate** → blue/green deploy (ECS + CodeDeploy); OpenTofu with remote state and locking | A deploy cannot lower the eval results | GitHub Actions with OIDC to AWS, or CodePipeline |
| Security posture | GuardDuty, Security Hub, AWS Config, CloudTrail (org trail), least-privilege task roles | Detection and compliance evidence | |

## Workload and capacity

The brief does not state a scale; it asks us to design for scalability, explain capacity limits and state our workload assumptions. We derive them from the dataset: about **626 contact-center interactions a day** for 150k customers (all reasons; 686,296 over 1,097 days).

Assumptions (labeled, to be replaced by measurements):
- disputes ≈ 20% of contacts (*to measure*);
- the peak hour carries 15% of the day;
- 6 turns per conversation;
- ~2 model calls per turn.

| Tier | Customers | Dispute chats/day | Peak model calls/min | What it takes |
|---|---|---|---|---|
| This bank (the dataset) | 150k | ~125 | ~5 | A single small deployment |
| Mid-size bank | 1M | ~830 | ~25 | The same design; watch Bedrock quotas and cost |
| Large bank | 10M | ~8,300 | ~250 | Quota increases or cross-region inference profiles; autoscaling tuned |
| Incident surge (breach, mass fraud) | any | 10-50× normal | bursts | Queueing, rate limits, degraded mode (hand off with context), surge playbook |

What this tells us:
- Even at millions of customers, the load is modest for the servers. **The binding limits are model throughput (Bedrock quotas) and cost**, then database connections.
- The production design (multi-AZ, private networking, managed services) is justified by **availability, security and compliance** more than by traffic.
- Capacity is checked as `peak conversations/min × turns × calls/turn × tokens/call` against the account's Bedrock quotas, then confirmed with a load test.

## Cross-cutting concerns

### Security and privacy
- **Authentication**: Cognito tokens are validated by the API; step-up OTP before sensitive actions such as a card block.
- **Authorization** happens in the tool layer on every call: the customer id comes from the token, never from the conversation. Row-Level Security in Postgres is a second line.
- **Data minimization toward models**: only the fields a step needs are sent. Account numbers are masked, and identifiers are pseudonymized where possible.
- **Encryption**: TLS in transit, KMS at rest everywhere. PII is redacted from logs and traces.
- **Threats** are mapped to the OWASP Top 10 for LLM applications: prompt injection, sensitive information disclosure and excessive agency are covered by the design (code decides, tools are scoped) and by the red-team evals.

### Reliability
- Multi-AZ for every stateful service. Proposed targets: **RPO ≤ 5 min** (RDS point-in-time recovery), **RTO ≤ 1 h** (restore plus redeploy through IaC).
- Bounded retries with jitter and timeouts on every external call. A circuit breaker on Bedrock, with a fallback model (another model or region through inference profiles).
- Idempotency keys on every write (dispute creation, card block).
- **Degraded mode**: if models are unavailable, the system hands off to a human with the facts collected so far, and tells the customer.

### Scalability and capacity
- The API is stateless; conversation state lives in Postgres (ElastiCache later, if latency requires it).
- **The binding limit is model throughput** (Bedrock tokens and requests per minute), not compute (see "Workload and capacity").
- RDS Proxy absorbs connection spikes when ECS scales out; a read replica serves read models if load requires it.

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
- Fixed costs: RDS, NAT gateways, ALB, VPC endpoints.
- Cost per attempted case and per successful resolution is a tracked metric (see [`evals.md`](evals.md)).

## Open questions

- RDS PostgreSQL vs. Aurora (revisit with measured load and failover needs).
- Whether production needs an LLM-specific trace UI (Phoenix, Langfuse) on top of CloudWatch/X-Ray.
- CI/CD runner: GitHub Actions with OIDC, or CodePipeline.
- Whether staging is worth building for the hackathon (probably not: described only).
