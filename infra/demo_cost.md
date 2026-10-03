# Demo cost review — not an invoice or spending cap

Prepared 2026-10-02 for CloudFront + one private `t3.large`, 60 GiB gp3, one NAT
Gateway and one public IPv4, single AZ, assumed `us-east-1`, running continuously.
No resources have been created for this proposal. Confirm account/region rates and
CloudFront billing before apply; credits/free-tier eligibility are not assumed.

Illustrative fixed-resource estimate for **14 days / 336 hours**:

| Component | Assumed unit price (USD) | Estimate |
|---|---|---|
| EC2 t3.large Linux | 0.0832/hour | 27.96 |
| NAT Gateway | 0.045/hour | 15.12 |
| NAT public IPv4 | 0.005/hour | 1.68 |
| gp3 60 GiB | 0.08/GiB-month, prorated over 30 days | 2.24 |
| **Fixed-resource subtotal** | | **47.00** |

These are illustrative rates, not an account-specific quote. The 14-day interval is
an example, not the actual end-of-demo cutoff. Recalculate from apply time through
2026-10-16 inclusive and allow teardown time. Baseline is about USD 3.36/day.

**Excluded, variable and not capped:** CloudFront requests/data, NAT data processing
(illustratively USD 0.045/GB), internet egress, T3 surplus CPU credits, S3/lake and
snapshots, logs, model tokens, tax and any existing unrelated resources. The subtotal
is not the total or a guarantee of staying under it. Spending alerts are not a hard
cap; expected traffic and an infrastructure budget must be agreed before apply.

This OpenTofu change does not subscribe to a CloudFront flat-rate Free plan. AWS offers
such plans subject to eligibility; the Free Tier account plan and the CloudFront Free
flat-rate plan are different. Check the actual account rather than promising free hosting.

Sources checked: [EC2 example rates](https://docs.aws.amazon.com/prescriptive-guidance/latest/optimize-costs-microsoft-workloads/right-size-selection.html),
[VPC NAT/IPv4 pricing](https://aws.amazon.com/vpc/pricing/),
[EBS pricing examples](https://aws.amazon.com/ebs/pricing/),
[CloudFront plans](https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/flat-rate-pricing-plan.html).
EC2 source is illustrative guidance; retrieve the current Linux us-east-1 quote before apply.

After the retention deadline, back up needed data and review the destroy plan. Stopping
EC2 does not stop NAT Gateway, EBS or public-IP charges. Do not destroy early: D2 requires
availability through 2026-10-16.
