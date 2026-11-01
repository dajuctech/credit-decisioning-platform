"""Monitoring checks: the feedback loop that ties all four layers together.

Every check produces one row with a status (ok / warn / alert) and the
engineering layer that owns the fix:

    Data Engineering     feature drift, missing-data spikes, validation reject rate
    AI Engineering       score shift, approval-rate movement, partner approval shifts
    Backend Engineering  failed requests (bad payloads, server errors)
    Cloud Engineering    latency, service availability

Inputs are real API outputs: `model_scores` and `api_requests` written by the
running service. They are compared with a reference window (the 9 months
before launch, Apr-Dec 2025). Drift against the original training months is
also reported, for information, as `psi_vs_training`.

Run:  uv run python -m src.monitoring.score_checks
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

import numpy as np
import pandas as pd

from src.data.feature_engineering import MODEL_FEATURES
from src.monitoring.drift import PSI_ALERT, PSI_WARN, feature_drift, psi
from src.utils import db
from src.utils.logger import get_logger
from src.utils.paths import Paths

log = get_logger(__name__)

SEGMENT_FEATURES = ["recent_procurement_value", "loan_to_procurement_ratio", "stock_trend_ratio"]
MIN_SEGMENT_SIZE = 25
REFERENCE_START = pd.Timestamp("2025-04-01")  # same window as baseline_scores (see train.py)
DATA, AI, BACKEND, CLOUD = "Data Engineering", "AI Engineering", "Backend Engineering", "Cloud Engineering"

ACTIONS = {
    "feature_drift": "Check the upstream feed for this feature (format, units, partner changes); retrain if the "
    "shift is real and persistent.",
    "missing_data_spike": "Find which partner feed stopped sending this field and fix the integration or mapping.",
    "segment_drift": "Check what changed for businesses in this region (supply, prices, partner feed) and "
    "whether the model's decisions there still make sense.",
    "validation_reject_rate": "Review rejected_records.csv with the data owner; tighten source system checks.",
    "score_shift": "Compare score drivers before/after; run a challenger model or plan a retrain.",
    "approval_rate_shift": "Confirm with the credit team whether the shift is expected; review thresholds.",
    "partner_approval_shift": "Deep-dive this partner's applications; check feature drift for their borrowers.",
    "manual_review_rate": "Check staffing for the review queue and which reasons send cases to review.",
    "client_error_rate": "Share the API contract with the integrating partner; add payload checks on their side.",
    "server_error_rate": "Inspect API logs by request_id; add a test that reproduces the failure.",
    "latency_p95": "Check container CPU/memory, scale out replicas, or cache the model/feature store.",
    "service_availability": "Check container health checks and restart policy; review recent deployments.",
    "label_maturity": "Join outcomes when loans reach 6 months on book, then compare predicted vs actual default.",
}


def _status(value: float, warn: float, alert: float) -> str:
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return "warn"
    return "alert" if value > alert else "warn" if value > warn else "ok"


def _check(name, subject, value, warn, alert, layer, explanation, status=None) -> dict:
    return {
        "check_name": name,
        "subject": subject,
        "value": None if value is None else round(float(value), 4),
        "threshold": alert,
        "status": status or _status(value, warn, alert),
        "owner_layer": layer,
        "explanation": explanation,
        "action": ACTIONS[name],
    }


def load_live_scores() -> pd.DataFrame:
    scores = db.read_sql(
        "SELECT s.*, b.region, b.business_type FROM model_scores s "
        "LEFT JOIN borrowers b ON s.borrower_id = b.borrower_id"
    )
    if scores.empty:
        return scores
    feats = pd.DataFrame([json.loads(x) for x in scores["features_json"]])
    scores["missing_list"] = scores["missing_features"].map(json.loads)
    return pd.concat([scores.drop(columns="features_json"), feats.add_prefix("f_")], axis=1)


def _missing_source(live: pd.DataFrame, feature: str) -> str:
    """Point at the partner feed responsible for most of the missing values."""
    is_missing = live["missing_list"].map(lambda m: feature in m)
    if not is_missing.any():
        return ""
    by_partner = live[is_missing]["partner_id"].value_counts()
    top, n = by_partner.index[0], int(by_partner.iloc[0])
    partner_total = int((live["partner_id"] == top).sum())
    return (
        f"; {n / is_missing.sum():.0%} of the gaps come from partner {top} "
        f"(missing in {n / partner_total:.0%} of its requests)"
    )


def data_checks(
    reference: pd.DataFrame, train: pd.DataFrame, live: pd.DataFrame, paths: Paths
) -> tuple[list[dict], pd.DataFrame]:
    current = pd.DataFrame({f: pd.to_numeric(live[f"f_{f}"], errors="coerce") for f in MODEL_FEATURES})
    missing_in_request = {
        f: float(live["missing_list"].map(lambda m, f=f: f in m).mean()) for f in MODEL_FEATURES
    }
    missing_in_request["loan_to_procurement_ratio"] = missing_in_request["recent_procurement_value"]
    drift = feature_drift(
        reference[MODEL_FEATURES].astype(float), current, MODEL_FEATURES, pd.Series(missing_in_request)
    )
    drift["psi_vs_training"] = [round(psi(train[f].astype(float), current[f]), 4) for f in drift["feature"]]
    checks = []
    for r in drift.itertuples():
        label = "PSI" if r.method == "psi" else "mean shift"
        checks.append(
            _check(
                "feature_drift",
                r.feature,
                r.drift_score,
                PSI_WARN,
                PSI_ALERT,
                DATA,
                f"{label} {r.drift_score:.3f}; median {r.baseline_median:,.3f} before launch vs "
                f"{'n/a' if r.current_median is None else f'{r.current_median:,.3f}'} now (KS={r.ks_statistic:.2f})",
                status=r.drift_status,
            )
        )
        delta = r.current_missing_rate - r.baseline_missing_rate
        if delta > 0.02:
            checks.append(
                _check(
                    "missing_data_spike",
                    r.feature,
                    delta,
                    0.05,
                    0.10,
                    DATA,
                    f"missing in {r.current_missing_rate:.0%} of requests vs {r.baseline_missing_rate:.0%} before launch"
                    f"{_missing_source(live, r.feature)}",
                )
            )
    checks += segment_checks(reference, current.assign(region=live["region"].values))
    if paths.validation_report_json.exists():
        v = json.loads(paths.validation_report_json.read_text())
        checks.append(
            _check(
                "validation_reject_rate",
                "all raw tables",
                v["removed_share"],
                0.01,
                0.05,
                DATA,
                f"{v['total_rows_removed']:,} of {v['total_rows_in']:,} raw rows failed validation",
            )
        )
    return checks, drift


def segment_checks(reference: pd.DataFrame, current: pd.DataFrame) -> list[dict]:
    """Drift inside each region. A local shock (e.g. a supply shortage in one region)
    can be invisible in portfolio-wide numbers but obvious inside the segment."""
    checks = []
    for region, cur in current.groupby("region"):
        ref = reference[reference["region"] == region]
        if len(cur) < MIN_SEGMENT_SIZE or len(ref) < MIN_SEGMENT_SIZE:
            continue
        for f in SEGMENT_FEATURES:
            value = psi(ref[f].astype(float), cur[f], bins=5)  # fewer buckets: segments are small
            if value > PSI_WARN:
                checks.append(
                    _check(
                        "segment_drift",
                        f"{region}: {f}",
                        value,
                        PSI_WARN,
                        PSI_ALERT,
                        DATA,
                        f"in {region}, median {f} moved {ref[f].median():.3f} -> {cur[f].median():.3f} "
                        f"(PSI {value:.3f}, {len(cur)} live applications)",
                    )
                )
    if not checks:
        checks.append(
            _check(
                "segment_drift",
                "all regions",
                0.0,
                PSI_WARN,
                PSI_ALERT,
                DATA,
                "no region shows drift in key business features",
            )
        )
    return checks


def ai_checks(baseline: pd.DataFrame, live: pd.DataFrame) -> list[dict]:
    checks = []
    score_psi = psi(baseline["score"], live["score"])
    checks.append(
        _check(
            "score_shift",
            "score distribution",
            score_psi,
            PSI_WARN,
            PSI_ALERT,
            AI,
            f"score PSI {score_psi:.3f}; mean score {baseline['score'].mean():.3f} -> {live['score'].mean():.3f}",
        )
    )
    base_rate = (baseline["decision"] == "approve").mean()
    live_rate = (live["decision"] == "approve").mean()
    checks.append(
        _check(
            "approval_rate_shift",
            "overall",
            abs(live_rate - base_rate),
            0.05,
            0.10,
            AI,
            f"approval rate {base_rate:.1%} before launch vs {live_rate:.1%} live",
        )
    )
    base_rev = (baseline["decision"] == "manual_review").mean()
    live_rev = (live["decision"] == "manual_review").mean()
    checks.append(
        _check(
            "manual_review_rate",
            "overall",
            live_rev - base_rev,
            0.05,
            0.10,
            AI,
            f"manual review share {base_rev:.1%} before launch vs {live_rev:.1%} live",
        )
    )
    b = baseline.groupby("partner_id")["decision"].apply(lambda d: (d == "approve").mean())
    lv = live.groupby("partner_id").agg(
        rate=("decision", lambda d: (d == "approve").mean()), n=("decision", "size")
    )
    for partner, row in lv.iterrows():
        if row["n"] < 15:
            continue
        if partner not in b.index:
            checks.append(
                _check(
                    "partner_approval_shift",
                    partner,
                    row["rate"],
                    0,
                    0,
                    AI,
                    f"new partner with no pre-launch history: {row['rate']:.1%} approved across {int(row['n'])} "
                    "applications",
                    status="warn",
                )
            )
            continue
        delta = abs(row["rate"] - b[partner])
        checks.append(
            _check(
                "partner_approval_shift",
                partner,
                delta,
                0.10,
                0.20,
                AI,
                f"approval {b[partner]:.1%} before launch vs {row['rate']:.1%} live ({int(row['n'])} applications)",
            )
        )
    checks.append(
        _check(
            "label_maturity",
            "live applications",
            None,
            0,
            0,
            AI,
            "outcomes for live loans are not known yet (need 6 months on book); predicted default rate "
            f"is {1 - live['score'].mean():.1%}",
            status="ok",
        )
    )
    return checks


def service_checks(requests: pd.DataFrame, health: dict | None) -> list[dict]:
    checks = []
    scoring = requests[requests["path"].str.contains("score")]
    if len(scoring):
        client_err = float(((scoring["status_code"] >= 400) & (scoring["status_code"] < 500)).mean())
        server_err = float((scoring["status_code"] >= 500).mean())
        p95 = float(scoring.loc[scoring["status_code"] < 400, "latency_ms"].quantile(0.95))
        top_error = scoring["error"].dropna().str.split(":").str[0].value_counts()
        checks.append(
            _check(
                "client_error_rate",
                "/score and /batch-score",
                client_err,
                0.01,
                0.05,
                BACKEND,
                f"{client_err:.1%} of {len(scoring)} scoring requests rejected as invalid "
                f"(most common: {top_error.index[0] if len(top_error) else 'none'})",
            )
        )
        checks.append(
            _check(
                "server_error_rate",
                "/score and /batch-score",
                server_err,
                0.0,
                0.005,
                BACKEND,
                f"{int((scoring['status_code'] >= 500).sum())} server errors",
            )
        )
        checks.append(
            _check(
                "latency_p95",
                "/score and /batch-score",
                p95,
                300,
                1000,
                CLOUD,
                f"95% of successful scoring calls finished within {p95:.0f} ms "
                f"(median {scoring['latency_ms'].median():.0f} ms)",
            )
        )
    if health is not None:
        healthy = health.get("status") == "ok"
        checks.append(
            _check(
                "service_availability",
                "/health",
                0 if healthy else 1,
                0,
                0.5,
                CLOUD,
                f"health={health.get('status')}, model_loaded={health.get('model_loaded')}, "
                f"database_ok={health.get('database_ok')}",
            )
        )
    return checks


def run(paths: Paths | None = None, health: dict | None = None) -> pd.DataFrame:
    paths = paths or Paths()
    live = load_live_scores()
    if live.empty:
        raise RuntimeError("No scoring traffic found. Run src.monitoring.simulate_traffic first.")
    feats = pd.read_parquet(paths.features)
    train = feats[feats["split"] == "train"]
    # Reference window = the 9 months before launch: the population the API was expected to see.
    reference = feats[feats["application_date"] >= REFERENCE_START]
    baseline = pd.read_parquet(paths.baseline_scores)
    requests = db.read_sql("SELECT * FROM api_requests")

    if health is None:
        from src.api.services import ScoringService
        from src.config import get_settings

        health = ScoringService(paths).health(get_settings().app_env)

    d_checks, drift = data_checks(reference, train, live, paths)
    checks = pd.DataFrame(d_checks + ai_checks(baseline, live) + service_checks(requests, health))

    snapshot_id = f"snap_{uuid.uuid4().hex[:10]}"
    snapshot_at = datetime.now(UTC).isoformat(timespec="seconds")
    checks.insert(0, "snapshot_at", snapshot_at)
    checks.insert(0, "snapshot_id", snapshot_id)
    checks.to_csv(paths.monitoring_checks, index=False)
    drift.to_csv(paths.drift_table, index=False)
    db.init_service_tables()
    db.write_table(checks.drop(columns="action"), "monitoring_snapshots", if_exists="append")

    counts = checks["status"].value_counts().to_dict()
    log.info("Monitoring snapshot %s: %s", snapshot_id, counts)
    for r in checks[checks["status"] == "alert"].itertuples():
        log.warning("ALERT [%s] %s / %s: %s", r.owner_layer, r.check_name, r.subject, r.explanation)
    return checks


if __name__ == "__main__":
    run()
