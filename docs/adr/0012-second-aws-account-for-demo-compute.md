# 0012. A second AWS account of the same owner for demo compute

- Status: accepted for the demo hosting; smoke deployed 2026-10-04, final retained demo pending
- Date: 2026-10-04
- Extends: 0001 (hosting only; data, models and the lake are unchanged)

## Context

ADR 0001 keeps everything in one AWS account. That account (the owner's older one, which
also holds the S3 lake) has applied compute quotas of **0** for EC2 standard vCPU, Lightsail
instances (checked in us-east-1, us-east-2 and us-west-2) and Fargate vCPU (us-east-1), below
the AWS defaults of 5, 20 and 6. It is not part of an AWS Organization, so an organization
policy does not explain it. Service Quotas only accepts requests above the default, so the
owner cannot restore the default alone, and the account has no paid support plan. The
Lightsail apply of ADR 0011 failed on that limit (`evals/reports/2026-10-03-lightsail-deployment.md`).

D2 requires a deployed link on 2026-10-05 that stays up until 2026-10-16.

On 2026-10-04 the owner opened a second account. Checked read-only the same day: AWS Free
plan, active, USD 180 credits until 2027-04-04, default quotas (EC2 standard vCPU 5 in
us-east-1 and 16 in us-east-2, Lightsail instances 10, Fargate vCPU 6 and 12), and a
`t3.micro` launched and terminated without a limit error. A real plan of `envs/smoke`
against it shows 5 creates, no changes or deletions.

## Decision

Run the temporary smoke of ADR 0011, and the retained demo if the smoke passes, in the
second account. It hosts **compute only**:

- The S3 lake, bronze, the organizer source and Bedrock stay where they are. Nothing is
  copied between accounts.
- The VM receives prebuilt linux/amd64 images and the synthetic gold read models over SSH
  (as in ADR 0011). It never receives organizer credentials, the workstation `.env`, or
  keys of the primary account.
- Models stay on the interim provider of ADR 0008; this ADR does not change that.
- Access: a dedicated IAM user with the permissions the module needs, its keys only in an
  AWS CLI profile, never in `.env` (which holds the organizer keys). Each plan and apply is
  reviewed before it runs.

If Lightsail is not available on the Free plan, the fallback is one EC2 `t3.small`
(a Free-plan-eligible type) running the same Compose stack, with a new reviewed plan.

This is not the production target. Production keeps the single-account design in
`docs/architecture.md`.

## Consequences

- Unblocks D2 without waiting for the older account's restriction, which AWS has to lift.
- Two accounts to track. Costs (about USD 14.50 per month for the VM and CDN) draw on the
  credits; deletion stays operator-run and there is no hard spending cap.
- The Free plan closes the account when credits run out or after six months (2027-04-04),
  and excludes some services. Whether Lightsail is among them is unverified until apply.
  The date is after 2026-10-16 but is not a production plan.
- Rule 6 of `AGENTS.md` ("our AWS account only") is read as "accounts owned by the team",
  with data and models kept in the primary one. If a reviewer reads it more strictly, this
  ADR is the thing to reject.
- How we would know it was wrong: the apply fails on a Free-plan restriction (use the EC2
  fallback), or the primary account's quotas are restored first (the smoke can return there,
  which needs a fresh plan).

## Outcome (2026-10-04)

The OpenTofu apply of `envs/smoke` was **denied**: the Free plan runs under an AWS-managed
service control policy that explicitly denies Lightsail `CreateInstances` and `AllocateStaticIp`
through the API, even for an administrator user (nothing was created; state stayed empty). The
same actions succeeded from the Lightsail console, so the stack was built by hand:

- Lightsail instance (2 GB, dual-stack, Ubuntu 22.04) in **us-east-2** (Ohio), created in the console;
  a static IPv4 attached; firewall set by CLI to SSH from one operator /32 only, no IPv6 SSH, HTTP open.
- Docker, Compose 2.39.4 and the two linux/amd64 images were installed and built **on the VM**
  (swap added), which also resolved the failed cross-build on the workstation.
- The six gold read models (4,425,008 transactions among them) were loaded into the Compose
  PostgreSQL through an SSH tunnel with `pipeline/load_gold.py`; counts matched the source.
- The CDN was created by CLI in us-east-1 (Lightsail distributions are managed there) with the
  Ohio instance as origin.

Defects found by creating real resources, none visible to the provider-mocked tests, fixed in the
module: the launch script runs under dash (`set -o pipefail` aborted it), the API rejects
`default_ttl = 0`, and it rejects the forwarded-headers option `all` (an allow-list that includes
`Authorization` and `Host` is used; `Content-Type` and POST `Authorization` are forwarded by default).

The module was not applied as written, so the deployed stack is not reproducible from `envs/smoke`
alone. Moving to the paid plan (credits are kept) would lift the policy and allow the module to apply.
