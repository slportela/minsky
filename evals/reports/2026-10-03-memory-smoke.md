# Two-GB deployment candidate: local resource diagnostic

Run on 2026-10-03 UTC (2026-10-03 in Argentina); no AWS resources created and no
provider model calls made. All bank reads were read-only. Case/conversation writes
lived in a separate in-memory app and did not change the serving app's case state.
No real dotenv file was inspected or modified. No dependency was installed.

## Observations

- Actual Docker Linux guest: **2,002,415,616 bytes = 1.865 GiB**, two vCPUs, ARM64.
  All four existing containers remained running during the diagnostic, plus an
  additional API diagnostic process: this was not an unconstrained host test.
- Existing gold: **4,425,008 transactions**, 160 dispute scenarios. Policy/data labels
  were checked in code before requests; twelve distinct D09 and twelve D07 rows.
- **36 conversations**, four concurrent API scenarios: 12 eligible resolutions,
  12 above-limit handoffs, 12 empty-search/follow-up clarifications. **84 HTTP/ASGI
  turns** returned 200; stored dispute/handoff references and ownership were read back
  in the isolated app. Empty searches retained their merchant after adding an amount.
- Anonymous request: HTTP 401.
- **100 frontend GETs** inside Docker and **100 HTTPS GETs** through Caddy (50 `/chat`,
  50 `/api/health`), each workload at concurrency four, all HTTP 200. Caddy certificate
  and hostname were verified using the explicitly trusted public local CA; macOS trust
  was unchanged. These checks do not establish a public AWS browser path.
- Diagnostic scenarios completed in approximately **3.97 seconds**. Scripted extraction
  delayed each extraction by 150 ms; reported timing is not real model latency.
- Minimum guest `MemAvailable`: **574,656,512 bytes = 548.04 MiB**. This is the minimum
  across the in-guest 50-ms samples, not a claim of precise per-container peak RAM.
- No container restarts or last-exit OOM flags before/after. API cgroup OOM/kill counters
  were zero afterwards; guest SwapTotal was zero afterwards.
- PostgreSQL database size: **1,506,457,267 bytes ≈ 1.40 GiB**. Four runtime image sizes
  total **903,826,097 bytes ≈ 0.84 GiB**. A 60-GB disk has room for these measured
  artifacts; OS, cache, logs, WAL growth and rebuilds need additional room.

## Conclusion and limits

**A prebuilt two-GB instance is a reasonable next deployment candidate for a short smoke.**
The test supports that recommendation, not a guarantee of Lightsail capacity. It is a
short local ARM64 diagnostic with scripted extraction; no real Lightsail/x86_64 run,
cloud-network/CDN validation, sustained load or full live-model resource measurement.
API conversations used ASGI in a second process in the API container; routed Caddy
GETs exercised frontend/health separately. No statistical production success rate is
claimed. Build images elsewhere and verify live-model flows after actual deployment.
No behavioral code or locked eval cases changed; this is resource evidence, not an L2 delta.

Two initial diagnostic attempts failed before cases because `httpx` is dev-only and
not installed in the production image. The existing SDK's `httpx2` supported ASGI, so
no dependency/config/container replacement was needed. Failed setup attempts did not
call the provider or change bank data.

## Lightsail billing verification

AWS documents hourly instance billing with a monthly ceiling; a stopped instance is
still billable until deleted. The 2-GB/60-GB/public-IPv4 bundle is **USD 12/month**.
The smallest CDN bundle is **USD 2.50/month / 50 GB** and provides an AWS hostname
with HTTPS. Sources checked on 2026-10-03:

- [Instance bundles](https://docs.aws.amazon.com/lightsail/latest/userguide/amazon-lightsail-bundles.html)
- [Instance billing](https://docs.aws.amazon.com/lightsail/latest/userguide/amazon-lightsail-frequently-asked-questions-faq-billing-and-account-management.html)
- [CDN pricing](https://aws.amazon.com/lightsail/pricing/)
- [Default HTTPS hostname](https://docs.aws.amazon.com/lightsail/latest/userguide/amazon-lightsail-faq-cdn-distributions.html)

**No authoritative per-hour CDN proration rule was located in the checked docs.** Do
not promise a cents-only total for a short trial: reserve the full USD 2.50 CDN monthly
fee plus the actual instance-hours, model calls, applicable transfer overages and tax.
No account free-tier credits are assumed. Cost approval, our AWS profile, image
architecture/build, deployment and live public-origin smoke remain pending. The existing
PR #26 still describes EC2 + NAT; it has not been changed to Lightsail by this diagnostic.
