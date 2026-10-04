# PR 26 review corrections — offline verification

2026-10-03. No AWS resources or model calls were created by these checks.

- CDN headers use `all`, supported by the pinned AWS provider. The mocked plan locks
  HTTP origin, all headers/cookies/queries, API methods, `dont-cache` and all TTLs zero.
  GET/HEAD remain the configured cacheable-method set; that does not enable caching
  when `dont-cache` and zero TTLs apply. Runtime cache/error behavior remains unverified.
- Module and environment accept IPv4 /32 addresses only. **7 module tests and 2 root
  environment tests pass**. Negative controls include paired /1 prefixes, /24,
  malformed addresses, IPv6 and empty input; both layers reject the paired /1 bypass.
- Bootstrap pins Compose **2.39.4**, enables noninteractive apt, adds `ubuntu` to the
  Docker group and gives that user access to `/opt/minsky`. The official amd64 binary
  SHA-256 was independently verified as
  `7af95166a730b87e172d4fc9aefea8725d3c6c7327d59149267b452114ddb7d4`.
  Shell syntax passes. A private mocked-command run accepts the verified artifact;
  a corrupt artifact exits before binary installation. This does not establish real
  Ubuntu cloud-init/package/group/bootstrap success; reconnect SSH to refresh groups.
- Compose configuration resolved with dummy image tags and `--env-file /dev/null`:
  all four services require linux/amd64, app build definitions removed, only Caddy
  HTTP published and PostgreSQL/API/frontend ports internal. No services were started.
- Repository CI passes: **219 Python tests**, lint/format/types, case checks,
  **4/4** offline regression cases, **12/12** scripted dev smoke (95% Wilson interval
  75.7–100%) and **2/2** frontend API tests/types. The identical Makefile target graph
  ran from a temporary copy with dotenv-reading pipeline assignments disabled.

The EC2 runbook is restored alongside Lightsail. D2 now states configuration validation
and blocked apply, with no VM or CDN deployed. Docs explicitly distinguish operator
cleanup/budget controls from automatic deletion and AWS hard spending caps.

A prior isolated FastAPI request-schema probe returned 422 without Content-Type and
200 with application/json. AWS documents that Content-Type and custom headers are
forwarded by default; the claimed CDN stripping was not observed. Explicit `all`
removes configuration ambiguity without adding unsupported names to headersAllowList.

No prompt/model/tool/policy/orchestration behavior or locked test cases changed: no
new L2 behavioral delta is claimed. Public browser/JSON/navigation/cache smoke,
amd64 frontend image build and actual bootstrap remain pending. Existing AWS quotas
and prior failed-deployment cleanup are recorded in the separate deployment report.

Sources: [Compose release](https://github.com/docker/compose/releases/tag/v2.39.4),
[AWS header configuration](https://docs.aws.amazon.com/lightsail/2016-11-28/api-reference/API_HeaderObject.html),
[AWS default header behavior](https://docs.aws.amazon.com/lightsail/latest/userguide/amazon-lightsail-distribution-request-and-response.html).
