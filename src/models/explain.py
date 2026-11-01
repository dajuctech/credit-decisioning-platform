"""Reason codes (AI Engineering).

For every application we ask a simple question per feature: "how would the
score change if this one value were typical (the training median) instead of
what this borrower actually has?" Features that push the score up become
positive reasons; features that pull it down become risk reasons.

This works for any model type, so the explanation method does not have to
change if the champion model changes.
"""

from __future__ import annotations

from collections.abc import Callable

import numpy as np
import pandas as pd

REASON_TEXT: dict[str, tuple[str, str]] = {
    "requested_amount": ("modest requested amount", "large requested amount"),
    "recent_procurement_value": ("healthy procurement activity", "low recent procurement activity"),
    "procurement_frequency_90d": ("frequent stock ordering", "infrequent stock ordering"),
    "avg_order_value_90d": ("healthy average order size", "small average order size"),
    "procurement_trend_ratio": ("growing procurement volumes", "declining procurement volumes"),
    "days_since_last_procurement": ("recent purchasing activity", "no recent purchasing activity"),
    "active_months_12m": ("consistently active over the last year", "irregular activity over the last year"),
    "late_payment_count_90d": ("manageable late payment history", "recent late payments"),
    "repayment_rate_180d": ("strong repayment consistency", "weak recent repayment record"),
    "prior_loan_count": ("established repeat borrower", "limited borrowing history"),
    "stock_turnover_ratio": ("stock sells through quickly", "slow-moving stock"),
    "stock_trend_ratio": ("rising sales trend", "falling sales trend"),
    "loan_to_procurement_ratio": (
        "loan size is modest relative to purchases",
        "loan size is large relative to purchases",
    ),
    "partner_late_rate_180d": ("partner network repays well", "partner network shows elevated late payments"),
    "years_in_operation": ("well-established business", "young business"),
}

# Short names for the "limited data" reason.
DATA_LABELS = {
    "recent_procurement_value": "procurement history",
    "repayment_rate_180d": "repayment history",
    "stock_turnover_ratio": "stock data",
    "late_payment_count_90d": "payment timeliness",
}

MIN_IMPACT = 0.002  # ignore effects smaller than 0.2 score points


def feature_impacts(
    X: pd.DataFrame, score_fn: Callable[[pd.DataFrame], np.ndarray], reference: dict[str, float]
) -> pd.DataFrame:
    """Return an (n_rows x n_features) table: score change caused by each feature's actual value."""
    features = list(X.columns)
    base = score_fn(X)
    blocks = []
    for f in features:
        Xf = X.copy()
        Xf[f] = reference[f]
        blocks.append(Xf)
    stacked = pd.concat(blocks, ignore_index=True)
    neutral = score_fn(stacked).reshape(len(features), len(X)).T
    return pd.DataFrame(base.reshape(-1, 1) - neutral, columns=features, index=X.index)


def reason_codes(impacts: pd.Series, decision: str, missing: list[str], top_n: int = 3) -> list[str]:
    """Pick plain-English reasons that support the decision."""
    positive = impacts[impacts > MIN_IMPACT].sort_values(ascending=False)
    negative = impacts[impacts < -MIN_IMPACT].sort_values()
    reasons: list[str] = []
    if missing:
        if decision != "approve":
            reasons.append("limited data: no " + " or ".join(DATA_LABELS.get(m, m) for m in missing[:2]))
    if decision == "approve":
        picks = [REASON_TEXT[f][0] for f in positive.index]
    elif decision == "decline":
        picks = [REASON_TEXT[f][1] for f in negative.index]
    else:  # manual review: show the main concerns first, then the main strengths
        picks = [REASON_TEXT[f][1] for f in negative.index[:2]] + [
            REASON_TEXT[f][0] for f in positive.index[:1]
        ]
    for p in picks:
        if p not in reasons:
            reasons.append(p)
    if not reasons:
        reasons.append("profile close to the typical borrower")
    return reasons[:top_n]


def reason_details(impacts: pd.Series, values: pd.Series, top_n: int = 5) -> list[dict]:
    top = impacts.abs().sort_values(ascending=False).index[:top_n]
    out = []
    for f in top:
        v = values.get(f)
        out.append(
            {
                "feature": f,
                "value": None if pd.isna(v) else round(float(v), 4),
                "impact": round(float(impacts[f]), 4),
                "direction": "supports approval" if impacts[f] > 0 else "increases risk",
            }
        )
    return out
