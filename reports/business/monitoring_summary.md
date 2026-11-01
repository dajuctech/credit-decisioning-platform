# Monitoring Summary - Feedback Loop

_Generated 2026-11-01 18:01 UTC by `src/monitoring/reporting.py`_

This is the feedback loop. Each check belongs to the engineering layer that would fix it.

Snapshot `snap_d19b05ea3d` at 2026-11-01T18:01:44+00:00: **10 alerts**, **13 warnings**, 25 OK.

| Layer | Alerts | Warnings | OK |
|---|---:|---:|---:|
| Data Engineering | 8 | 11 | 12 |
| AI Engineering | 2 | 1 | 10 |
| Backend Engineering | 0 | 1 | 1 |
| Cloud Engineering | 0 | 0 | 2 |

## Data Engineering

- **ALERT** `missing_data_spike` - procurement_trend_ratio: missing in 18% of requests vs 1% before launch; 99% of the gaps come from partner P09 (missing in 79% of its requests)
  - Action: Find which partner feed stopped sending this field and fix the integration or mapping.
- **ALERT** `missing_data_spike` - stock_turnover_ratio: missing in 22% of requests vs 0% before launch; 99% of the gaps come from partner P09 (missing in 100% of its requests)
  - Action: Find which partner feed stopped sending this field and fix the integration or mapping.
- **ALERT** `missing_data_spike` - stock_trend_ratio: missing in 22% of requests vs 1% before launch; 99% of the gaps come from partner P09 (missing in 100% of its requests)
  - Action: Find which partner feed stopped sending this field and fix the integration or mapping.
- **ALERT** `missing_data_spike` - partner_late_rate_180d: missing in 22% of requests vs 0% before launch; 100% of the gaps come from partner P09 (missing in 100% of its requests)
  - Action: Find which partner feed stopped sending this field and fix the integration or mapping.
- **ALERT** `segment_drift` - Kano: loan_to_procurement_ratio: in Kano, median loan_to_procurement_ratio moved 0.404 -> 0.638 (PSI 0.779, 59 live applications)
  - Action: Check what changed for businesses in this region (supply, prices, partner feed) and whether the model's decisions there still make sense.
- **ALERT** `segment_drift` - Mombasa: loan_to_procurement_ratio: in Mombasa, median loan_to_procurement_ratio moved 0.436 -> 0.566 (PSI 0.275, 64 live applications)
  - Action: Check what changed for businesses in this region (supply, prices, partner feed) and whether the model's decisions there still make sense.
- **ALERT** `segment_drift` - Mombasa: stock_trend_ratio: in Mombasa, median stock_trend_ratio moved 1.027 -> 0.831 (PSI 0.425, 64 live applications)
  - Action: Check what changed for businesses in this region (supply, prices, partner feed) and whether the model's decisions there still make sense.
- **ALERT** `segment_drift` - Nairobi: loan_to_procurement_ratio: in Nairobi, median loan_to_procurement_ratio moved 0.387 -> 0.532 (PSI 0.260, 192 live applications)
  - Action: Check what changed for businesses in this region (supply, prices, partner feed) and whether the model's decisions there still make sense.
- **WARN** `feature_drift` - active_months_12m: PSI 0.226; median 12.000 before launch vs 11.000 now (KS=0.18)
  - Action: Check the upstream feed for this feature (format, units, partner changes); retrain if the shift is real and persistent.
- **WARN** `feature_drift` - prior_loan_count: PSI 0.143; median 3.000 before launch vs 3.000 now (KS=0.09)
  - Action: Check the upstream feed for this feature (format, units, partner changes); retrain if the shift is real and persistent.
- **WARN** `feature_drift` - loan_to_procurement_ratio: PSI 0.117; median 0.398 before launch vs 0.465 now (KS=0.12)
  - Action: Check the upstream feed for this feature (format, units, partner changes); retrain if the shift is real and persistent.
- **WARN** `feature_drift` - procurement_frequency_90d: PSI 0.103; median 10.000 before launch vs 8.500 now (KS=0.11)
  - Action: Check the upstream feed for this feature (format, units, partner changes); retrain if the shift is real and persistent.
- **WARN** `missing_data_spike` - repayment_rate_180d: missing in 22% of requests vs 13% before launch; 99% of the gaps come from partner P09 (missing in 100% of its requests)
  - Action: Find which partner feed stopped sending this field and fix the integration or mapping.
- **WARN** `segment_drift` - Kano: recent_procurement_value: in Kano, median recent_procurement_value moved 212642.150 -> 191386.740 (PSI 0.200, 59 live applications)
  - Action: Check what changed for businesses in this region (supply, prices, partner feed) and whether the model's decisions there still make sense.
- **WARN** `segment_drift` - Kano: stock_trend_ratio: in Kano, median stock_trend_ratio moved 1.069 -> 0.876 (PSI 0.229, 59 live applications)
  - Action: Check what changed for businesses in this region (supply, prices, partner feed) and whether the model's decisions there still make sense.
