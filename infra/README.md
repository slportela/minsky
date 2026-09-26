# infra

How the system runs **for the hackathon**. Two environments: local development and the POC demo on AWS.

> **The POC demo (one EC2 + Docker Compose) is not the production architecture.** The target is [`docs/architecture.md`](../docs/architecture.md). How each POC piece maps to it, database included: [`docs/poc_to_prod.md`](../docs/poc_to_prod.md). The same container images run in both.

```
 LOCAL (make up)                       POC DEMO (make demo-apply)             PRODUCTION (docs only)
 laptop · compose                      1 EC2 · compose · instance role        ECS · Aurora · Cognito · WAF · …
 self-signed TLS · ~/.aws for Bedrock  Let's Encrypt · no SSH (SSM)           see docs/architecture.md
```

## Local

```bash
cp .env.example .env     # set AWS_PROFILE (Bedrock access) and the organizer/bucket values
make up                  # build and start caddy, web, api, postgres → https://localhost
make logs                # follow logs
make down                # stop (data volumes are kept)
```

## Demo on AWS (one EC2 + compose)

The cheapest setup that is still reachable over HTTPS and uses least-privilege IAM:
- one EC2 instance in the default VPC, with an Elastic IP;
- only ports 80/443 open; no SSH, access through SSM Session Manager;
- IMDSv2 only;
- an instance role that may invoke only the listed Bedrock models and read our bucket.

```bash
cp infra/tofu/envs/demo/terraform.tfvars.example infra/tofu/envs/demo/terraform.tfvars   # fill in
make demo-plan           # tofu plan
make demo-apply          # tofu apply → prints public_ip and instance_id
aws ssm start-session --target <instance_id>
# on the host: git clone the repo, create .env, set DOMAIN, then:  docker compose up -d --build
```

Limits of the POC, stated honestly:
- single instance, single AZ;
- no autoscaling;
- the database lives on the instance disk (take EBS snapshots);
- deploys are manual.

That is acceptable for a two-week demo and is exactly what the production design removes.

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
