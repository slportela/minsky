# What can be learned from the data: findings

A sweep of ~30 hypotheses for predictive structure across the silver tables (everything except `complaints`, which is covered in [`disputes_findings.md`](disputes_findings.md)). Reproduce with [`pipeline/notebooks/explore_learnability.ipynb`](../pipeline/notebooks/explore_learnability.ipynb) (every number below appears there with its query). Measured on the full silver dataset (2026-09-30).

Method: each hypothesis is proposition → query → verdict. Verdicts are **signal** (strong, stable, usable), **structural** (imposed by data generation, e.g. one column derived from another), **transformation** (deterministic function of another column), **leakage** (gives the target away; exclude from features) or **none** (no useful association, with n and effect sizes shown). With ~30 hypotheses some weak "effects" appear by chance; only large effects (tens of percentage points, or near-deterministic) that hold in cross-cuts are marked as signal.

## Summary

| # | Relation | Verdict | Evidence (n, effect) | Predictive use |
|---|---|---|---|---|
| H1 | `segment` ~ `credit_score`/income | structural | 150k; bands 600/649/699/797 ± 40 | None (generator artifact) |
| H2 | `credit_score` ↔ income | structural (Simpson) | r=0.356 overall, ~0 within segment | None within segment |
| H3 | `customer_status` (churn) ~ behavior | none | 150k; differences < 3% on everything | No churn model possible |
| H4 | demographics ~ `credit_score` | none | 150k; Δ < 2 points | — |
| H5 | delinquency ~ utilization | none | 400k; ~15% at every level | — |
| H6 | delinquency ~ income/score | none | 400k; flat | No credit-risk model |
| H7 | `interest_rate` ~ product type | structural | 400k; constant per type | — |
| H8-H9 | fraud ~ amount/hour | none | 4.4M; ~0.1% flat | — |
| H10-H11 | decline/fraud ~ channel/country/merchant | none | 4.4M; 5% / 0.1% flat | — |
| **H12** | **FCR ~ `contact_reason`** | **signal** | **686k; 43.6% (Queja) → 91.5% (Transaccional); stable in country/year/month/channel/tenure; entropy 0.78→0.66 bits; flag Queja: 56.4% precision, 2.4× lift** | **Viable target/feature (router, triage, risk)** |
| H13 | FCR ~ call duration | partial (post-hoc) | 686k; 90% → 57% | Monitoring, not start-of-call prediction |
| H14 | FCR ~ detected sentiment | none (confounded) | 686k; global effect vanishes within reason | — |
| H15 | FCR ~ agent | none | 686k; 76.2–77.2% | — |
| H16 | `requires_followup` ~ `was_resolved` | partial transformation | 686k; (false,false) never occurs | Redundant target |
| **H17** | **`main_score` ~ `was_resolved` × survey type** | **signal (near-deterministic)** | **213k; 3 vs 2 (CSAT/CES), 6 vs 3 (NPS); cell-mode 59.3% vs 42.5%; residual sd 0.54–0.82** | **Viable target, but only post-call** |
| H18 | `main_score` ~ questions 1–3 | none | 213k; r ≤ 0.01 | — |
| H19 | `main_score` ~ `comment_sentiment` | partial (templates) | 213k; 2.5 vs 4.0 | Text demo, not learning |
| H20 | `nps_category` ~ `main_score` | transformation | 213k; deterministic | — |
| **H21** | **campaign funnel ~ channel** | **signal (open step only)** | **1.75M; SMS 50% vs Email 30% opens; between-campaign sd 0.62; funnel closes exactly** | **Bounded channel prioritization** |
| J1–J7 | `digital_events` (15.6M) ~ everything | none | engagement, errors, UTM, duration: all flat; UTM rows never convert | — |
| H22 | template ~ conversion | none (noise) | 1.75M; template position flat 0.55–0.58% | — |
| H23 | engagement ~ `accepts_marketing` | none (quality finding) | 1.75M; 38.6% = 38.6% | — |
| H24 | `customer_text` ~ `contact_reason` | none | 171k; 42 texts, purity 34.9% | Router needs generated text |
| H25 | `main_topics` ~ `contact_reason` | **leakage** | 171k; 100% identical per row | Exclude from features |
| H26-H27 | call ↔ complaint | none | 686k / 67k; ~3% and ~0.3% | — |
| H28 | accent match ~ outcome | none | 482k; 76.7% = 76.7% | — |
| H29 | campaign ~ later transaction | none | 1.75M; 16.5% flat | — |
| H30 | complaint history ~ score | none | 213k; flat | — |
| H31 | `avg_csat` ~ realized satisfaction | none (quality finding) | 1,090 agents; r = −0.03 | — |

## The three usable signals

**H12 — First-contact resolution follows the contact reason.** `was_resolved` is 91.5% for `Transaccional` and `Producto` (89.6%), falling to 69.9% (`Técnico`), 65.2% (`Comercial`), 60.2% (`Retención`) and 43.6% (`Queja`); n = 686,296. The effect holds within every duration band and every sentiment band, and is **stable in every cut tested**: within ±0.5 points across Argentina/Colombia/Mexico, across 2023–2026, across calendar months, across customer tenure bands and across interaction channels (76.5–77.8% everywhere). Quantified: knowing the reason lowers the entropy of `was_resolved` from 0.784 to 0.657 bits (−16%); the per-reason majority rule reaches 78.8% accuracy vs 76.7% for "always resolved" (modest in accuracy — the signal is *calibration*, not hard classification). Actionable framing: flagging `Queja` as "risk of not resolving in first contact" gives **precision 56.4%, recall 41.2% against a 23.4% base rate — 2.4× lift**. Inside `Queja` nothing else moves the rate (priority, repeat-complainer: 36–42% on small joined n). This is the feature for early escalation / follow-up offers, and it is known at call start.

