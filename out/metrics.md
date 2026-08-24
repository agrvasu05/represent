# RePresent — evaluation results

Held-out split only · 5000 mandates/seed x 5 seeds · split 60/40 by id-hash · common random numbers across policies

## Headline: recovery vs compliance

| Policy | Recovery (by value) | Recovery (by count) | Recovered (mean) | Compliance violations | Retries/recovery | Nudges to unrecoverable |
|---|---|---|---|---|---|---|
| No-retry | 0.0% ± 0.0% | 0.0% ± 0.0% | Rs.0 | 0 | 0.00 | 0 |
| Naive retry bot | 37.6% ± 2.4% | 42.1% ± 1.5% | Rs.1,207,155 | 12309 | 7.13 | 976 |
| RePresent | 39.8% ± 1.9% | 45.0% ± 0.8% | Rs.1,272,818 | 0 | 2.80 | 400 |
| Oracle (upper bound) | 43.8% ± 2.1% | 49.4% ± 1.1% | Rs.1,402,517 | 0 | 1.90 | 0 |

## Failure case: correlated bank outage

| Policy | Outage-cohort recovery | Retries burned into outage windows (seed mean) |
|---|---|---|
| Naive retry bot | 53.2% | 174 |
| RePresent | 70.4% | 9 |
| Oracle (upper bound) | 69.2% | 0 |

## TAT module (RBI DPSS 629 compensation)

- Claims filed: 53/seed · claim precision (auditor-recomputed): 1.000
- Compensation identified for customers: Rs.40,800/seed (reported separately from merchant revenue — beneficiary is the customer)

## Classifier (held-out, seed 1)

- Hybrid accuracy: 93.2% on 1997 events · clean-code coverage 62.1% · gated to human: 132 (6.6%)

| Class | n | accuracy |
|---|---|---|
| insufficient_funds | 1227 | 0.938 |
| limit_breach | 90 | 0.856 |
| mandate_paused | 174 | 0.897 |
| mandate_revoked | 107 | 0.935 |
| technical_decline_bank | 300 | 0.937 |
| technical_decline_psp | 99 | 0.970 |

_Escalations to human (RePresent): 148/seed — bounded autonomy, not silent failure._
