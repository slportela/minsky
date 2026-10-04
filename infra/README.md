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

When this Tofu apply path becomes available: delete the deployment within six hours.
Preserve evidence, then review and apply `tofu plan -destroy -out=destroy.tfplan` /
`tofu apply destroy.tfplan` in the smoke environment; verify CDN, instance and IP deletion
through AWS. Stopping the VM does not stop billing. One short local ARM64 capacity probe is
recorded in `evals/reports/2026-10-03-memory-smoke.md`; deployed amd64 capacity under load
remains pending. The stack currently deployed was built by hand; see the next section.

## Smoke stack built by hand (2026-10-04)

The OpenTofu apply of `envs/smoke` was denied by the Free plan's AWS-managed service control
policy (`CreateInstances` and `AllocateStaticIp` through the Lightsail API are blocked; console
and permitted CLI calls succeeded). The stack was created by hand on 2026-10-04 using the second
AWS account of the same owner (ADR 0012), and is retained for the demo window. It is not
reproducible from `envs/smoke` alone; moving to a paid plan (credits are kept) would lift the
policy restriction and allow the module to apply. Report:
`evals/reports/2026-10-04-lightsail-second-account.md`.

### Resource inventory

| Resource | Name | Region / AZ | Details |
|---|---|---|---|
| Lightsail instance | minsky-smoke | us-east-2 / us-east-2a | small_3_0: 2 GB / 2 vCPU / 60 GB SSD; Ubuntu 22.04; dual-stack |
| Static IPv4 | minsky-1 | us-east-2 | attached to minsky-smoke |
| Lightsail CDN distribution | minsky-smoke-cdn | us-east-1 | small_1_0 (USD 2.50/month); HTTP-only origin (Ohio instance); HTTPS viewer |

No IPs, account IDs or CDN domains are recorded in version control.

### Rebuild steps

These steps were not re-run end to end after the initial build.
Placeholders: `<VM_IP>` static IPv4 of the instance; `<OPERATOR_CIDR>` operator public IPv4 /32;
`<KEY_FILE>` path to the Lightsail SSH private key; `<POSTGRES_PASSWORD>` strong random password.

1. **Create the Lightsail instance in the AWS console** (the API is blocked by org policy).
   Account: second account (ADR 0012), profile `minsky-new`.
   Region: `us-east-2`. Blueprint: Ubuntu 22.04 LTS. Bundle: `small_3_0` (2 GB / 2 vCPU / 60 GB).
   Networking: dual-stack (IPv4 + IPv6). Name: `minsky-smoke`. Attach your SSH key pair.

2. **Allocate and attach a static IPv4 in the console.**
   Name: `minsky-1`. Attach to `minsky-smoke`. Record the IP as `<VM_IP>`.

3. **Set the firewall by CLI** (these calls are not blocked by org policy):
   ```bash
   AWS_PROFILE=minsky-new AWS_PAGER="" aws lightsail put-instance-public-ports \
     --region us-east-2 --instance-name minsky-smoke \
     --port-infos '[
       {"fromPort":22,"toPort":22,"protocol":"tcp",
        "cidrs":["<OPERATOR_CIDR>"],"ipv6Cidrs":[]},
       {"fromPort":80,"toPort":80,"protocol":"tcp",
        "cidrs":["0.0.0.0/0"],"ipv6Cidrs":["::0/0"]}
     ]'
   ```
   SSH is restricted to one operator /32 over IPv4 only; no IPv6 SSH; HTTP open.

4. **Install Docker and Compose on the VM.**
   The launch script fails under the system `dash` shell; run it explicitly with `bash`:
   ```bash
   scp -i <KEY_FILE> infra/tofu/modules/lightsail_smoke/user_data.sh ubuntu@<VM_IP>:/tmp/
   ssh -i <KEY_FILE> ubuntu@<VM_IP> "bash /tmp/user_data.sh"
   ```
   Reconnect after bootstrap; verify `id -nG` includes `docker` and
   `docker compose version` reports the pinned release (2.39.4).

5. **Add a 2 GB swapfile** (the VM has no swap by default):
   ```bash
   ssh -i <KEY_FILE> ubuntu@<VM_IP> \
     "sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile \
      && sudo mkswap /swapfile && sudo swapon /swapfile \
      && echo '/swapfile swap swap defaults 0 0' | sudo tee -a /etc/fstab"
   ```

6. **Copy the source to the VM** using `git archive`:
   ```bash
   git archive HEAD -- pyproject.toml uv.lock backend prompts frontend \
     infra/caddy compose.yaml compose.demo.yaml \
   | ssh -i <KEY_FILE> ubuntu@<VM_IP> \
     "sudo mkdir -p /opt/minsky/src \
      && sudo tar -xf - -C /opt/minsky/src \
      && sudo chown -R ubuntu:ubuntu /opt/minsky/src"
   ```

7. **Build the images on the VM.** The workstation cannot cross-build the Next.js
   frontend for `linux/amd64`; build on the VM instead:
   ```bash
   ssh -i <KEY_FILE> ubuntu@<VM_IP> \
     "cd /opt/minsky/src \
      && docker build -f backend/Dockerfile -t minsky-api:smoke . \
      && docker build -t minsky-web:smoke frontend"
   ```

