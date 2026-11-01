"""Named locations for every file the platform reads or writes.

Keeping file names in one place means each layer agrees on where the
previous layer left its output.
"""

from __future__ import annotations

from pathlib import Path

from src.config import Settings, get_settings

RAW_TABLES = [
    "partners",
    "borrowers",
    "applications",
    "procurement_transactions",
    "repayments",
    "stock_movements",
]


class Paths:
    def __init__(self, settings: Settings | None = None):
        self.s = settings or get_settings()

    # --- Data engineering -------------------------------------------------
    def raw(self, table: str) -> Path:
        return self.s.raw_dir / f"{table}.csv"

    @property
    def incoming_applications(self) -> Path:
        return self.s.raw_dir / "incoming_applications.csv"

    def processed(self, table: str) -> Path:
        return self.s.processed_dir / f"{table}.parquet"

    @property
    def validation_report_json(self) -> Path:
        return self.s.processed_dir / "validation_report.json"

    @property
    def rejected_records(self) -> Path:
        return self.s.processed_dir / "rejected_records.csv"

    @property
    def features(self) -> Path:
        return self.s.processed_dir / "application_features.parquet"

    @property
    def feature_store(self) -> Path:
        return self.s.processed_dir / "borrower_feature_store.parquet"

    @property
    def incoming_features(self) -> Path:
        return self.s.processed_dir / "incoming_application_features.parquet"

    # --- AI engineering ---------------------------------------------------
    def model_file(self, version: str | None = None) -> Path:
        return self.s.models_dir / f"credit_model_{version or self.s.model_version}.joblib"

    @property
    def latest_model_pointer(self) -> Path:
        return self.s.models_dir / "latest.json"

    @property
    def training_metrics(self) -> Path:
        return self.s.reports_dir / "metrics" / "training_metrics.json"

    @property
    def threshold_simulation(self) -> Path:
        return self.s.reports_dir / "metrics" / "threshold_simulation.csv"

    @property
    def baseline_scores(self) -> Path:
        return self.s.processed_dir / "baseline_scores.parquet"

    # --- Monitoring -------------------------------------------------------
    @property
    def monitoring_checks(self) -> Path:
        return self.s.monitoring_dir / "monitoring_checks.csv"

    @property
    def drift_table(self) -> Path:
        return self.s.monitoring_dir / "feature_drift.csv"

    def business_report(self, name: str) -> Path:
        return self.s.reports_dir / "business" / f"{name}.md"

    def figure(self, name: str) -> Path:
        return self.s.reports_dir / "figures" / f"{name}.png"
