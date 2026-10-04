# Temporary smoke cost review

Prepared 2026-10-03. Approved infrastructure allowance: **USD 5 for at most six hours**,
then delete resources. Model diagnostics have a separate cumulative USD 1 allowance.
The confirmed `personal` profile and real plan were verified. Apply was blocked by
zero instance quota in the primary account; the sole allocated static IP was deleted.
No VM/CDN was created in the primary account. Any brief unattached-IP charge requires
billing verification. The second account (ADR 0012) also had the Lightsail API blocked by
org policy; the stack was created by hand on 2026-10-04 — see `infra/README.md` and
`evals/reports/2026-10-04-lightsail-second-account.md`.

| Component | Published plan | Conservative six-hour estimate |
|---|---|---|
| Lightsail IPv4 VM, 2 GB / 60 GB | USD 12/month, hourly up to monthly ceiling | approximately USD 0.10 |
| Lightsail CDN, 50 GB | USD 2.50/month | reserve the full USD 2.50 |
| Static IPv4 attached to running instance | included | USD 0 |
| **Estimated infrastructure reservation** | before tax/overages | **USD 2.60** |

The VM approximation uses 720 hours/month; verify the actual hourly bundle rate with
`get-bundles`. Do not assume CDN proration, free-trial eligibility or account credits.
Transfer/request overages, taxes, snapshots and unrelated resources are excluded.
Do not create snapshots or additional paid services for this diagnostic. Stop if the
real plan or expected usage cannot fit USD 5. Alerts are not an AWS spending cap.

Delete the CDN, instance and static IP after preserving test evidence; verify deletion
in AWS. Stopping a Lightsail VM still incurs charges. Unattached static IPs can incur
charges. A retained-demo cost calculation is in the section below.

Sources: [AWS pricing](https://aws.amazon.com/lightsail/pricing/),
[billing and deletion](https://docs.aws.amazon.com/lightsail/latest/userguide/amazon-lightsail-frequently-asked-questions-faq-billing-and-account-management.html).

## Retained demo (2026-10-04 – 2026-10-16) — PENDING owner approval

The smoke stack is retained through the demo window. Costs draw from the Free-plan credits
(approximately USD 180 remaining at 2026-10-04; plan ends 2027-04-04 or when credits run out).

| Component | Daily rate | Window (2026-10-04 through 2026-10-16 inclusive, 13 calendar days) | Estimate |
|---|---|---|---|
| Lightsail VM (small_3_0, 2 GB / 60 GB) | USD 0.40/day (USD 12/month ÷ 30) | reserve 13 days | USD 5.20 |
| Lightsail CDN (small_1_0) | USD 0.08/day (USD 2.50/month ÷ 30) | full monthly fee may be reserved | USD 2.50 |
| **Retained demo total (conservative)** | | | **USD 7.70** |

Reserve 13 full days to cover October 16 inclusive and teardown on October 17;
actual VM charges depend on creation and deletion times. As ADR 0011 assumed, the CDN
monthly fee may be reserved in full rather than prorated.
The cumulative model-call budget remains the separate **USD 1** allowance of ADR 0008.
There is no automatic deletion and no hard spending cap; teardown is operator-run after
2026-10-16 (see `infra/README.md`). Stopping the VM does not stop billing.