8. **Provision config files** at `/opt/minsky/`, all mode `0600`, owned by `ubuntu`.
   Never upload the workstation `.env`, AWS profiles, organizer credentials or primary-account keys.
   - `images.env`: `MINSKY_API_IMAGE=minsky-api:smoke`; `MINSKY_WEB_IMAGE=minsky-web:smoke`; `AWS_REGION=us-east-2`
   - `db.env`: `POSTGRES_PASSWORD=<POSTGRES_PASSWORD>`
   - `runtime.env`: model key (ADR 0008); `TEST_SESSIONS` JSON for three synthetic customers, expiring ≤ two weeks ahead
   - `test-tokens.txt`: bearer tokens from `TEST_SESSIONS`, one per line, labeled with scenario

9. **Start the Compose stack:**
   ```bash
   ssh -i <KEY_FILE> ubuntu@<VM_IP> \
     "cd /opt/minsky/src \
      && docker compose \
           --env-file ../images.env --env-file ../db.env --env-file ../runtime.env \
           -f compose.yaml -f compose.demo.yaml up -d"
   ```

10. **Load the gold read models** via an SSH port-forward tunnel to the Postgres container.
    PostgreSQL is not host-published to the internet; tunnel through SSH:
    ```bash
    ssh -i <KEY_FILE> -L 15432:localhost:5432 ubuntu@<VM_IP> -fN
    GOLD_DATABASE_URL="postgresql://postgres:<POSTGRES_PASSWORD>@localhost:15432/minsky" \
      python pipeline/load_gold.py
    ```
    Expected duration: approximately 18 minutes, most of it in transactions.
    Verify counts: customers 150,000; products 400,000; transactions 4,425,008;
    customer_complaint_stats 150,000; resolution_benchmarks 26; dispute_scenarios 160.

11. **Create the CDN distribution by CLI** (Lightsail distributions are always managed in us-east-1):
    ```bash
    AWS_PROFILE=minsky-new AWS_PAGER="" aws lightsail create-distribution \
      --region us-east-1 \
      --distribution-name minsky-smoke-cdn \
      --bundle-id small_1_0 \
      --origin '{"name":"minsky-smoke","regionName":"us-east-2","protocolPolicy":"http-only"}' \
      --default-cache-behavior '{"behavior":"dont-cache"}' \
      --cache-behavior-settings '{
        "defaultTTL":1,"minimumTTL":0,"maximumTTL":1,
        "allowedHTTPMethods":"allow-all",
        "cachedHTTPMethods":"GET-HEAD",
        "forwardedCookies":{"option":"all"},
        "forwardedQueryStrings":{"option":true},
        "forwardedHeaders":{
          "option":"allow-list",
          "headersAllowList":["Authorization","Host","Origin","Accept","Accept-Language","Referer"]
        }
      }'
    ```
    Known API restrictions: `defaultTTL 0` is rejected by the API (use `1`); `forwardedHeaders`
    option `"all"` is also rejected (use `"allow-list"`). `Content-Type` and POST `Authorization`
    are forwarded by default.

### Teardown

> **NOT EXECUTED; operator-run after the demo window. Suggested after 2026-10-16.**

Notes before deleting:
- Test tokens expire 2026-10-17; there is no harm in deleting the stack before expiry.
- The model key lives only on the VM and is permanently deleted with the instance.
- The Free plan closes the account when credits run out (approximately USD 180 remaining
  at 2026-10-04; plan ends 2027-04-04 at the latest). There is no automatic deletion and
  no hard spending cap. Stopping the VM does not stop billing; only deletion does.

Delete in this order:

```bash
# 1. Delete the CDN distribution (managed in us-east-1).
#    The CDN may take several minutes to disable; the delete call may be rejected while
#    the distribution state is IN_PROGRESS. Re-run the get-distributions check below
#    until the distribution is gone before continuing.
AWS_PROFILE=minsky-new AWS_PAGER="" \
  aws lightsail delete-distribution \
    --region us-east-1 \
    --distribution-name minsky-smoke-cdn

# 2. Detach the static IP from the instance.
AWS_PROFILE=minsky-new AWS_PAGER="" \
  aws lightsail detach-static-ip \
    --region us-east-2 \
    --static-ip-name minsky-1

# 3. Release the static IP.
AWS_PROFILE=minsky-new AWS_PAGER="" \
  aws lightsail release-static-ip \
    --region us-east-2 \
    --static-ip-name minsky-1

# 4. Delete the instance.
AWS_PROFILE=minsky-new AWS_PAGER="" \
  aws lightsail delete-instance \
    --region us-east-2 \
    --instance-name minsky-smoke
```

Verify deletion (all commands must return an empty list):

```bash
# CDN gone (us-east-1)
AWS_PROFILE=minsky-new AWS_PAGER="" \
  aws lightsail get-distributions --region us-east-1 \
    --query 'distributions[?name==`minsky-smoke-cdn`]'

# Static IP gone (us-east-2)
AWS_PROFILE=minsky-new AWS_PAGER="" \
  aws lightsail get-static-ips --region us-east-2 \
    --query 'staticIps[?name==`minsky-1`]'

# Instance gone (us-east-2)
AWS_PROFILE=minsky-new AWS_PAGER="" \
  aws lightsail get-instances --region us-east-2 \
    --query 'instances[?name==`minsky-smoke`]'
```

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
