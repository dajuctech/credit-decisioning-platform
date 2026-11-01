# High-Risk Application Report

_Generated 2026-11-01 18:01 UTC by `src/monitoring/reporting.py`_

Which applications need a human decision, and why?

- Declined: **71**
- In the manual review queue: **114**, of which **87** are there partly because key data was missing (a data-feed problem, not necessarily a risky borrower)

## Why cases go to manual review

| reason | cases |
|---|---|
| limited data: no repayment history or stock data | 87 |
| infrequent stock ordering | 70 |
| loan size is large relative to purchases | 51 |
| small average order size | 22 |
| low recent procurement activity | 21 |
| slow-moving stock | 20 |

## 25 highest-risk applications

| application_id | borrower_id | partner_id | score | decision | reasons |
|---|---|---|---|---|---|
| APP-L00481 | BOR-4368 | P09 | 0.429 | decline | limited data: no repayment history or stock data; infrequent stock ordering; loan size is large relative to purchases |
| APP-L00315 | BOR-4388 | P09 | 0.43 | decline | limited data: no repayment history or stock data; loan size is large relative to purchases; infrequent stock ordering |
| APP-L00027 | BOR-4006 | P09 | 0.441 | decline | limited data: no repayment history or stock data; infrequent stock ordering; loan size is large relative to purchases |
| APP-L00560 | BOR-4014 | P09 | 0.446 | decline | limited data: no repayment history or stock data; infrequent stock ordering; loan size is large relative to purchases |
| APP-L00274 | BOR-4294 | P09 | 0.455 | decline | limited data: no repayment history or stock data; infrequent stock ordering; loan size is large relative to purchases |
| APP-L00033 | BOR-4247 | P09 | 0.459 | decline | limited data: no repayment history or stock data; infrequent stock ordering; loan size is large relative to purchases |
| APP-L00105 | BOR-4355 | P09 | 0.462 | decline | limited data: no repayment history or stock data; loan size is large relative to purchases; infrequent stock ordering |
| APP-L00467 | BOR-3369 | P04 | 0.471 | decline | slow-moving stock; infrequent stock ordering; loan size is large relative to purchases |
| APP-L00041 | BOR-4382 | P09 | 0.474 | decline | limited data: no repayment history or stock data; infrequent stock ordering; loan size is large relative to purchases |
| APP-L00196 | BOR-3963 | P06 | 0.475 | decline | slow-moving stock; infrequent stock ordering; loan size is large relative to purchases |
| APP-L00180 | BOR-4175 | P09 | 0.491 | decline | limited data: no repayment history or stock data; loan size is large relative to purchases; infrequent stock ordering |
| APP-L00044 | BOR-4323 | P09 | 0.494 | decline | limited data: no repayment history or stock data; loan size is large relative to purchases; infrequent stock ordering |
| APP-L00227 | BOR-4019 | P09 | 0.5 | decline | limited data: no repayment history or stock data; infrequent stock ordering; loan size is large relative to purchases |
| APP-L00437 | BOR-4226 | P09 | 0.502 | decline | limited data: no repayment history or stock data; loan size is large relative to purchases; infrequent stock ordering |
| APP-L00103 | BOR-4290 | P09 | 0.502 | decline | limited data: no repayment history or stock data; loan size is large relative to purchases; infrequent stock ordering |
| APP-L00344 | BOR-4181 | P09 | 0.507 | decline | limited data: no repayment history or stock data; loan size is large relative to purchases; infrequent stock ordering |
| APP-L00161 | BOR-4234 | P09 | 0.511 | decline | limited data: no repayment history or stock data; loan size is large relative to purchases; infrequent stock ordering |
| APP-L00237 | BOR-1002 | P06 | 0.518 | decline | loan size is large relative to purchases; slow-moving stock; infrequent stock ordering |
| APP-L00124 | BOR-4243 | P09 | 0.531 | decline | limited data: no repayment history or stock data; loan size is large relative to purchases; infrequent stock ordering |
| APP-L00546 | BOR-1733 | P06 | 0.536 | decline | slow-moving stock; infrequent stock ordering; declining procurement volumes |
| APP-L00035 | BOR-4096 | P09 | 0.55 | decline | limited data: no repayment history or stock data; loan size is large relative to purchases; infrequent stock ordering |
| APP-L00020 | BOR-0917 | P05 | 0.565 | decline | slow-moving stock; loan size is large relative to purchases; infrequent stock ordering |
| APP-L00309 | BOR-4128 | P09 | 0.571 | decline | limited data: no repayment history or stock data; loan size is large relative to purchases; infrequent stock ordering |
| APP-L00451 | BOR-1632 | P08 | 0.572 | decline | slow-moving stock; loan size is large relative to purchases; infrequent stock ordering |
| APP-L00106 | BOR-4361 | P09 | 0.574 | decline | limited data: no repayment history or stock data; loan size is large relative to purchases; infrequent stock ordering |
