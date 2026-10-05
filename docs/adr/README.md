# Architecture decision records

One file per decision: `NNNN-short-title.md`. Status is `proposed`, `accepted`, `superseded by NNNN` or `rejected`. Never rewrite an accepted ADR; supersede it.

Template:

```markdown
# NNNN. Title

- Status: proposed | accepted | superseded by NNNN | rejected
- Date: YYYY-MM-DD

## Context
What forces the decision (requirements, constraints, evidence).

## Decision
What we do.

## Consequences
What gets easier, what gets harder, what we must now do. How we would know it was wrong.
```

| ADR | Title | Status |
|---|---|---|
| [0001](0001-aws-only.md) | Everything runs in our AWS account; no SaaS outside it | accepted (models: temporarily superseded by 0008; demo hosting: extended by 0012) |
| [0002](0002-eval-methodology.md) | Eval methodology: τ²-style simulation, end-state grading | proposed |
| [0003](0003-agent-runtime.md) | Agent runtime: Messages API with our own orchestrator | proposed |
| [0004](0004-laya.md) | Laya as a fine-tuned router candidate only | proposed |
| [0005](0005-workflow-dispute-intake.md) | Workflow: transaction-dispute intake | accepted |
| [0006](0006-stack-and-deployment.md) | Stack, POC deployment and target architecture | proposed |
| [0007](0007-observability-and-tracking.md) | Observability and ML tracking: OpenTelemetry first, tools pluggable | proposed |
| [0008](0008-interim-model-provider.md) | Interim model provider: the OpenAI API with GPT-6 Luna while Bedrock is blocked | accepted (temporary) |
| [0009](0009-trusted-test-sessions.md) | Trusted test sessions and recoverable conversation turns | accepted for the POC |
| [0010](0010-cloudfront-demo.md) | CloudFront hostname and private EC2 origin for the demo | superseded by 0011 |
| [0011](0011-lightsail-smoke.md) | Lightsail and CDN for a bounded remote smoke | accepted for the temporary smoke; deployment pending |
| [0012](0012-second-aws-account-for-demo-compute.md) | A second AWS account of the same owner for demo compute | accepted for the demo hosting; deployment pending (data placement superseded by 0014) |
| [0013](0013-case-queue-and-console.md) | Cases in Postgres, a triaged back-office queue and an agent console | proposed |
| [0014](0014-consolidate-into-one-account.md) | One account: the lake and bronze move to the second account; Bedrock follows when a model is invocable | accepted for the lake and bronze; Bedrock not usable yet |
| [0015](0015-demo-operator-credentials.md) | Demo operator credentials that choose which customer to chat as | accepted for the demo only |
| [0016](0016-flexible-transaction-matching.md) | Flexible transaction matching: near amounts, days and merchants are proposed, never selected | proposed |
