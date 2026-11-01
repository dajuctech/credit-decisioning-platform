"""Data -> AI hand-off: features must only use information known on the application date."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.data.feature_engineering import MODEL_FEATURES, add_derived_features, assign_split, build_features

D = pd.Timestamp


@pytest.fixture()
def tables():
    borrowers = pd.DataFrame(
        {
            "borrower_id": ["B1"],
            "partner_id": ["P01"],
            "years_in_operation": [4.0],
            "business_type": ["clinic"],
            "region": ["Lagos"],
        }
    )
    applications = pd.DataFrame(
        {
            "application_id": ["OLD", "NEW"],
            "borrower_id": ["B1", "B1"],
            "partner_id": ["P01", "P01"],
            "application_date": [D("2024-06-01"), D("2025-01-01")],
            "requested_amount": [10_000.0, 20_000.0],
        }
    )
    procurement = pd.DataFrame(
        {
            "borrower_id": ["B1"] * 5,
            "txn_date": [D("2024-09-01"), D("2024-11-01"), D("2024-12-15"), D("2025-01-01"), D("2025-01-20")],
            "procurement_value": [5_000.0, 30_000.0, 30_000.0, 99_999.0, 99_999.0],
        }
    )
    # Repayments of the OLD loan, seen from the NEW application (2025-01-01)
    repayments = pd.DataFrame(
        {
            "application_id": ["OLD"] * 4,
            "borrower_id": ["B1"] * 4,
            "repayment_due_date": [D("2024-10-01"), D("2024-11-01"), D("2024-12-01"), D("2025-02-01")],
            "repayment_date": [D("2024-09-30"), D("2024-11-12"), pd.NaT, D("2025-02-01")],
            "amount_due": [1_000.0, 1_000.0, 1_000.0, 1_000.0],
            "repayment_amount": [1_000.0, 500.0, 0.0, 1_000.0],
        }
    )
    stock = pd.DataFrame(
        {
            "borrower_id": ["B1"] * 4,
            "movement_date": [D("2024-08-15"), D("2024-08-20"), D("2024-11-15"), D("2024-11-20")],
            "movement_type": ["inbound", "outbound", "inbound", "outbound"],
            "stock_value": [10_000.0, 8_000.0, 10_000.0, 12_000.0],
        }
    )
    return {
        "borrowers": borrowers,
        "applications": applications,
        "procurement_transactions": procurement,
        "repayments": repayments,
        "stock_movements": stock,
    }


def _new_row(tables):
    feats = build_features(tables["applications"], tables)
    return feats.set_index("application_id").loc["NEW"]


def test_procurement_window_excludes_same_day_and_future(tables):
    row = _new_row(tables)
    # last 90 days before 2025-01-01: 2024-11-01 (61 days ago) and 2024-12-15; 2025-01-01 and later are excluded
    assert row["recent_procurement_value"] == pytest.approx(60_000)
    assert row["procurement_frequency_90d"] == 2
    assert row["avg_order_value_90d"] == pytest.approx(30_000)
    assert row["days_since_last_procurement"] == 17
    # 60,000 now vs 5,000 in the 90 days before = 12x growth, capped at 5 so outliers cannot dominate
    assert row["procurement_trend_ratio"] == pytest.approx(5.0)


def test_repayment_features_are_point_in_time(tables):
    row = _new_row(tables)
    # due in last 180 days: Oct (paid 1000), Nov (paid 500 late), Dec (unpaid). Feb 2025 is in the future.
    assert row["repayment_rate_180d"] == pytest.approx(1_500 / 3_000)
    # late in last 90 days: Nov paid 11 days late, Dec still unpaid 31 days later -> 2
    assert row["late_payment_count_90d"] == 2
    assert row["prior_loan_count"] == 1


def test_first_application_has_no_repayment_history(tables):
    feats = build_features(tables["applications"], tables).set_index("application_id")
    assert np.isnan(feats.loc["OLD", "repayment_rate_180d"])
    assert feats.loc["OLD", "late_payment_count_90d"] == 0
    assert feats.loc["OLD", "prior_loan_count"] == 0


def test_stock_ratios(tables):
    row = _new_row(tables)
    assert row["stock_turnover_ratio"] == pytest.approx(1.2)  # last 90 days: sold 12k of 10k received
    assert row["stock_trend_ratio"] == pytest.approx(12_000 / 8_000)


def test_all_model_features_present(tables):
    feats = build_features(tables["applications"], tables)
    assert set(MODEL_FEATURES) <= set(feats.columns)


def test_derived_loan_to_procurement_ratio():
    df = pd.DataFrame(
        {"requested_amount": [30_000, 10_000, 5_000], "recent_procurement_value": [180_000, 0, np.nan]}
    )
    out = add_derived_features(df)["loan_to_procurement_ratio"].tolist()
    assert out[0] == pytest.approx(0.5)
    assert out[1] == 10.0  # no purchases at all -> capped maximum risk ratio
    assert np.isnan(out[2])  # unknown purchases -> unknown ratio


def test_time_based_split():
    dates = pd.Series(pd.to_datetime(["2024-06-01", "2025-02-01", "2025-05-01", "2025-09-01", "2025-05-02"]))
    labels = pd.Series([0, 1, 0, pd.NA, pd.NA], dtype="Int64")
    assert assign_split(dates, labels).tolist() == ["train", "validation", "test", "recent", "unlabelled"]
