# frontend

Next.js (React, TypeScript, App Router) with two surfaces in one app:

| Route | Who | What |
|---|---|---|
| `/chat` | Customer | Test login (simulated OTP), conversation in es/pt, explicit confirm buttons for actions |
| `/console` | Dispute and fraud agents | Handoff queue; each handoff shows the request, verified facts with sources, actions taken, the policy rule, open questions and a trace link |

Rules:
- The UI renders what the backend returns; it never computes facts, eligibility or decisions.
- All calls go through `lib/api.ts` to same-origin `/api/*`: Caddy (local, demo) or the load balancer (production) routes them to the backend. There is no API URL or secret in the bundle.
- `/chat` posts to `/api/chat/turn` with POC header `X-Minsky-Customer-Id` (not real auth) and replays the server-owned message history each turn.

```bash
cd frontend && npm install     # creates package-lock.json: commit it
npm run dev                    # http://localhost:3000; /api needs the backend (make up runs everything)
npm run typecheck && npm run lint
```