- **WARN** `segment_drift` - Lagos: recent_procurement_value: in Lagos, median recent_procurement_value moved 203512.360 -> 241001.980 (PSI 0.107, 109 live applications)
  - Action: Check what changed for businesses in this region (supply, prices, partner feed) and whether the model's decisions there still make sense.
- **WARN** `segment_drift` - Lagos: stock_trend_ratio: in Lagos, median stock_trend_ratio moved 1.039 -> 1.176 (PSI 0.169, 109 live applications)
  - Action: Check what changed for businesses in this region (supply, prices, partner feed) and whether the model's decisions there still make sense.
- **WARN** `segment_drift` - Mombasa: recent_procurement_value: in Mombasa, median recent_procurement_value moved 184321.940 -> 171567.500 (PSI 0.106, 64 live applications)
  - Action: Check what changed for businesses in this region (supply, prices, partner feed) and whether the model's decisions there still make sense.
- **WARN** `segment_drift` - Nairobi: recent_procurement_value: in Nairobi, median recent_procurement_value moved 206751.005 -> 171312.345 (PSI 0.143, 192 live applications)
  - Action: Check what changed for businesses in this region (supply, prices, partner feed) and whether the model's decisions there still make sense.
- **OK** `feature_drift` - requested_amount: PSI 0.084; median 26,500.000 before launch vs 32,000.000 now (KS=0.11)
- **OK** `feature_drift` - avg_order_value_90d: PSI 0.049; median 22,025.375 before launch vs 25,086.536 now (KS=0.09)
- **OK** `feature_drift` - procurement_trend_ratio: PSI 0.048; median 1.036 before launch vs 1.102 now (KS=0.09)
- **OK** `feature_drift` - recent_procurement_value: PSI 0.039; median 200,432.100 before launch vs 202,711.030 now (KS=0.06)
- **OK** `feature_drift` - stock_turnover_ratio: PSI 0.033; median 1.040 before launch vs 1.036 now (KS=0.05)
- **OK** `feature_drift` - stock_trend_ratio: PSI 0.021; median 1.042 before launch vs 1.053 now (KS=0.05)
- **OK** `feature_drift` - days_since_last_procurement: PSI 0.019; median 7.000 before launch vs 8.000 now (KS=0.05)
- **OK** `feature_drift` - partner_late_rate_180d: mean shift 0.013; median 0.191 before launch vs 0.170 now (KS=0.27)
- **OK** `feature_drift` - years_in_operation: PSI 0.005; median 4.900 before launch vs 4.850 now (KS=0.02)
- **OK** `feature_drift` - late_payment_count_90d: PSI 0.000; median 0.000 before launch vs 0.000 now (KS=0.00)
- **OK** `feature_drift` - repayment_rate_180d: PSI 0.000; median 1.000 before launch vs 1.000 now (KS=0.01)
- **OK** `validation_reject_rate` - all raw tables: 855 of 689,924 raw rows failed validation

## AI Engineering

- **ALERT** `approval_rate_shift` - overall: approval rate 86.5% before launch vs 69.2% live
  - Action: Confirm with the credit team whether the shift is expected; review thresholds.
- **ALERT** `manual_review_rate` - overall: manual review share 6.3% before launch vs 19.0% live
  - Action: Check staffing for the review queue and which reasons send cases to review.
- **WARN** `partner_approval_shift` - P09: new partner with no pre-launch history: 0.0% approved across 132 applications
  - Action: Deep-dive this partner's applications; check feature drift for their borrowers.
- **OK** `label_maturity` - live applications: outcomes for live loans are not known yet (need 6 months on book); predicted default rate is 12.2%
- **OK** `partner_approval_shift` - P01: approval 91.5% before launch vs 93.2% live (59 applications)
- **OK** `partner_approval_shift` - P02: approval 90.1% before launch vs 95.1% live (61 applications)
- **OK** `partner_approval_shift` - P03: approval 85.2% before launch vs 88.2% live (51 applications)
- **OK** `partner_approval_shift` - P04: approval 89.9% before launch vs 88.6% live (70 applications)
- **OK** `partner_approval_shift` - P05: approval 83.2% before launch vs 78.6% live (56 applications)
- **OK** `partner_approval_shift` - P06: approval 80.0% before launch vs 83.0% live (53 applications)
- **OK** `partner_approval_shift` - P07: approval 83.5% before launch vs 86.4% live (59 applications)
- **OK** `partner_approval_shift` - P08: approval 85.9% before launch vs 94.9% live (59 applications)
- **OK** `score_shift` - score distribution: score PSI 0.085; mean score 0.906 -> 0.878

## Backend Engineering

- **WARN** `client_error_rate` - /score and /batch-score: 3.2% of 95 scoring requests rejected as invalid (most common: validation_error)
  - Action: Share the API contract with the integrating partner; add payload checks on their side.
- **OK** `server_error_rate` - /score and /batch-score: 0 server errors

## Cloud Engineering

- **OK** `latency_p95` - /score and /batch-score: 95% of successful scoring calls finished within 65 ms (median 34 ms)
- **OK** `service_availability` - /health: health=ok, model_loaded=True, database_ok=True
