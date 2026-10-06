# frontend

Next.js (React, TypeScript, App Router) with two surfaces in one app:

| Route | Who | What |
|---|---|---|
| `/chat` | Customer | Trusted expiring test-session credential, conversation in es, Sí/No shortcuts for confirmations |
| `/console` | Dispute and fraud agents | Handoff queue; each handoff shows the request, verified facts with sources, actions taken, the policy rule, open questions and a trace link |

Rules:
- The UI renders what the backend returns; it never computes facts, eligibility or decisions.
- Styling lives in one stylesheet, `app/globals.css` (tokens at the top, same dark look as the pitch deck). No web fonts, no external requests (ADR 0001); pages use class names, not inline styles.
- All calls go through `lib/api.ts` to same-origin `/api/*`: Caddy (local, demo) or the load balancer (production) routes them to the backend. There is no API URL or secret in the bundle.
- `/chat` asks `GET /api/chat/options` once. Only if the server says `mode_switch: true` (`MINSKY_ALLOW_MODE_SWITCH`) it shows a "Flujo" selector (standard flow or agent); the choice is sent as `mode` on the first turn of a conversation and never on later ones, and changing it starts a new conversation. The UI never decides which flow is allowed or what it does (`docs/agentic_dispute_agent.md`).
- `/chat` posts to `/api/chat/turn` with `Authorization: Bearer <credential>` (server resolves customer identity; OTP/Cognito come later) and replays the server-owned message history each turn.

```bash
cd frontend && npm install     # creates package-lock.json: commit it
npm run dev                    # http://localhost:3000; /api needs the backend (make up runs everything)
npm run typecheck && npm run lint
```

Test credentials are password inputs held only in component memory: never displayed in the chat,
stored in localStorage, or included in message payloads. “Cambiar sesión” clears credential and history.
`npm run test:api` verifies the request/authentication contract without a backend or provider call.
