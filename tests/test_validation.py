"""Data Engineering: validation and cleaning rules."""

from __future__ import annotations

import pandas as pd
import pytest

from src.data.clean import clean_tables, normalise_labels
from src.data.validate import SchemaError, check_required_columns, find_duplicates, find_invalid_records


def _partners():
    return pd.DataFrame(
        {
            "partner_id": ["P01", "P02"],
            "partner_name": ["A", "B"],
            "channel": ["wholesaler", "Pharma Distributor "],
            "region": ["Lagos", "kano"],
            "onboarded_date": ["2023-01-01", "2023-01-01"],
        }
    )


def _borrowers():
    return pd.DataFrame(
        {
            "borrower_id": ["B1", "B2", "B3", "B3"],
            "business_name": ["x", "y", "z", "z"],
            "business_type": ["PHARMACY ", "pharm", "clinic", "clinic"],
            "region": ["Lagos", "Lagos", "Lagos", "Lagos"],
            "partner_id": ["P01", "P99", "P02", "P02"],
            "onboarding_date": ["2024-01-01"] * 4,
            "years_in_operation": ["3", "4", "5", "5"],
            "staff_count": ["4", "2", "3", "3"],
        }
    )


def _applications():
    return pd.DataFrame(
        {
            "application_id": ["A1", "A2", "A3", "A4"],
            "borrower_id": ["B1", "B1", "B3", "B2"],
            "partner_id": ["P01", "P01", "P02", "P99"],
            "application_date": ["2025-01-10", "2025-13-45", "2025-02-01", "2025-02-01"],
            "requested_amount": ["10000", "5000", "-200", "3000"],
            "requested_tenor_months": ["6", "6", "6", "6"],
            "approval_flag": ["1", "1", "0", "1"],
            "default_flag": ["0", None, "1", "0"],
        }
    )


def _empty(cols):
    return pd.DataFrame({c: pd.Series(dtype="object") for c in cols})


def _raw():
    return {
        "partners": _partners(),
        "borrowers": _borrowers(),
        "applications": _applications(),
        "procurement_transactions": _empty(
            ["txn_id", "borrower_id", "partner_id", "txn_date", "procurement_value", "items_count"]
        ),
        "repayments": pd.DataFrame(
            {
                "repayment_id": ["R1", "R2"],
                "application_id": ["A1", "A1"],
                "borrower_id": ["B1", "B1"],
                "installment_number": ["1", "2"],
                "repayment_due_date": ["2025-02-10", "2024-12-01"],  # R2 falls due before the loan existed
                "amount_due": ["1000", "1000"],
                "repayment_date": ["2025-02-09", None],
                "repayment_amount": ["1000", "0"],
            }
        ),
        "stock_movements": _empty(
            ["movement_id", "borrower_id", "movement_date", "movement_type", "stock_value"]
        ),
    }


def test_missing_required_column_stops_pipeline():
    with pytest.raises(SchemaError, match="requested_amount"):
        check_required_columns("applications", _applications().drop(columns="requested_amount"))


def test_duplicate_ids_keep_first_copy():
    mask, issues = find_duplicates("borrowers", _borrowers())
    assert mask.tolist() == [False, False, False, True]
    assert issues[0]["rule"] == "duplicate_id"


def test_label_variants_are_normalised():
    fixed, fixes = normalise_labels("borrowers", _borrowers())
    assert fixed["business_type"].tolist() == ["pharmacy", "pharmacy", "clinic", "clinic"]
    assert len(fixes) == 2
    partners, _ = normalise_labels("partners", _partners())
    assert partners["channel"].tolist() == ["wholesaler", "pharma_distributor"]
    assert partners["region"].tolist() == ["Lagos", "Kano"]


def test_invalid_records_are_rejected_with_reasons():
    apps = _applications()
    reject, issues = find_invalid_records(
        "applications", apps, {"borrowers": {"B1", "B3"}, "partners": {"P01", "P02"}}
    )
    assert reject.tolist() == [False, True, True, True]
    rules = {i["record_id"]: i["rule"] for i in issues}
    assert rules == {"A2": "invalid_date", "A3": "impossible_value", "A4": "broken_foreign_key"}


def test_non_whole_numbers_are_rejected_not_crashing():
    apps = _applications().iloc[[0]].assign(requested_tenor_months="6.5")
    reject, issues = find_invalid_records("applications", apps, {})
    assert reject.all()
    assert issues[0]["rule"] == "not_an_integer"


def test_nullable_label_is_allowed():
    apps = _applications().iloc[[0]].assign(default_flag=None)
    reject, _ = find_invalid_records("applications", apps, {})
    assert not reject.any()


def test_clean_tables_cascades_and_reports():
    clean, issues, report = clean_tables(_raw())
    assert set(clean["borrowers"]["borrower_id"]) == {
        "B1",
        "B3",
    }  # B2 has unknown partner, B3 duplicate dropped
    assert clean["applications"]["application_id"].tolist() == ["A1"]
    assert clean["repayments"]["repayment_id"].tolist() == ["R1"]
    assert pd.api.types.is_datetime64_any_dtype(clean["applications"]["application_date"])
    assert report["tables"]["borrowers"]["duplicates_dropped"] == 1
    assert "impossible_date_order" in set(issues["rule"])


def test_generated_dirty_data_is_caught(platform_env):
    from src.utils.paths import Paths

    report = pd.read_json(Paths().validation_report_json, typ="series")
    rules = {(r["table"], r["rule"]) for r in report["rule_counts"]}
    for expected in [
        ("borrowers", "duplicate_id"),
        ("borrowers", "inconsistent_label"),
        ("borrowers", "broken_foreign_key"),
        ("applications", "invalid_date"),
        ("applications", "impossible_value"),
        ("procurement_transactions", "impossible_value"),
    ]:
        assert expected in rules
    assert report["removed_share"] < 0.05
