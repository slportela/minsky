# Lightsail smoke on the second account: deployed by hand, partly verified

2026-10-04. Second AWS account of the same owner (ADR 0012), Free plan, profile `minsky-new`.
No model calls were made. No account ids, keys, tokens or customer data are recorded here.

## What exists
- Lightsail 2 GB / 60 GB instance, dual-stack, Ubuntu 22.04, us-east-2a, static IPv4 attached.
- Compose stack (Caddy, API, web, PostgreSQL) from images built on the VM (linux/amd64).
- Gold read models in PostgreSQL: customers 150,000; products 400,000; transactions 4,425,008;
  customer_complaint_stats 150,000; resolution_benchmarks 26; dispute_scenarios 160 (run recorded in `ops.load_runs`).
- Lightsail CDN (small_1_0), HTTP-only origin, HTTPS and HTTP→HTTPS redirect at the viewer side.
- Three random test credentials for three synthetic customers (D01, D02, D03 scenarios), expiring 2026-10-17,
  stored only on the VM. The model key is also only on the VM.

## Verified (from outside, through the CDN)
- `GET /` 200 (HTTP/2), `GET /chat` 200, `GET /api/health` 200 `{"status":"ok","environment":"demo"}`.
- `POST /api/chat/turn` without a credential: 401 `missing_credentials`; with a forged one: 401
  `invalid_credentials`; with only a customer-id header: 401. A JSON POST reaches FastAPI, so `Content-Type` is
  forwarded, and `Authorization` is forwarded (forged token is judged, not dropped).
- No caching: `x-cache: Miss from cloudfront` on repeated calls.
- Firewall: SSH only from one operator /32 over IPv4; no IPv6 SSH; HTTP open. SSH key login works.

## Not verified
- A chat turn with a valid credential and the model key, in a real browser (the purpose of the smoke).
- Behavior under the 2 GB memory limit during a conversation; the stack idles at about 350 MB used.
- Cross-session isolation and the expired-session path through the CDN.
- Persistence: conversations and cases are process-local (ADR 0009); a container restart loses them.

## Deviations and defects found
- The OpenTofu apply was denied by the Free plan's organization policy; the stack was created in the
  console and by CLI. The module was not applied as written.
- Module defects fixed: launch script under dash, `default_ttl = 0` and forwarded-headers `all` rejected by the real API.
- Origin HTTP on port 80 is reachable directly (disclosed in ADR 0011); synthetic data only.

## Cost and teardown
About USD 12/month (VM) + 2.50/month (CDN), drawn from the plan credits. There is no automatic deletion and
no hard spending cap. Stopping the VM does not stop billing; deleting the instance, static IP and
distribution does. Teardown is operator-run and not yet done.
