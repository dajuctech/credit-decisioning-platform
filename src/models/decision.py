"""Decision policy (AI Engineering -> Backend Engineering contract).

Turns a calibrated score into a business decision. The rules are kept in one
small, readable place so analysts can review them and the API applies them
exactly the same way every time.

    score = probability the borrower repays (1 - probability of default)

    score >= approve_threshold                -> approve
    decline_threshold <= score < approve      -> manual_review
    score < decline_threshold                 -> decline
    too much key data missing (and not a decline) -> manual_review
    purchases unknown, or the safe limit rounds to 0 -> manual_review
"""

from __future__ import annotations

import math
from dataclasses import dataclass

# Features without which an automatic approval is not safe.
CORE_FEATURES = [
    "recent_procurement_value",
    "repayment_rate_180d",
    "stock_turnover_ratio",
    "late_payment_count_90d",
]
MAX_CORE_MISSING_FOR_AUTO_APPROVE = 1


@dataclass(frozen=True)
class DecisionPolicy:
    approve_threshold: float
    decline_threshold: float
    strong_margin: float = 0.04  # score this far above approve -> "strong" band
    limit_procurement_cover: float = 0.75  # limit <= 75% of average monthly purchases
    round_to: int = 500

    def band(self, score: float) -> str:
        if score >= self.approve_threshold + self.strong_margin:
            return "strong"
        if score >= self.approve_threshold:
            return "standard"
        if score >= self.decline_threshold:
            return "review"
        return "decline"

    def decide(self, score: float, core_missing: int, procurement_known: bool = True) -> str:
        band = self.band(score)
        if band == "decline":
            return "decline"
        # Without purchase data we cannot size the loan safely, so a person must look at it.
        if core_missing > MAX_CORE_MISSING_FOR_AUTO_APPROVE or not procurement_known:
            return "manual_review"
        return "manual_review" if band == "review" else "approve"

    def recommend_limit(
        self, score: float, decision: str, requested_amount: float, recent_procurement_value: float | None
    ) -> float:
        if decision == "decline":
            return 0.0
        factor = {"strong": 1.0, "standard": 0.8, "review": 0.5, "decline": 0.0}[self.band(score)]
        if decision == "manual_review":
            factor = min(factor, 0.5)  # indicative limit only, pending a human review
        limit = requested_amount * factor
        if recent_procurement_value is not None and not math.isnan(recent_procurement_value):
            limit = min(limit, self.limit_procurement_cover * recent_procurement_value / 3)
        return float(max(0, math.floor(limit / self.round_to) * self.round_to))

    def recommend_tenor(self, score: float, decision: str, requested_tenor: int | None) -> int | None:
        if decision == "decline":
            return None
        max_tenor = {"strong": 12, "standard": 6, "review": 3, "decline": 0}[self.band(score)]
        if decision == "manual_review":
            max_tenor = 3
        requested = requested_tenor or 6
        return int(min(requested, max_tenor))
