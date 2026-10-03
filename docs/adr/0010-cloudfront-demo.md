# 0010. A CloudFront hostname for the AWS demo

- Status: accepted for the POC; deployment pending
- Date: 2026-10-02

## Context

The team has no domain. The direct EC2/Caddy demo in ADR 0006 assumes a domain for
public TLS. The user selected CloudFront + EC2 instead of external DNS/hosting or a
Lambda migration. D2 requires a deployed link through 2026-10-16; S7 and P6.2 require
an honest distinction between demo and production. ADR 0001 remains in force.

## Decision

Opt into `enable_cloudfront` in the existing demo environment. CloudFront supplies its
`cloudfront.net` hostname and default public certificate. A VPC origin routes to the
same Compose stack on one private EC2, without an ALB. Caddy listens on private HTTP
port 80; CloudFront-to-Caddy has no application-level TLS in this POC. This is a
private origin connection, not a claim of end-to-end TLS. Production retains ACM TLS
at the ALB as documented in the target architecture.

A dedicated VPC has one public NAT subnet and one private app subnet in the same AZ.
NAT provides outbound access for bootstrap, SSM, image downloads and the interim
model provider. Only the CloudFront origin-facing managed prefix list may reach
Caddy; no database/API/frontend ports are published on the host. The prefix list is
not distribution-specific. IAM controls creation/access to this account's VPC origin.

Forward viewer headers except Host, cookies and query strings; allow POST; disable
all response caching and error caching. Backend identity/policy remains authoritative.
The direct-demo mode remains the default for existing configurations; the example
opts in explicitly. Do not enable on a deployed direct host without backing up its
volume and reviewing replacement in the plan.

## Consequences

- No purchased domain, external SaaS, local root-CA installation or model/runtime rewrite.
- CloudFront can be retained when the origin moves to the production ALB/ECS stack.
- NAT adds hourly/data charges. The hostname is included but deployment is not free.
  OpenTofu currently provisions ordinary usage-billed CloudFront; it does not subscribe
  to a flat-rate Free plan or assume account credits. Infrastructure needs a separately
  approved budget; the existing USD 1 model allowance is unchanged.
- Single instance/AZ, manual deploy, process-local conversation/case writes and trusted
  synthetic sessions remain limitations. Do not call production readiness or D2 complete
  until the deployed browser smoke succeeds.
- Caddy local TLS remains independently tested. CloudFront browser TLS is a new entry
  path and must be verified after deployment, including auth failures and no cached
  customer responses. No agent/policy change: no new L2 behavioral delta required.

References: [VPC origins](https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/private-content-vpc-origins.html),
[default certificate](https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/cnames-and-https-dedicated-ip-or-sni.html),
[authorization forwarding](https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/add-origin-custom-headers.html).
