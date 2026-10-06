# 0015. Demo operator credentials that choose which customer to chat as

- Status: accepted for the demo only
- Date: 2026-10-05
- Refines: 0009 (trusted test sessions). It does not replace it: the provisioned customer sessions keep working.

## Context

The demo had seven customer credentials, one per policy rule, each loaded by hand. The backend itself is not limited to
them: the policy, the search and the dispute writes read `bank.*` for any of the 150,000 customers. What limits the
demo is who can sign in. The dataset has no personal identifier to check (`bank.customers` holds an id, a first name,
a country, a segment, a status and a detected accent), so a knowledge check such as "id and date of birth" has nothing
to compare with, and a first name or a country is not a secret. The brief says a customer number alone does not prove
identity (B5), and ADR 0009 refuses to let an id authenticate.

Options considered: sign in with the customer id alone (rejected: it is exactly what B5 and ADR 0009 rule out, and the
CloudFront URL is public); a signing key handed to the jury so each can mint a credential for any customer (rejected:
the key is a master secret that cannot be revoked per person); more provisioned credentials (works, but a static list).

## Decision

A third class of credential, the **demo operator**, beside the customer sessions (`identity.http`) and the staff
sessions (`identity.staff`). It is server-provisioned and expiring, like them (`MINSKY_DEMO_OPERATOR_SESSIONS`: token →
`{operator_id, expires_at}`), and resolves only in `identity.operator`. Two operators are issued: `equipo` (ours, for
rehearsal) and `jurado` (shared by the jury), so the audit trail tells a rehearsal from a jury session and one can be
revoked without the other.

- `GET /api/demo/whoami` says whether a credential is an operator's. `POST /api/demo/session` takes an operator
  credential and either a customer id or `random` (a customer with a recent posted charge under the automatic limit
  and not a repeat complainer, so a dispute can be taken to the end). It checks the customer exists in `bank.customers`
  and returns a **customer session**: a random token with a two-hour lifetime. Only its SHA-256 is kept, in memory
  (conversations are process-local too, so a restart ends both).
- The customer id never authenticates. The operator credential does, and the customer id says whom to chat as. The chat
  and the tools receive an ordinary `ToolSession`; the customer id still never comes from the conversation text, so
  the tool-layer ownership checks are unchanged.
- **Audit:** every choice is written to the audit trail (`demo_choose_customer`, operator, customer), and the session id
  of the issued session is `demo:<operator>:<ref>`, so every tool call made in it names who chose the customer.
- **Bounds:** off by default (`MINSKY_DEMO_OPERATOR_ENABLED`); the settings refuse to load with it on unless
  `MINSKY_ENVIRONMENT` is `local` or `demo`; at most 20 sessions a minute per operator and 500 live sessions; the
  operator credentials expire on 2026-10-19, after the demo has to keep working (2026-10-16).
- A customer or staff credential is not an operator credential and the reverse: each class resolves only in its own
  module, so an operator cannot read the case queue and a customer cannot choose a customer.
- The chat page shows a visible "Modo demo" notice, the operator and the customer it is acting as.

## Consequences

- Anyone holding an operator credential can chat as any customer, see their charges and open disputes on them. The data
  is synthetic and this is the intended use, but it is **not authentication of customers** and must not be described as
  such. Production signs customers in with Cognito and OTP (`docs/architecture.md`); this class does not exist there.
- B5 stays 🟡 and says so. Opening a dispute is once per charge, and the cases a rehearsal leaves in `cases.*` stay: the
  `equipo` operator makes them identifiable in the audit trail, and clearing them is an operations step, not code.
- Credentials are secrets of the demo: generated randomly (`infra/demo_sessions.py`), kept out of git and out of the
  documents, delivered through the private channel of the submission, and removed after 2026-10-16.
- Rotating an operator credential means changing the VM's `runtime.env` and restarting the API; live sessions end.
