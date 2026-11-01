"""Cleaning (Data Engineering, step 3).

Takes raw tables, fixes what can be fixed safely (label spelling, spacing,
types), removes what cannot be trusted (using validate.py), and saves:

  data/processed/<table>.parquet       cleaned tables for the AI layer
  data/platform.db                     the same tables in SQLite (for SQL users and the API)
  data/processed/rejected_records.csv  every record we removed, and why
  data/processed/validation_report.json and reports/business/data_validation_report.md
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pandas as pd

from src.data.validate import (
    CONTRACTS,
    VALIDATION_ORDER,
    check_repayment_dates,
    check_required_columns,
    find_duplicates,
    find_invalid_records,
)
from src.utils import db
from src.utils.logger import get_logger
from src.utils.paths import Paths

log = get_logger(__name__)

LABEL_ALIASES = {
    "business_type": {"pharm": "pharmacy", "chemist": "pharmacy", "medical_clinic": "clinic"},
    "channel": {"distributor": "pharma_distributor", "ngo": "ngo_program", "direct": "direct_sales"},
}


def _snake(value: str) -> str:
    return "_".join(str(value).strip().lower().replace("-", " ").split())


def normalise_labels(table: str, df: pd.DataFrame) -> tuple[pd.DataFrame, list[dict]]:
    """Fix inconsistent spellings such as 'PHARMACY ', 'Pharmacy' and 'pharm' -> 'pharmacy'."""
    df = df.copy()
    contract = CONTRACTS[table]
    fixes: list[dict] = []
    for col in contract.labels:
        original = df[col].astype("string")
        if col == "region":
            fixed = original.str.strip().str.title()
        else:
            fixed = original.map(lambda v: v if pd.isna(v) else _snake(v), na_action="ignore")
            fixed = fixed.replace(LABEL_ALIASES.get(col, {}))
        changed = original.notna() & (original != fixed)
        for rid, old, new in zip(
            df.loc[changed, contract.id_column], original[changed], fixed[changed], strict=True
        ):
            fixes.append(
                {
                    "table": table,
                    "record_id": str(rid),
                    "rule": "inconsistent_label",
                    "detail": f"{col}: '{old}' -> '{new}'",
                    "action": "fixed",
                }
            )
        df[col] = fixed
    return df, fixes


def cast_types(table: str, df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    contract = CONTRACTS[table]
    for col in contract.dates + contract.nullable_dates:
        df[col] = pd.to_datetime(df[col], errors="coerce")
    for col in contract.ranges:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    for col in (
        "approval_flag",
        "requested_tenor_months",
        "staff_count",
        "items_count",
        "installment_number",
    ):
        if col in df.columns:
            df[col] = df[col].astype("Int64")
    if "default_flag" in df.columns:
        df["default_flag"] = df["default_flag"].astype("Int64")
    return df


def clean_tables(raw: dict[str, pd.DataFrame]) -> tuple[dict[str, pd.DataFrame], pd.DataFrame, dict]:
    """Validate and clean all tables in dependency order. Returns (clean, issues, report)."""
    clean: dict[str, pd.DataFrame] = {}
    all_issues: list[dict] = []
    summary: dict[str, dict] = {}
    valid_keys: dict[str, set[str]] = {}

    for table in VALIDATION_ORDER:
        df = raw[table].copy()
        check_required_columns(table, df)
        rows_in = len(df)

        df, fixes = normalise_labels(table, df)
        dup_mask, dup_issues = find_duplicates(table, df)
        df = df[~dup_mask]
        reject_mask, issues = find_invalid_records(table, df, valid_keys)
        if table == "repayments":
            order_mask, order_issues = check_repayment_dates(df, clean["applications"])
            order_mask = order_mask & ~reject_mask
            issues += [i for i in order_issues if i["record_id"] in set(df.loc[order_mask, "repayment_id"])]
            reject_mask = reject_mask | order_mask
        df = cast_types(table, df[~reject_mask]).reset_index(drop=True)

        clean[table] = df
        valid_keys[table] = set(df[CONTRACTS[table].id_column].astype(str))
        all_issues += fixes + dup_issues + issues
        summary[table] = {
            "rows_in": rows_in,
            "duplicates_dropped": int(dup_mask.sum()),
            "rows_rejected": int(reject_mask.sum()),
            "labels_fixed": len(fixes),
            "rows_out": len(df),
        }
        log.info(
            "Validated %-26s in=%6d dup=%3d rejected=%3d fixed=%3d out=%6d",
            table,
            rows_in,
            summary[table]["duplicates_dropped"],
            summary[table]["rows_rejected"],
            len(fixes),
            len(df),
        )

    issues_df = pd.DataFrame(all_issues, columns=["table", "record_id", "rule", "detail", "action"])
    total_in = sum(s["rows_in"] for s in summary.values())
    total_removed = sum(s["rows_rejected"] + s["duplicates_dropped"] for s in summary.values())
    report = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "tables": summary,
        "rule_counts": issues_df.groupby(["table", "rule"])
        .size()
        .rename("count")
        .reset_index()
        .to_dict("records"),
        "total_rows_in": total_in,
        "total_rows_removed": total_removed,
        "removed_share": round(total_removed / max(total_in, 1), 5),
    }
    return clean, issues_df, report


def _write_markdown_report(report: dict, issues: pd.DataFrame, paths: Paths) -> None:
    lines = [
        "# Data Validation Report",
        "",
        f"Generated: {report['generated_at']}",
        "",
        "Why this matters: the model and the API can only be as good as the data they receive.",
        "Every record below was checked against the table's contract before any feature was built.",
        "",
        "| Table | Rows in | Duplicates dropped | Rejected | Labels fixed | Rows out |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for t, s in report["tables"].items():
        lines.append(
            f"| {t} | {s['rows_in']:,} | {s['duplicates_dropped']} | {s['rows_rejected']} | "
            f"{s['labels_fixed']} | {s['rows_out']:,} |"
        )
    lines += ["", "## Issues by rule", "", "| Table | Rule | Count |", "|---|---|---:|"]
    for r in report["rule_counts"]:
        lines.append(f"| {r['table']} | {r['rule']} | {r['count']} |")
    lines += ["", "## Example issues", ""]
    if not issues.empty:
        sample = issues.groupby("rule").head(2)
        lines += ["| Table | Record | Rule | Detail | Action |", "|---|---|---|---|---|"]
        for r in sample.itertuples(index=False):
            lines.append(f"| {r.table} | {r.record_id} | {r.rule} | {r.detail} | {r.action} |")
    lines += [
        "",
        f"Overall {report['total_rows_removed']:,} of {report['total_rows_in']:,} rows removed "
        f"({report['removed_share']:.2%}). Full list: `data/processed/rejected_records.csv`.",
    ]
    paths.business_report("data_validation_report").write_text("\n".join(lines) + "\n")


def run(raw: dict[str, pd.DataFrame] | None = None, paths: Paths | None = None) -> dict[str, pd.DataFrame]:
    from src.data.ingest import ingest_raw

    paths = paths or Paths()
    raw = raw if raw is not None else ingest_raw(paths)
    clean, issues, report = clean_tables(raw)

    for name, df in clean.items():
        df.to_parquet(paths.processed(name), index=False)
        db.write_table(df, name)
    issues.to_csv(paths.rejected_records, index=False)
    paths.validation_report_json.write_text(json.dumps(report, indent=2))
    _write_markdown_report(report, issues, paths)
    log.info(
        "Cleaning complete: removed %d rows (%.2f%%), fixed %d labels",
        report["total_rows_removed"],
        100 * report["removed_share"],
        int((issues["action"] == "fixed").sum()),
    )
    return clean


if __name__ == "__main__":
    run()
