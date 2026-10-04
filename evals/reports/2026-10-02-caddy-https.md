# Scoped Caddy HTTPS smoke on merged main

Source: `d3bf18b5da91f13b4f0467fd34e6f2a6c12f3314`; rebuilt Compose API/frontend,
real PostgreSQL gold, live configured `gpt-6-luna`, SDK retries disabled.

This is an HTTP-client diagnostic, not a browser test or held-out evaluation.
Certificate chain and hostname were validated against Caddy's public local CA explicitly
in the client. macOS trust was unchanged; no TLS warnings were bypassed.

- `/api/health` and `/chat`: HTTP 200.
- Anonymous chat request: HTTP 401, `missing_credentials`.
- Policy-checked gold D09 scenario: three HTTP 200 turns; reply reference `DSP-228d0b90793d`.
- Policy-checked gold D07 scenario: two HTTP 200 turns; reply reference `HO-d7bc25d6a7c8`.
- Search for a nonexistent merchant: HTTP 200, clarification requested.

Six authenticated turns, one scripted conversation per scenario. These are diagnostic
observations, not a statistical success estimate. References were observed in replies;
process-local case writes were not independently inspected in this run. The earlier
instrumented integration report provides separate read-back evidence.

Reserved USD 0.0300000 conservatively; provider usage is unavailable through this HTTP
endpoint. Total retained reservations are USD 0.2385836 against the authorized total USD 1.
No credentials are included in the evidence. Temporary synthetic sessions expire.

Remaining: native browser through Caddy with a trusted certificate, persistence and the
other limitations recorded in `2026-10-02-live-integrated.md`. Serving the frontend HTML
successfully does not establish browser interaction or browser trust.
