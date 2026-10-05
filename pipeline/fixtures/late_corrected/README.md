# Late and corrected partition fixture (P4.3)

Offline fixture only. Labels are by construction, not from production.

| File | Role |
|---|---|
| `base_transactions.csv` | Initial load: T1 amount 25.00, T2 amount 10.00 |
| `late_transactions.csv` | Late partition: adds T3 amount 40.00 |
| `corrected_transactions.csv` | Correction: T1 amount becomes 25.50 |

Expected merge by `transaction_id`: T1=25.50, T2=10.00, T3=40.00.
