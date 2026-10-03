# infra

How the system runs **for the hackathon**. Two environments: local development and the POC demo on AWS.

> **The POC demo (one EC2 + Docker Compose) is not the production architecture.** The target is [`docs/architecture.md`](../docs/architecture.md). How each POC piece maps to it, database included: [`docs/poc_to_prod.md`](../docs/poc_to_prod.md). The same container images run in both.

```
 LOCAL (make up)                       REMOTE SMOKE (envs/smoke)             PRODUCTION (docs only)
 laptop · compose                      Lightsail CDN · VM · compose        ECS · RDS · Cognito · WAF · …
 self-signed TLS · ~/.aws for Bedrock  viewer TLS · restricted SSH           see docs/architecture.md
```

## Local

```bash
cp .env.example .env     # set AWS_PROFILE (Bedrock access) and the organizer/bucket values
make up                  # build and start caddy, web, api, postgres → https://localhost
make logs                # follow logs
make down                # stop (data volumes are kept)
```

## Temporary remote smoke on Lightsail (ADR 0011)

No domain purchase: a 2 GB / 60 GB IPv4 VM runs Caddy, Next.js, FastAPI and PostgreSQL.
A Lightsail CDN supplies `https://<id>.cloudfront.net/chat` and its public certificate.
Viewer TLS ends at the CDN; the origin uses **public HTTP**. This synthetic diagnostic
does not verify production origin TLS/isolation. The original EC2 environment stays
unchanged; use the separate `infra/tofu/envs/smoke` state.

### Prepare and verify

Use a confirmed profile from our own account; never organizer credentials. Agents must
not inspect `.env`. Confirm regional `get-bundles`, `get-blueprints` and distribution
bundles before apply; example ids are not proof of account availability. Set the region
and a matching AZ and replace the example SSH CIDR with the operator's public IPv4 /32.

```bash
cp infra/tofu/envs/smoke/terraform.tfvars.example infra/tofu/envs/smoke/terraform.tfvars
tofu -chdir=infra/tofu/envs/smoke init -input=false
tofu -chdir=infra/tofu/envs/smoke validate
tofu -chdir=infra/tofu/modules/lightsail_smoke init -backend=false -input=false
tofu -chdir=infra/tofu/modules/lightsail_smoke test
tofu -chdir=infra/tofu/envs/smoke plan -out=smoke.tfplan
```

Mocked plan tests make no AWS calls. They check the selected bundles, restricted SSH,
HTTP-only host exposure, uncached POST and Authorization forwarding. The own-account API confirmed the 2 GB USD 12/month VM and USD 2.50/month CDN;
the real plan contains five creates and no replacement/deletion. Apply was attempted on 2026-10-03 but blocked by the account Lightsail limit of zero.
The briefly allocated static IP was deleted; deployed browser checks remain pending.
See `evals/reports/2026-10-03-lightsail-deployment.md`. Review the saved plan: only one
VM, static IP/attachment/firewall and CDN; no NAT, ALB, RDS, snapshots or IAM credentials.
State/plans are private and ignored. Confirm the USD 5/six-hour allowance against
`demo_cost.md`; the cumulative model budget remains USD 1.

### Deploy and load the read models

```bash
tofu -chdir=infra/tofu/envs/smoke apply smoke.tfplan
tofu -chdir=infra/tofu/envs/smoke output demo_url
tofu -chdir=infra/tofu/envs/smoke output public_ip
tofu -chdir=infra/tofu/envs/smoke output instance_name
```

Bootstrap installs Docker/Compose, not the application or database. Access Ubuntu over
SSH with the Lightsail key and the allowed operator CIDR; keep keys private, verify the
host key. Build app images on the workstation for **linux/amd64**, transfer image archives
and `compose.yaml`, `compose.demo.yaml`, `infra/caddy/Caddyfile` to `/opt/minsky` over SSH.
Do not build Next.js on the small VM. Use `docker load` and provision a minimal, mode-0600
runtime env file with strong PostgreSQL password, expiring synthetic sessions and model
settings (ADR 0008). Never upload the workstation `.env`, AWS profiles or organizer keys.
Set `MINSKY_API_IMAGE` and `MINSKY_WEB_IMAGE` to the exact transferred image tags.

On the host, use Compose **2.24.4 or newer**:

```bash
docker compose -f compose.yaml -f compose.demo.yaml up -d --no-build
```

Do not add `compose.local.yaml`. The override removes build definitions and publishes
only Caddy port 80. The firewall additionally permits restricted SSH. Database and API
ports are not host-published. No EC2 instance profile/SSM access is assumed for Lightsail.
Export the synthetic `bank` read models using `pg_dump` from the working PostgreSQL,
transfer securely and restore into the new PostgreSQL. Do not transfer local case writes
or complete database volumes. Verify gold counts/scenarios and health before opening chat.
Only fresh diagnostic identities may invoke the model; do not expose bearer tokens in
URLs, evidence or logs. The test operator accounts for every paid attempt against the ledger.

### Browser verification and cleanup

Follow `docs/integrated_smoke.md`: public certificate without bypass, frontend/health,
POST, history, D09 read-back, ambiguity, handoff, anonymous/expired 401 and cross-session
isolation. Verify forwarding and cache/error behavior on the live CDN; mocked settings
alone do not prove them. Record source/image revision and results before reporting success.

Delete this **temporary** deployment within six hours. Preserve evidence, then review and
apply `tofu plan -destroy -out=destroy.tfplan` / `tofu apply destroy.tfplan` in the smoke
environment; verify CDN, instance and IP deletion through AWS. Stopping the VM does not
stop billing. D2 remains partial: the final demo still needs availability through October 16
and a separately approved lifetime/budget. One short local ARM64 capacity probe is recorded
in `evals/reports/2026-10-03-memory-smoke.md`; deployed amd64 and sustained load are pending.

## Production

Not built here. Target design: [`docs/architecture.md`](../docs/architecture.md); the POC → production map: [`docs/poc_to_prod.md`](../docs/poc_to_prod.md). When we write it, it becomes more modules under `tofu/modules/` and an `envs/prod` environment shown with `tofu plan`.

## Layout

```
infra/
├── caddy/Caddyfile        TLS + routing (/api → api, / → web) for local and demo
└── tofu/
    ├── modules/
    │   ├── lightsail_smoke/  temporary VM + CDN + firewall
    │   └── demo_host/     EC2 + security group + IAM role + EIP (+ user_data: Docker)
    └── envs/
        ├── smoke/         temporary Lightsail environment
        └── demo/          the applied environment (tfvars git-ignored)
```
