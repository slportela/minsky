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
| [0001](0001-aws-only.md) | Everything runs in our AWS account; no SaaS outside it | accepted (models: temporarily superseded by 0008) |
| [0002](0002-eval-methodology.md) | Eval methodology: τ²-style simulation, end-state grading | proposed |
| [0003](0003-agent-runtime.md) | Agent runtime: Messages API with our own orchestrator | proposed |
| [0004](0004-laya.md) | Laya as a fine-tuned router candidate only | proposed |
| [0005](0005-workflow-dispute-intake.md) | Workflow: transaction-dispute intake | accepted |
| [0006](0006-stack-and-deployment.md) | Stack, POC deployment and target architecture | proposed |
| [0007](0007-observability-and-tracking.md) | Observability and ML tracking: OpenTelemetry first, tools pluggable | proposed |
| [0008](0008-interim-model-provider.md) | Interim model provider: the OpenAI API with GPT-6 Luna while Bedrock is blocked | accepted (temporary) |
| [0009](0009-trusted-test-sessions.md) | Trusted test sessions and recoverable conversation turns | accepted for the POC |
