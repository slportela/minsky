# 0011. Lightsail for a bounded remote smoke

- Status: accepted for the temporary smoke; deployment pending
- Date: 2026-10-03
- Supersedes: 0010

## Context

The team needs a remote browser test without purchasing a domain. The private EC2/NAT
proposal exceeds the desired diagnostic cost. A short local 2 GB guest probe passed
36 scripted conversations and 200 frontend/health requests without OOM; it is ARM64,
not a deployed amd64 capacity test (`evals/reports/2026-10-03-memory-smoke.md`).
D2 still requires a separately funded final demo through 2026-10-16. ADR 0001 hosting
and the temporary model exception in ADR 0008 remain in force.

## Decision

Create a separate Lightsail 2 GB / 60 GB IPv4 instance and smallest CDN distribution
in `envs/smoke`, leaving the existing EC2 environment unchanged. AWS supplies the
CloudFront hostname and public viewer certificate. Caddy receives HTTP on port 80.
Disable caching, allow POST and forward Authorization, cookies, queries and UI headers.
Only HTTP and operator-restricted SSH are published; PostgreSQL stays inside Compose.
Upload prebuilt linux/amd64 app images and synthetic gold read models over SSH.
Provision fresh expiring synthetic credentials and a minimal runtime environment;
never upload the local .env, organizer credentials or AWS credential directory.
No Lightsail runtime instance role is assumed. The smoke uses the interim provider.

The user approved at most USD 5 infrastructure for six hours, followed by deletion;
model diagnostics retain their independent USD 1 cumulative budget. Reserve the full
USD 2.50 monthly CDN fee rather than assuming hourly proration or free-tier credits.
Confirm own AWS profile, bundle availability, price and actual resource plan before apply.

## Consequences

No NAT, ALB, RDS, new domain or runtime rewrite. This is a temporary diagnostic:
public origin HTTP can be reached directly and does not provide end-to-end TLS or
origin isolation. Use synthetic data and short-lived test identities only. Production
retains private services and ALB/ACM TLS; a production deployment needs a different
network and identity setup. Lightsail CDN cannot simply change its origin to ECS/ALB.

Offline plans cannot establish deployment, header forwarding or error-cache behavior.
Verify these in the real browser, including unauthenticated/expired 401 responses and
cross-session isolation. D2 stays partial after a six-hour smoke: the final demo must
be available through October 16. Capture evidence before destroy; stopping the VM
alone does not stop billing. The spending allowance is operational, not an AWS hard cap.

Sources: [Lightsail pricing](https://aws.amazon.com/lightsail/pricing/),
[distribution HTTPS](https://docs.aws.amazon.com/lightsail/latest/userguide/amazon-lightsail-faq-cdn-distributions.html),
[request forwarding](https://docs.aws.amazon.com/lightsail/latest/userguide/amazon-lightsail-distribution-request-and-response.html),
[billing](https://docs.aws.amazon.com/lightsail/latest/userguide/amazon-lightsail-frequently-asked-questions-faq-billing-and-account-management.html).
