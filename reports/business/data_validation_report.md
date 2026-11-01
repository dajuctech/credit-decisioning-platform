# Data Validation Report

Generated: 2026-11-01T17:57:38+00:00

Why this matters: the model and the API can only be as good as the data they receive.
Every record below was checked against the table's contract before any feature was built.

| Table | Rows in | Duplicates dropped | Rejected | Labels fixed | Rows out |
|---|---:|---:|---:|---:|---:|
| partners | 9 | 0 | 0 | 1 | 9 |
| borrowers | 4,405 | 5 | 3 | 85 | 4,397 |
| applications | 12,018 | 4 | 31 | 0 | 11,983 |
| procurement_transactions | 374,836 | 0 | 420 | 0 | 374,416 |
| repayments | 72,006 | 0 | 142 | 0 | 71,864 |
| stock_movements | 226,650 | 0 | 250 | 0 | 226,400 |

## Issues by rule

| Table | Rule | Count |
|---|---|---:|
| applications | broken_foreign_key | 17 |
| applications | duplicate_id | 4 |
| applications | impossible_value | 8 |
| applications | invalid_date | 6 |
| borrowers | broken_foreign_key | 3 |
| borrowers | duplicate_id | 5 |
| borrowers | inconsistent_label | 85 |
| partners | inconsistent_label | 1 |
| procurement_transactions | broken_foreign_key | 390 |
| procurement_transactions | impossible_value | 20 |
| procurement_transactions | invalid_date | 10 |
| repayments | broken_foreign_key | 132 |
| repayments | impossible_value | 10 |
| stock_movements | broken_foreign_key | 240 |
| stock_movements | impossible_value | 10 |

## Example issues

| Table | Record | Rule | Detail | Action |
|---|---|---|---|---|
| partners | P07 | inconsistent_label | channel: 'Direct Sales ' -> 'direct_sales' | fixed |
| borrowers | BOR-0075 | inconsistent_label | business_type: 'Clinic' -> 'clinic' | fixed |
| borrowers | BOR-2245 | duplicate_id | repeated borrower_id | dropped |
| borrowers | BOR-3092 | duplicate_id | repeated borrower_id | dropped |
| borrowers | BOR-1847 | broken_foreign_key | partner_id not found in partners | rejected |
| borrowers | BOR-3218 | broken_foreign_key | partner_id not found in partners | rejected |
| applications | APP-00242 | invalid_date | application_date is not a valid YYYY-MM-DD date | rejected |
| applications | APP-06009 | invalid_date | application_date is not a valid YYYY-MM-DD date | rejected |
| applications | APP-11524 | impossible_value | requested_amount is below 1 | rejected |
| applications | APP-10404 | impossible_value | requested_amount is below 1 | rejected |

Overall 855 of 689,924 rows removed (0.12%). Full list: `data/processed/rejected_records.csv`.
