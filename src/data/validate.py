"""Data validation rules (Data Engineering, step 2).

Every raw table is checked against a simple contract before anything else
uses it. Each failed check is recorded with the record ID and a plain-English
reason, so bad data is never silently dropped or silently used.

Checks (PRD 14.2):
  * missing required columns       -> pipeline stops (cannot continue safely)
  * duplicate IDs                  -> keep first copy, log the rest
  * invalid dates                  -> reject record
  * negative or impossible values  -> reject record
  * broken foreign keys            -> reject record
  * inconsistent labels            -> auto-fixed by clean.py if recognisable, else rejected
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

CHANNELS = {"pharma_distributor", "wholesaler", "ngo_program", "direct_sales", "digital_marketplace"}
BUSINESS_TYPES = {"pharmacy", "clinic"}
REGIONS = {"Lagos", "Abuja", "Kano", "Nairobi", "Mombasa", "Kisumu"}
MOVEMENT_TYPES = {"inbound", "outbound"}
# Columns that must hold whole numbers (6.5 months or 0.5 approvals are data errors).
INTEGER_COLUMNS = {
    "requested_tenor_months",
    "approval_flag",
    "default_flag",
    "staff_count",
    "items_count",
    "installment_number",
}


@dataclass
class TableContract:
    id_column: str
    required: list[str]
    dates: list[str] = field(default_factory=list)
    nullable_dates: list[str] = field(default_factory=list)
    # column -> (min allowed, max allowed); None means unbounded
    ranges: dict[str, tuple[float | None, float | None]] = field(default_factory=dict)
    nullable_numeric: list[str] = field(default_factory=list)
    labels: dict[str, set[str]] = field(default_factory=dict)
    foreign_keys: dict[str, str] = field(default_factory=dict)


CONTRACTS: dict[str, TableContract] = {
    "partners": TableContract(
        id_column="partner_id",
        required=["partner_id", "partner_name", "channel", "region", "onboarded_date"],
        dates=["onboarded_date"],
        labels={"channel": CHANNELS, "region": REGIONS},
    ),
    "borrowers": TableContract(
        id_column="borrower_id",
        required=[
            "borrower_id",
            "business_name",
            "business_type",
            "region",
            "partner_id",
            "onboarding_date",
            "years_in_operation",
            "staff_count",
        ],
        dates=["onboarding_date"],
        ranges={"years_in_operation": (0, 100), "staff_count": (1, 5000)},
        labels={"business_type": BUSINESS_TYPES, "region": REGIONS},
        foreign_keys={"partner_id": "partners"},
    ),
    "applications": TableContract(
        id_column="application_id",
        required=[
            "application_id",
            "borrower_id",
            "partner_id",
            "application_date",
            "requested_amount",
            "requested_tenor_months",
            "approval_flag",
            "default_flag",
        ],
        dates=["application_date"],
        ranges={
            "requested_amount": (1, 50_000_000),
            "requested_tenor_months": (1, 36),
            "approval_flag": (0, 1),
            "default_flag": (0, 1),
        },
        nullable_numeric=["default_flag"],
        foreign_keys={"borrower_id": "borrowers", "partner_id": "partners"},
    ),
    "procurement_transactions": TableContract(
        id_column="txn_id",
        required=["txn_id", "borrower_id", "partner_id", "txn_date", "procurement_value", "items_count"],
        dates=["txn_date"],
        ranges={"procurement_value": (0.01, None), "items_count": (1, None)},
        foreign_keys={"borrower_id": "borrowers", "partner_id": "partners"},
    ),
    "repayments": TableContract(
        id_column="repayment_id",
        required=[
            "repayment_id",
            "application_id",
            "borrower_id",
            "installment_number",
            "repayment_due_date",
            "amount_due",
            "repayment_date",
            "repayment_amount",
        ],
        dates=["repayment_due_date"],
        nullable_dates=["repayment_date"],
        ranges={"amount_due": (0.01, None), "repayment_amount": (0, None), "installment_number": (1, 36)},
        foreign_keys={"application_id": "applications", "borrower_id": "borrowers"},
    ),
    "stock_movements": TableContract(
        id_column="movement_id",
        required=["movement_id", "borrower_id", "movement_date", "movement_type", "stock_value"],
        dates=["movement_date"],
        ranges={"stock_value": (0, None)},
        labels={"movement_type": MOVEMENT_TYPES},
        foreign_keys={"borrower_id": "borrowers"},
    ),
}

# Parents must be validated before children so foreign keys point at clean rows.
VALIDATION_ORDER = [
    "partners",
    "borrowers",
    "applications",
    "procurement_transactions",
    "repayments",
    "stock_movements",
]


class SchemaError(ValueError):
    """Raised when a table is missing required columns. The pipeline must stop."""


def check_required_columns(table: str, df: pd.DataFrame) -> None:
    missing = [c for c in CONTRACTS[table].required if c not in df.columns]
    if missing:
        raise SchemaError(f"Table '{table}' is missing required columns: {missing}")


def _issue_rows(df: pd.DataFrame, mask: pd.Series, table: str, rule: str, detail: str) -> list[dict]:
    contract = CONTRACTS[table]
    return [
        {"table": table, "record_id": rid, "rule": rule, "detail": detail, "action": "rejected"}
        for rid in df.loc[mask, contract.id_column].astype(str)
    ]


def find_invalid_records(
    table: str, df: pd.DataFrame, valid_keys: dict[str, set[str]]
) -> tuple[pd.Series, list[dict]]:
    """Return a boolean mask of rows to reject plus one issue row per problem found.

    `df` should already have had labels normalised by clean.py.
    `valid_keys` maps parent table name -> set of IDs that passed validation.
    """
    contract = CONTRACTS[table]
    reject = pd.Series(False, index=df.index)
    issues: list[dict] = []

    def flag(mask: pd.Series, rule: str, detail: str) -> None:
        nonlocal reject
        mask = mask & ~reject  # report each rejected row once, under its first failure
        if mask.any():
            issues.extend(_issue_rows(df, mask, table, rule, detail))
            reject = reject | mask

    id_missing = df[contract.id_column].isna() | (df[contract.id_column].astype(str).str.strip() == "")
    flag(id_missing, "missing_id", f"{contract.id_column} is empty")

    for col in contract.required:
        if col in contract.nullable_numeric or col in contract.nullable_dates:
            continue
        flag(df[col].isna(), "missing_value", f"{col} is empty")

    for col in contract.dates:
        parsed = pd.to_datetime(df[col], format="%Y-%m-%d", errors="coerce")
        flag(parsed.isna() & df[col].notna(), "invalid_date", f"{col} is not a valid YYYY-MM-DD date")
        flag(
            parsed > pd.Timestamp("2030-12-31"), "invalid_date", f"{col} is unrealistically far in the future"
        )
    for col in contract.nullable_dates:
        parsed = pd.to_datetime(df[col], errors="coerce")
        flag(parsed.isna() & df[col].notna(), "invalid_date", f"{col} is not a valid date")

    for col, (lo, hi) in contract.ranges.items():
        values = pd.to_numeric(df[col], errors="coerce")
        nullable = col in contract.nullable_numeric
        flag(values.isna() & (df[col].notna() | (not nullable)), "not_a_number", f"{col} is not numeric")
        if col in INTEGER_COLUMNS:
            flag(values.notna() & (values % 1 != 0), "not_an_integer", f"{col} must be a whole number")
        if lo is not None:
            flag(values < lo, "impossible_value", f"{col} is below {lo}")
        if hi is not None:
            flag(values > hi, "impossible_value", f"{col} is above {hi}")

    for col, allowed in contract.labels.items():
        flag(~df[col].isin(allowed), "unknown_label", f"{col} is not one of {sorted(allowed)}")

    for col, parent in contract.foreign_keys.items():
        if parent in valid_keys:
            flag(~df[col].isin(valid_keys[parent]), "broken_foreign_key", f"{col} not found in {parent}")

    return reject, issues


def find_duplicates(table: str, df: pd.DataFrame) -> tuple[pd.Series, list[dict]]:
    """Flag every repeat of an ID after its first appearance."""
    col = CONTRACTS[table].id_column
    dup = df.duplicated(subset=[col], keep="first")
    issues = [
        {
            "table": table,
            "record_id": rid,
            "rule": "duplicate_id",
            "detail": f"repeated {col}",
            "action": "dropped",
        }
        for rid in df.loc[dup, col].astype(str)
    ]
    return dup, issues


def check_repayment_dates(
    repayments: pd.DataFrame, applications: pd.DataFrame
) -> tuple[pd.Series, list[dict]]:
    """A repayment cannot fall due before its loan was applied for."""
    app_dates = applications.set_index("application_id")["application_date"]
    due = pd.to_datetime(repayments["repayment_due_date"], errors="coerce")
    applied = pd.to_datetime(repayments["application_id"].map(app_dates), errors="coerce")
    mask = due <= applied
    issues = _issue_rows(
        repayments, mask, "repayments", "impossible_date_order", "due date is before application"
    )
    return mask, issues
