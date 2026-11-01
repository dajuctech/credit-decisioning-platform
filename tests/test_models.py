"""AI Engineering: decision policy, calibration, explanations, drift maths and the trained artefact."""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from src.models.calibrate import PlattCalibrator, expected_calibration_error
from src.models.decision import DecisionPolicy
from src.models.explain import reason_codes
from src.monitoring.drift import feature_drift, psi

POLICY = DecisionPolicy(approve_threshold=0.85, decline_threshold=0.70)


@pytest.mark.parametrize(
    ("score", "missing", "expected"),
    [
        (0.95, 0, "approve"),
        (0.86, 1, "approve"),
        (0.80, 0, "manual_review"),
        (0.60, 0, "decline"),
        (0.95, 3, "manual_review"),
        (0.60, 3, "decline"),
    ],
)
def test_decision_bands(score, missing, expected):
    assert POLICY.decide(score, missing) == expected


def test_unknown_purchases_go_to_review():
    assert POLICY.decide(0.95, 0, procurement_known=False) == "manual_review"
    assert POLICY.decide(0.60, 0, procurement_known=False) == "decline"


def test_limit_matches_prd_example():
    # PRD: requested 25,000 with 180,000 purchases in 90 days -> recommended 20,000 for a standard approval
    assert POLICY.recommend_limit(0.86, "approve", 25_000, 180_000) == 20_000
    assert POLICY.recommend_tenor(0.86, "approve", None) == 6


def test_limit_capped_by_purchases_and_rounded():
    # strong band would give the full 50,000 but purchases only support 0.75 * 30,000 = 22,500
    assert POLICY.recommend_limit(0.95, "approve", 50_000, 90_000) == 22_500
    assert POLICY.recommend_limit(0.95, "approve", 12_345, None) == 12_000
    assert POLICY.recommend_limit(0.60, "decline", 50_000, 90_000) == 0
    assert POLICY.recommend_tenor(0.60, "decline", 6) is None
    assert POLICY.recommend_tenor(0.95, "approve", 9) == 9


def test_platt_calibration_fixes_overconfident_model():
    rng = np.random.default_rng(0)
    true_p = rng.uniform(0.02, 0.4, 5000)
    y = rng.random(5000) < true_p
    overconfident = np.clip(true_p * 2, 0, 0.99)
    cal = PlattCalibrator().fit(overconfident, y)
    assert expected_calibration_error(y, cal.transform(overconfident)) < expected_calibration_error(
        y, overconfident
    )


def test_reason_codes_follow_decision():
    impacts = pd.Series(
        {"repayment_rate_180d": 0.05, "late_payment_count_90d": -0.04, "stock_turnover_ratio": 0.02}
    )
    assert reason_codes(impacts, "approve", [])[0] == "strong repayment consistency"
    assert reason_codes(impacts, "decline", [])[0] == "recent late payments"
    review = reason_codes(impacts, "manual_review", ["stock_turnover_ratio"])
    assert review[0].startswith("limited data")


def test_psi_detects_shift():
    rng = np.random.default_rng(1)
    base = rng.normal(0, 1, 5000)
    assert psi(base, rng.normal(0, 1, 5000)) < 0.02
    assert psi(base, rng.normal(1, 1, 5000)) > 0.25


def test_feature_drift_reports_missing_spike():
    base = pd.DataFrame({"x": np.arange(100.0)})
    cur = pd.DataFrame({"x": np.r_[np.arange(70.0), [np.nan] * 30]})
    row = feature_drift(base, cur, ["x"]).iloc[0]
    assert row["current_missing_rate"] == pytest.approx(0.3)
    assert row["missing_status"] == "alert"


def test_trained_artefact_is_complete(platform_env):
    from src.models.predict import CreditScorer
    from src.utils.paths import Paths

    paths = Paths()
    scorer = CreditScorer.load()
    assert scorer.version == "v1.0.0"
    assert scorer.policy.decline_threshold < scorer.policy.approve_threshold
    metrics = json.loads(paths.training_metrics.read_text())
    assert metrics["test"]["roc_auc"] > 0.6
    for fig in ("roc_curve", "calibration_curve", "threshold_simulation", "confusion_matrix"):
        assert paths.figure(fig).exists()
    assert paths.business_report("model_card").exists()
