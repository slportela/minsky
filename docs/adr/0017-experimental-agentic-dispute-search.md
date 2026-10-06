# 0017. Experimental conversational transaction search

- Status: accepted for an opt-in experiment; workflow remains the default
- Date: 2026-10-05
- Extends: 0003 and 0016; does not replace the default workflow or dispute policy

## Context

Customers describe amounts, dates and merchants approximately. ADR 0016 now gives the workflow flexible
matching in code. PR #51 explores whether a model that converses and chooses read queries provides useful
additional flexibility. The updated live dev A/B at 56e1c86 found workflow 74/74 versus agentic 71/74;
the agentic mode is therefore not adopted as the default. Results and limitations are in
`evals/reports/2026-10-05-pr51-updated-ab.md`.

## Decision

Keep two modes, fixed when a conversation starts. `workflow` is the default; `agentic` is explicitly enabled
by server configuration, or selected through the existing operator-enabled mode switch. This is an experimental
search option, not a permission setting.

In agentic search, the model receives the conversation and can query a read-only SQLite snapshot containing
only the authenticated customer's transactions. Session ownership is verified before building the snapshot.
Query authorization, value/row/time limits and permitted columns are code-owned. Fraud indicators are omitted.
The model may propose only an id returned by a query. It has no tool to open a dispute, block a card or create
a handoff. Query SQL and results are untrusted; output grounding checks remain partial, not a safety proof.

Code re-reads the proposed transaction, asks for explicit consent, confirms recognition when necessary,
applies the existing policy, and verifies writes before reporting them. Every ambiguous answer to the card
counts toward one shared limit, even if search resumes or the model interprets a longer reply as affirmative.
A free-text correction can return to search; it cannot authorize an action. Escalation requires confirmation
of the charge or explicit acceptance of a person. The summary separates bank-verified facts from labeled
customer/assistant narrative.

These decisions extend the state-machine runtime of ADR 0003 only during transaction discovery. They do not
supersede authorization, policy, consent, terminal-state or audit requirements. Provider configuration remains
outside this decision; the latest live runs used the directly authorized interim OpenAI configuration.

## Consequences

More conversational flexibility is possible, with additional tool-loop cost and failure modes. The current
workflow already handles the measured matching cases, so the experiment has no demonstrated accuracy gain.
The latest comparison exposed a confirmation-count defect and a deterministic simulator that could not answer
a legitimate expected-amount question. Fixes require new evidence; historical results are not overwritten.

SQLite snapshots and process-local conversation storage are suitable for the current small fixture histories.
Production must retain session isolation and query budgets while providing durable conversation state and
handling larger histories explicitly; an oversized history fails rather than silently truncating.

Before considering a default change, evaluate matched workloads after fixes, validate on val for selection,
and review security and operating cost. The locked test split stays untouched. Merging the opt-in implementation
is distinct from enabling it in the demo or adopting it as the default.
