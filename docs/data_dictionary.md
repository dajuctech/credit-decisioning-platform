# Data Dictionary

All data is synthetic, produced by `src/data/generate.py` with a fixed seed.

## Raw tables (`data/raw/*.csv`) -> clean tables (`data/processed/*.parquet`, `data/platform.db`)

| Table | Key | Important fields | Notes |
|---|---|---|---|
| `partners` | `partner_id` | `channel`, `region`, `onboarded_date` | Distributors / wholesalers that refer borrowers. P09 joins in Jan 2026. |
| `borrowers` | `borrower_id` | `business_type` (clinic/pharmacy), `region`, `partner_id`, `years_in_operation` | |
| `applications` | `application_id` | `application_date`, `requested_amount`, `requested_tenor_months`, `approval_flag`, `default_flag` | `approval_flag` = manual underwriter view; `default_flag` = reached 60+ days past due within 6 months (empty if not yet known). |
| `procurement_transactions` | `txn_id` | `borrower_id`, `txn_date`, `procurement_value` | Stock purchases from partners. |
| `repayments` | `repayment_id` | `application_id`, `repayment_due_date`, `amount_due`, `repayment_date`, `repayment_amount` | One row per instalment. Empty `repayment_date` = not paid. |
| `stock_movements` | `movement_id` | `borrower_id`, `movement_date`, `movement_type` (inbound/outbound), `stock_value` | Stock received vs sold. |
| `incoming_applications` | `application_id` | as `applications`, without outcomes | Post-launch requests (Jan-Mar 2026) sent to the API. |

## Platform tables (written by the system)

| Table | Written by | Purpose |
|---|---|---|
| `application_features` | feature engineering | One row of model inputs per historical application, plus `split` (train / validation / test / recent / unlabelled) |
| `borrower_feature_store` | feature engineering | Latest known features per borrower; the API uses it to fill fields a caller does not send |
| `baseline_scores` | training | Model decisions on Apr-Dec 2025 applications: the "before launch" reference for monitoring |
| `model_scores` | API | Every decision: score, decision, limit, tenor, reason codes, the exact features used, missing fields |
| `api_requests` | API middleware | Every call: path, status code, latency, error message, request ID |
| `monitoring_snapshots` | monitoring | Every check result with its status and owning layer, appended on each run |

## Model features (`src/data/feature_engineering.py`)

All features use only events **before** the application date.

| Feature | Meaning |
|---|---|
| `requested_amount` | Loan amount requested |
| `recent_procurement_value` | Total stock purchased in the last 90 days |
| `procurement_frequency_90d` | Number of stock orders in the last 90 days |
| `avg_order_value_90d` | Average order size in the last 90 days |
| `procurement_trend_ratio` | Purchases in the last 90 days / the 90 days before (capped at 5) |
| `days_since_last_procurement` | Days since the last order (365 if none in a year) |
| `active_months_12m` | Months with at least one order in the last 12 months |
| `late_payment_count_90d` | Instalments due in the last 90 days paid 7+ days late, or still unpaid 7+ days after the due date |
| `repayment_rate_180d` | Value repaid / value due, instalments due in the last 180 days (empty for first-time borrowers) |
| `prior_loan_count` | Earlier applications by the same borrower, capped at 3 ("3 or more") |
| `stock_turnover_ratio` | Stock sold / stock received in the last 90 days |
| `stock_trend_ratio` | Stock sold in the last 90 days / the 90 days before |
| `loan_to_procurement_ratio` | Requested amount / average monthly purchases (computed by the API itself, never sent by callers) |
| `partner_late_rate_180d` | Share of late instalments across the partner's borrowers, measured at the start of the month |
| `years_in_operation` | Age of the business |

## Model outputs

| Field | Meaning |
|---|---|
| `score` | Calibrated probability that the borrower repays (0-1) |
| `probability_of_default` | `1 - score` |
| `decision` | `approve` / `manual_review` / `decline` (see `src/models/decision.py`) |
| `risk_band` | `strong`, `standard`, `review`, `decline` |
| `recommended_limit` | Requested amount x band factor, capped at 75% of average monthly purchases, rounded down to 500 |
| `recommended_tenor_months` | Up to 12 (strong), 6 (standard), 3 (manual review) |
| `reason_codes` | Up to three plain-English reasons supporting the decision |
