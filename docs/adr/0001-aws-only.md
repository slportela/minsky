# 0001. Everything runs in our AWS account; no SaaS outside it

- Status: accepted
- Date: 2026-09-26

## Context
The team wants no dependency on third-party SaaS: every service must run on infrastructure we control. We use AWS:
- **S3** for data,
- **Bedrock** for models (Claude; other providers only where Bedrock offers them),
- **AWS** for hosting.

Two things outside our account are imposed by the organizers: the source dataset in their S3 bucket (read-only), and the submission as a public GitHub repository. Vendor eval platforms can also disappear: OpenAI is deprecating its Evals platform (read-only on 2026-10-31, shut down on 2026-11-30).

## Decision
- Data: organizer S3 (read-only) → bronze and lake in our own S3 bucket (`make pipeline`).
- Models through Amazon Bedrock only. There are no direct calls to model vendors' APIs.
- Observability: fed through OpenTelemetry, so the backend can be swapped (tool choice: ADR 0007).
- Eval cases, graders, runs and reports live in the repository, in plain files.
- CI runs locally (`make ci`, pre-commit). GitHub Actions is still open (see `docs/challenge.md`).

## Consequences
- One cloud, one access-control model (IAM), and data residency is easy to explain.
- Bedrock differs from the first-party Claude API in a few features we would otherwise use: **no Message Batches**, **no server-side model fallbacks**, and **no `inference_geo`**. Structured outputs, strict tools, prompt caching and citations are available. So eval runs execute live calls (no batch discount), and fallbacks are handled in our code.
- Which models are available depends on the Bedrock catalog and the region; check it before designing around a model.
