# 0005. Workflow: transaction-dispute intake

- Status: accepted
- Date: 2026-09-26

## Context
The brief asks for one workflow done deeply. It gives four examples: account/payment inquiries, card support, transaction-dispute intake, credit information and eligibility. More workflows earn no bonus.

## Decision
We build **transaction-dispute intake** (`docs/solution.md`, policy in `docs/dispute_policy.md`). Why:
- the data has what the workflow needs: transaction status, fraud flags and amounts, plus complaints with SLA, resolution days and compensation for a baseline;
- all three required paths occur naturally: automatic intake, clarification among several candidate transactions, and a human for fraud or high amounts;
- one sensitive action (card block) exercises confirmation;
- it avoids the credit workflow's extra constraints (separate risk model, no AI approvals).

## Consequences
- Complaints have no transaction id, so the historical baseline is by category and customer, not by transaction (`known_issues.md`).
- The dispute policy is synthetic and must be labeled as such everywhere.
- To confirm with the data analysis: dispute-related contact volume and cost. If the data contradicts the choice, this ADR is superseded.
