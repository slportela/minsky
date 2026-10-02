# 0009. Trusted test sessions for the POC HTTP boundary

- Status: accepted for the POC
- Date: 2026-10-02

## Context

B5 requires a trusted test session; a customer id alone is not identity. Accepting an arbitrary
customer header as a valid session defeats the existing ownership checks. Real OTP/Cognito is
still outside this correction's scope.

## Decision

Resolve opaque bearer credentials through a server-provisioned secret mapping to customer id,
state, and timezone-aware expiry. No customer lookup from chat text or legacy headers authenticates.
Missing, anonymous, forged, and expired sessions fail closed before the orchestrator runs. The
identity resolver is the replacement boundary for an OTP/Cognito adapter.

Serialize each conversation's history check and turn commit, and store isolated state snapshots.
After a failed turn, clients can replay their last accepted history. Disputes, card blocks, and
conversation-turn handoffs are idempotent, so a failure after a write can be reconciled by read-back.

## Consequences

Credentials must be generated randomly, distributed to the corresponding test users, rotated,
and sent over HTTPS. They never enter model inputs or eval artifacts. There is no public credential
provisioning/reset API. Test sessions are not production authentication; no OTP flow is claimed.
Conversation/case persistence and distributed locking remain work for the Postgres-backed adapter;
the current process-local implementation must run as a single worker.
