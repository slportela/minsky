# Temporary smoke cost review

Prepared 2026-10-03. Approved infrastructure allowance: **USD 5 for at most six hours**,
then delete resources. Model diagnostics have a separate cumulative USD 1 allowance.
No resources have been created yet; the own-account profile and real plan are pending.

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

The short test is separate from D2's deployed-demo retention through October 16. A
longer-lived final demo needs its own cost calculation and approval. Delete the CDN,
instance and static IP after preserving test evidence; verify deletion in AWS. Stopping
a Lightsail VM still incurs charges. Unattached static IPs can incur charges.

Sources: [AWS pricing](https://aws.amazon.com/lightsail/pricing/),
[billing and deletion](https://docs.aws.amazon.com/lightsail/latest/userguide/amazon-lightsail-frequently-asked-questions-faq-billing-and-account-management.html).
