# Lightsail remote deployment attempt — blocked by account quota

2026-10-03, own account profile `personal`, `us-east-1`, source `19cd16d`.
Approved USD 5 / six hours; no additional model calls.

The account API confirmed the 2 GB / 60 GB USD 12/month IPv4 instance and USD 2.50/month
50 GB CDN. The reviewed real plan had five creates and no replacement/deletion.
Repository CI, two provider-mocked plan tests and resolved Compose checks passed.

Apply failed: Lightsail rejected `CreateInstances` because the account instance limit
is **0**. One static IP had been allocated first; a reviewed destroy plan deleted it.
No instance or CDN was created. EC2's standard on-demand vCPU quota is also **0**, so
EC2 is not an available fallback in this account. Actual browser/CDN smoke remains
pending; infrastructure plans do not establish deployment or D2 completion.

The local linux/amd64 API image built successfully. Frontend cross-compilation failed
with Docker daemon EOF during Next.js compilation; the local containers were found
stopped afterwards and restarted. This build needs a larger/stable build environment
before an amd64 deployment. No changed application behavior or live-model eval is claimed.

AWS recommends retrying later for a new Lightsail account, then contacting support if
the restriction persists. Enable the service/raise the quota in the own account before
retrying. Do not silently deploy under organizer or another unconfirmed profile.

Cleanup evidence and API verification are in the paired JSON; no keys, tokens, account
ids, IP addresses or customer data are included. A briefly unattached static IP may
have a prorated charge: no zero-cost invoice is asserted. Preserve local private state
for reproducibility; a retry must create and review a fresh plan after quota activation.
