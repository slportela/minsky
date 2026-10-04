# infra

How the system runs **for the hackathon**: local development, the retained EC2 demo,
and a separate temporary Lightsail smoke.

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

## Retained EC2 demo (`envs/demo`)

`make demo-plan` and `make demo-apply` still target this environment. It creates one
EC2 in the default VPC, an Elastic IP and an instance role limited to SSM, our lake
bucket and the configured Bedrock models. Only ports 80/443 are open; administration
uses SSM, with IMDSv2 required. This path needs a DNS name pointing at the Elastic IP
for Caddy's publicly trusted ACME certificate; it does not supply a free hostname.
The confirmed `personal` account currently has zero standard EC2 vCPU quota too.
Review quota, costs and the plan before apply; the temporary Lightsail budget does
not authorize retaining this EC2 deployment through October 16.

```bash
cp infra/tofu/envs/demo/terraform.tfvars.example infra/tofu/envs/demo/terraform.tfvars
# Fill in our lake bucket/model ARNs and approved ingress CIDRs.
# Export the confirmed own-account AWS_PROFILE in the launching shell.
make demo-plan
make demo-apply
aws ssm start-session --target <instance_id>
```

On EC2, deploy the reviewed repository revision and provision a minimal runtime env
file securely, including a strong database password and expiring synthetic sessions.
Set `DOMAIN` to the DNS name; load the gold read models with the pipeline loader and
the instance role. Use `sudo docker compose up -d --build` with `compose.yaml` alone;
do not use the Lightsail `compose.demo.yaml` or local AWS credential mounts. Bootstrap
installs Docker/Compose; repository/data/runtime provisioning remain deployment steps.
The EC2 instance role supports Bedrock; the interim model exception is ADR 0008.

Verify browser TLS and all integrated paths before reporting D2 complete. The final
demo must stay available through October 16 inclusive, with a separately approved
budget and cleanup date. Single instance/AZ, manual deploy and local database remain
POC limitations; application case writes are still in memory.

## Temporary remote smoke on Lightsail (ADR 0011)

No domain purchase: a 2 GB / 60 GB IPv4 VM runs Caddy, Next.js, FastAPI and PostgreSQL.
A Lightsail CDN supplies `https://<id>.cloudfront.net/chat` and its public certificate.
Viewer TLS ends at the CDN; the origin uses **public HTTP**. This synthetic diagnostic
does not verify production origin TLS/isolation. The original EC2 environment stays
unchanged; use the separate `infra/tofu/envs/smoke` state.

### Prepare and verify

Use a confirmed profile from our own account (for the smoke, the second account of ADR 0012;
the primary account has zero compute quotas); never organizer credentials. Agents must
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
HTTP-only host exposure, all request methods/headers/cookies/queries and zero cache TTLs.
These are configuration assertions, not proof of runtime caching or header behavior.
The own-account API confirmed the 2 GB USD 12/month VM and USD 2.50/month CDN.
The original real plan contained five creates and no replacement/deletion; apply was
attempted on 2026-10-03 but blocked by the account Lightsail limit of zero.
Review a fresh plan before any retry of this updated configuration.
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

Bootstrap pins **Compose 2.39.4** and verifies the official x86_64 asset SHA-256 before
installation. It adds `ubuntu` to the Docker group and makes `/opt/minsky` writable by
that user. Reconnect SSH after bootstrap so the group membership is active; verify
`id -nG` includes `docker` and `docker compose version` reports the pinned release.
Docker-group access grants host-level privileges; keep SSH limited to operator /32s.

The override requires linux/amd64 for all four services. After loading app images,
verify the tags with `docker image inspect --format '{{.Os}}/{{.Architecture}}' <tag>`
before starting; both must report `linux/amd64`. On the host:

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

**No automatic teardown or AWS hard spending cap is installed.** The operator must
record apply time/deletion deadline, monitor usage and execute cleanup; disabled
distributions or stopped VMs are not deletion. The six-hour/USD 5 limits are operational.

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