**H17 — Satisfaction is determined by resolution.** Within each survey type, the score distribution shifts around a fixed center depending on `was_resolved`: CSAT/CES 2.0 (unresolved) vs 3.0 (resolved), NPS 3.0 vs 6.0; n = 212,759. The 6-cell lookup (`survey_type` × `was_resolved`) predicts the modal score 59.3% of the time vs 42.5% for the global mode (+16.8 points), and the residual inside each cell is small, homoscedastic noise (sd 0.54 CSAT/CES, 0.82 NPS) that **nothing** explains — not reason, sentiment, agent, segment or channel. The whole "satisfaction model" is that 6-entry cell. Near-deterministic and only known after the interaction: usable as an expected-satisfaction estimate for handoff decisions, not as an independent business insight.

**H21 — Channel moves only the open rate.** SMS opens 49.9%, Push 40.1%, Email 30.0% (n = 1.75M); WhatsApp and Voice record no opens. Delivery is ~94% everywhere, click | open ≈ 20% and conversion | click ≈ 10% are constant across channels **and** across devices, and the funnel arithmetic closes exactly (estimated 5.64 vs actual 5.64 conversions per 1,000 Email sends; 9.39 vs 9.37 for SMS). Between campaigns of the same channel, open rates vary 27.9–31.7% (sd 0.62) — noise. So a channel choice model improves opens and nothing else; there is no conversion uplift to learn, and `open_device`, `open_country`, `click_date` and `conversion_value` are post-event columns (leakage, not features).

## Structural and leakage findings (report, don't model)

- **Simpson's paradox (H2).** `credit_score` and `estimated_monthly_income` correlate r = 0.356 overall but ≈ 0 within each segment. Both are generated from `segment` (H1: Basic 600 / Student 649 / Plus 699 / Premium 797, sd ≈ 40). Any "credit score predicts income" claim is an artifact of the segment mix.
- **`main_topics` is `contact_reason` (H25).** The two columns match on 100% of the 171,321 transcript rows, and `detected_intents` is constant (`consulta_general`). This is the leakage check that `ml/README.md` demands: the router must not see these columns as features. Note the transcripts are also only two balance-inquiry templates with 42 variants (H24), so text alone cannot predict the reason either (majority purity 34.9%).
- **`requires_followup` is nearly determined by `was_resolved` (H16).** The pair (not resolved, no follow-up) never occurs. The two columns are not independent targets.
- **`nps_category` is a transformation of `main_score` (H20)** (detractor 2–6, passive 7, no promoters — scales are truncated, see `known_issues.md`).
- **`interest_rate` is constant per product type (H7).**

## `digital_events`: the untested table (15.6M rows)

Not in silver; read straight from `data/bronze/data/digital_events/**/*.csv` (only the needed columns — see `known_issues.md`). Everything in it is flat:

| Hypothesis | Result |
|---|---|
| Digital engagement (events, sessions) ~ `customer_status` | none: 79.1–79.4 events, 9.8 sessions for every status |
| Error / Purchase rate ~ `segment` | none: 2.28% / 1.52% identical in all four segments |
| Error event → complaint within 3 days | none: 0.12% (n = 272,663 error events) |
| Session duration ~ platform / mobile | none: 152.3–152.9 s everywhere |
| Engagement volume ~ survey score / complaints | none |
| UTM source/medium ~ Purchase / Error | **none at all**: UTM-tagged rows have 0.000% Purchase and 0.000% Error — the UTM parameters are decorative |

So the whole clickstream adds no learnable relation to the sweep.

## New data-quality surprises

- **`accepts_marketing` is not honored (H23).** Customers who do not accept marketing are sent campaigns at the same rate and open/convert identically (38.6% vs 38.6% opens; 0.57% vs 0.55% conversions). Consent does not filter sends or engagement.
- **`service_agents.avg_csat` does not match reality (H31).** The declared agent attribute correlates r = −0.03 with the satisfaction actually measured in their surveys (1,090 agents with ≥ 30 surveys). Do not use `avg_csat` as a routing feature or quality signal.
- **Churn has no behavioral footprint (H3).** `customer_status` (Active/Inactive/Suspended/Closed) is indistinguishable on transaction volume, complaint count, products, delinquency or recency — and equally on digital engagement (J2). A churn model is not possible from this data.
- **UTM parameters are decorative (J6).** The 839,045 rows carrying `utm_source`/`utm_medium` have zero `Purchase` and zero `Error` events: campaigns cannot be attributed through the clickstream.
- **Campaign event columns are post-hoc (I1).** `open_device`/`open_country` are filled only when `was_opened`, `click_date` only when `was_clicked`, `conversion_value` only when `had_conversion` — leakage columns for any funnel model.

Both are recorded in [`known_issues.md`](known_issues.md).

## Implications for the learned component

1. The dispute-workflow conclusion of `disputes_findings.md` stands: there is no case-level target in the historical data for dispute outcomes. Of the remaining candidates, **FCR ~ contact reason is the only one with signal, a legitimate pre-call feature and a natural place in the system** (early escalation / follow-up offer), and it maps directly onto the planned router (intent + reason) — the router's labels would double as the feature for the FCR model.
2. A satisfaction model would be a lookup of `was_resolved`, and a channel model only shuffles opens. If either is shown, present it as a baseline comparison, not as a discovery.
3. Any model built on `main_topics`, `detected_intents`, `fraud_score` or `nps_category` is trivial by leakage/transformation and cannot count as the learned component.
4. Negative results matter for the story: ~25 of ~30 hypotheses are flat or structural, which is the quantitative backing for "the dataset grounds the system but does not teach most business relationships".
