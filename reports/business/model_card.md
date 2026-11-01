# Model Card - credit risk model v1.0.0

## What it does
Estimates the probability that a clinic or pharmacy repays a stock-finance loan, using only
behaviour known on the application date (procurement, stock movement, repayment history, partner).

## Candidates compared (ROC-AUC)

| Model | Validation AUC | Test AUC |
|---|---:|---:|
| random_forest | 0.818 | 0.788 |
| gradient_boosting | 0.818 | 0.781 |
| logistic_regression | 0.800 | 0.777 |

**Champion: `random_forest`** (simpler model preferred unless another is better by more than 0.01 AUC).

## Decision thresholds
- Approve when score >= **0.80** (chosen so at most 6% of auto-approved loans default on the validation set)
- Decline when score < **0.70** (default risk above 30%)
- Everything in between goes to manual review.

## Out-of-time test results (applications Apr-Jun 2025)

| Metric | Model | Manual underwriter |
|---|---:|---:|
| Auto-approval rate | 84.6% | 85.5% |
| Bad rate among approved | 6.3% | 7.1% |
| Recall (defaults flagged) | 46.2% | 38.6% |
| Precision | 29.8% | 26.5% |
| F1 | 0.362 | 0.315 |
| ROC-AUC | 0.788 | - |
| Brier score | 0.0777 | - |
| Expected calibration error | 0.0148 | - |

Confusion matrix (test): TN=1156, FP=158, FN=78, TP=67

## Behaviour across time

| Month | Split | Apps | Actual default | Predicted default | Avg score | Auto-approve | AUC |
|---|---|---:|---:|---:|---:|---:|---:|
| 2023-07 | train | 270 | 18.9% | 16.5% | 0.835 | 70.4% | 0.888 |
| 2023-08 | train | 299 | 18.7% | 16.5% | 0.835 | 67.9% | 0.889 |
| 2023-09 | train | 262 | 18.3% | 17.1% | 0.829 | 67.9% | 0.885 |
| 2023-10 | train | 329 | 20.7% | 17.1% | 0.829 | 67.2% | 0.826 |
| 2023-11 | train | 343 | 15.7% | 16.2% | 0.838 | 72.3% | 0.866 |
| 2023-12 | train | 405 | 18.5% | 17.0% | 0.830 | 68.6% | 0.889 |
| 2024-01 | train | 353 | 17.0% | 16.0% | 0.840 | 72.5% | 0.901 |
| 2024-02 | train | 282 | 16.3% | 13.7% | 0.863 | 76.2% | 0.875 |
| 2024-03 | train | 334 | 12.9% | 13.9% | 0.861 | 76.6% | 0.902 |
| 2024-04 | train | 365 | 13.2% | 12.8% | 0.872 | 79.2% | 0.870 |
| 2024-05 | train | 401 | 14.7% | 12.1% | 0.879 | 79.8% | 0.885 |
| 2024-06 | train | 378 | 13.0% | 13.8% | 0.862 | 74.9% | 0.852 |
| 2024-07 | train | 411 | 13.9% | 11.2% | 0.887 | 82.7% | 0.877 |
| 2024-08 | train | 392 | 11.0% | 11.5% | 0.885 | 83.7% | 0.906 |
| 2024-09 | train | 392 | 15.3% | 13.8% | 0.862 | 75.3% | 0.913 |
| 2024-10 | train | 412 | 10.0% | 11.2% | 0.888 | 80.6% | 0.887 |
| 2024-11 | train | 449 | 11.1% | 11.2% | 0.888 | 81.7% | 0.879 |
| 2024-12 | train | 444 | 10.1% | 11.2% | 0.888 | 82.7% | 0.893 |
| 2025-01 | validation | 432 | 12.0% | 11.9% | 0.881 | 81.0% | 0.767 |
| 2025-02 | validation | 371 | 11.3% | 12.4% | 0.876 | 77.1% | 0.836 |
| 2025-03 | validation | 472 | 11.4% | 10.7% | 0.893 | 83.9% | 0.851 |
| 2025-04 | test | 501 | 9.4% | 9.0% | 0.910 | 87.4% | 0.765 |
| 2025-05 | test | 513 | 9.2% | 10.5% | 0.895 | 82.8% | 0.784 |
| 2025-06 | test | 445 | 11.5% | 11.0% | 0.890 | 83.4% | 0.811 |

## Limitations
- Trained on synthetic pilot data where every application was funded (no reject-inference needed).
- Small sample: individual months have few defaults, so monthly AUC is noisy.
- Thresholds encode a risk appetite and must be re-approved if the business changes it.

Figures: `reports/figures/roc_curve.png`, `calibration_curve.png`, `threshold_simulation.png`,
`score_distribution.png`, `confusion_matrix.png`.
