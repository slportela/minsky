# infra

How the system runs **for the hackathon**. Two environments: local development and the POC demo on AWS.

> **The POC demo (one EC2 + Docker Compose) is not the production architecture.** The target is [`docs/architecture.md`](../docs/architecture.md). How each POC piece maps to it, database included: [`docs/poc_to_prod.md`](../docs/poc_to_prod.md). The same container images run in both.

```
 LOCAL (make up)                       POC DEMO (make demo-apply)             PRODUCTION (docs only)
 laptop · compose                      CloudFront · private EC2 · compose        ECS · RDS · Cognito · WAF · …
 self-signed TLS · ~/.aws for Bedrock  public TLS · no SSH (SSM)           see docs/architecture.md
```

## Local

```bash
cp .env.example .env     # set AWS_PROFILE (Bedrock access) and the organizer/bucket values
make up                  # build and start caddy, web, api, postgres → https://localhost
make logs                # follow logs
make down                # stop (data volumes are kept)
```

## Demo on AWS (CloudFront + one private EC2 + Compose)

The team selected this path because no purchased domain is available (ADR 0010).
`enable_cloudfront = true` creates a dedicated VPC, one private EC2, one public NAT
Gateway/Elastic IP, a VPC origin and a CloudFront distribution. The browser uses
`https://<id>.cloudfront.net/chat` with AWS's default certificate. Caddy serves HTTP on
port 80 inside the private VPC; this is **not application-level TLS to the origin**.
Production adds ALB/ACM TLS. The stack images and banking behavior are unchanged.

The existing direct-host mode is retained when the flag is false. **Switching an
existing direct host to private mode replaces EC2 and may lose its root-volume data.**
Back up PostgreSQL/EBS and review the replacement plan before apply; restore/load gold
and provision new short-lived synthetic sessions on the new host. Local Compose is unchanged.

### Prepare and review (no resource creation)

Use the team's own AWS profile, never organizer credentials. Set `AWS_PROFILE` in the
launching shell; agents do not inspect `.env`. Copy the example and enter the team lake
bucket and pinned allowed model/profile ARNs. The region must support VPC origins.
The AWS operator needs permission to create CloudFront's service-linked role and VPC
origin/network resources, as well as EC2/IAM. The instance role remains restricted to
SSM, the lake bucket and configured models. Default region: `us-east-1`.

```bash
cp infra/tofu/envs/demo/terraform.tfvars.example infra/tofu/envs/demo/terraform.tfvars
tofu -chdir=infra/tofu/envs/demo init -input=false
tofu -chdir=infra/tofu/envs/demo plan -out=demo.tfplan
```

Review every create/replace/destroy and the cost before applying. State and saved plans
are local/private; never commit or publish them. No actual AWS plan/apply has yet been
verified for this change. Offline provider-mocked plans do not prove account permissions,
regional availability, quotas, successful bootstrap, or deployed connectivity.

### Offline verification

```bash
tofu -chdir=infra/tofu/envs/demo init -backend=false -input=false
tofu -chdir=infra/tofu/envs/demo validate
tofu -chdir=infra/tofu/modules/demo_host init -backend=false -input=false
tofu -chdir=infra/tofu/modules/demo_host test
```

The module tests mock AWS and use plan-only runs: no credentials, paid resources or
provider API calls. They cover private ingress, viewer HTTPS, uncached authenticated
POST, unchanged error status and the legacy opt-out. Also validate the resolved demo
Compose configuration with `--env-file /dev/null` to avoid loading real secrets.
These checks and the full repository CI passed locally on 2026-10-02. Deployed checks
remain pending.

### Deploy after budget approval

```bash
tofu -chdir=infra/tofu/envs/demo apply demo.tfplan
tofu -chdir=infra/tofu/envs/demo output instance_id
tofu -chdir=infra/tofu/envs/demo output demo_url
aws ssm start-session --target <instance_id>
```

On EC2: clone the repository at the reviewed revision, provision the runtime `.env`
securely (including a strong database password and expiring synthetic test sessions),
then start with Docker Compose **2.24.4 or newer** (`!override` support):

```bash
docker compose -f compose.yaml -f compose.demo.yaml up -d --build
```

Do not use `compose.local.yaml` on EC2. Load/restore the gold `bank.*` data using the
pipeline's documented loader and the instance role. The current bootstrap installs
Docker, Git and Compose, but not the app, Python/uv or the gold data: these are manual
SSM deployment steps (see `../README.md`, "Data pipeline", and `pipeline/load_gold.py`). Set model settings according to ADR
0008 while Bedrock is blocked. The process environment is `demo`, not the local dotenv
loader; Compose injects only configured backend variables. EC2 does not mount `~/.aws`.

CloudFront routes all paths to Caddy. All responses bypass caching, errors retain their
status and zero cache TTL, POST is allowed, and `Authorization` reaches the backend.
No SPA fallback rewrites API errors. The security group allows only CloudFront's managed
origin-facing prefix list on port 80, not public CIDRs. This consumes 55 ingress rule
quota slots; check the account quota before apply. SSM is the administrative entry.

### Verify, then mark D2 complete

Follow `docs/integrated_smoke.md` at the output URL. Check health/frontend, anonymous
and expired credentials, D09 read-back, ambiguity and handoff, isolation across sessions,
POST/history replay, and that repeated requests do not reuse cached customer responses.
Inspect errors as errors; do not rewrite them to HTTP 200. No browser TLS bypass.
Record source revision, account deployment, observations and model budget. A successful
frontend page alone does not close the deployed gate.

### Cost and lifetime

The generated hostname/default certificate needs no domain purchase. This configuration
uses CloudFront usage billing: **no flat-rate Free plan subscription or account credit is
assumed**. A free CloudFront plan does not make EC2/NAT/EBS free. See the review estimate
in `infra/demo_cost.md`; confirm actual regional/account rates and expected traffic before
apply. USD 1 covers the previously authorized model diagnostics only.

Keep the demo available through **2026-10-16 inclusive**; schedule teardown after that
date, preserve necessary evidence/data first, and review `tofu plan -destroy` before
approval. Destroy removes the EC2/root volume and NAT/IPv4 charges. Stopping EC2 alone
still leaves NAT, EBS and IPv4 billing. CloudFront/VPC-origin deletion may take time.

Limits: one instance/AZ, no autoscaling, manual deploy, database on EBS, process-local
case/conversation state, synthetic authentication and no dedicated WAF/rate limiting.

## Production

Not built here. Target design: [`docs/architecture.md`](../docs/architecture.md); the POC → production map: [`docs/poc_to_prod.md`](../docs/poc_to_prod.md). When we write it, it becomes more modules under `tofu/modules/` and an `envs/prod` environment shown with `tofu plan`.

## Layout

```
infra/
├── caddy/Caddyfile        TLS + routing (/api → api, / → web) for local and demo
└── tofu/
    ├── modules/
    │   └── demo_host/     EC2 + security group + IAM role + EIP (+ user_data: Docker)
    └── envs/
        └── demo/          the applied environment (tfvars git-ignored)
```
