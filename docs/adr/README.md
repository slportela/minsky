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
| [0001](0001-aws-only.md) | Everything runs in our AWS account; no SaaS outside it | accepted |
| [0002](0002-eval-methodology.md) | Eval methodology: τ²-style simulation, end-state grading | accepted |
| [0003](0003-agent-runtime.md) | Agent runtime: Messages API with our own orchestrator | proposed |
| [0004](0004-laya.md) | Laya as a fine-tuned router candidate only | proposed |
| [0005](0005-workflow-dispute-intake.md) | Workflow: transaction-dispute intake | accepted |
| [0006](0006-stack-and-deployment.md) | Stack, POC deployment and target architecture | proposed |
