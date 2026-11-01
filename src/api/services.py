"""Business logic behind the API (Backend Engineering).

The route handlers in main.py stay thin; everything they need lives here:
  * loading the model artefact and feature store once at start-up
  * filling missing request fields from the feature store
  * scoring via the shared CreditScorer (same code as the batch pipeline)
  * writing every decision to `model_scores` so monitoring can read real traffic
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime

import numpy as np
import pandas as pd

from src.api.schemas import ScoreRequest
from src.data.feature_engineering import (
    DERIVED_FEATURES,
    FEATURE_DESCRIPTIONS,
    MODEL_FEATURES,
    add_derived_features,
)
from src.models.predict import CreditScorer, ModelNotFoundError
from src.utils import db
from src.utils.logger import get_logger
from src.utils.paths import Paths

log = get_logger(__name__)

LOOKUP_FEATURES = [f for f in MODEL_FEATURES if f not in DERIVED_FEATURES and f != "requested_amount"]


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


class ScoringService:
    def __init__(self, paths: Paths | None = None):
        self.paths = paths or Paths()
        self.started = time.time()
        self.scorer: CreditScorer | None = None
        self.feature_store = pd.DataFrame()
        self.load_error: str | None = None
        self.reload()

    def reload(self) -> None:
        db.init_service_tables()
        try:
            self.scorer = CreditScorer.load(paths=self.paths)
            self.load_error = None
            log.info("Loaded model %s (%s)", self.scorer.version, self.scorer.bundle["model_type"])
        except ModelNotFoundError as e:
            self.scorer, self.load_error = None, str(e)
            log.warning("Model not loaded: %s", e)
        if self.paths.feature_store.exists():
            self.feature_store = pd.read_parquet(self.paths.feature_store).set_index("borrower_id")
            log.info("Loaded feature store with %d borrowers", len(self.feature_store))

    @property
    def ready(self) -> bool:
        return self.scorer is not None

    # ------------------------------------------------------------- health
    def health(self, app_env: str) -> dict:
        try:
            db.read_sql("SELECT 1 AS ok")
            db_ok = True
        except Exception:  # noqa: BLE001 - health must never raise
            db_ok = False
        return {
            "status": "ok" if self.ready and db_ok else "degraded",
            "model_loaded": self.ready,
            "model_version": self.scorer.version if self.scorer else None,
            "database_ok": db_ok,
            "feature_store_rows": int(len(self.feature_store)),
            "uptime_seconds": round(time.time() - self.started, 1),
            "app_env": app_env,
        }

    def model_info(self) -> dict:
        self._require_model()
        info = self.scorer.info
        info["feature_descriptions"] = FEATURE_DESCRIPTIONS
        return info

    # ------------------------------------------------------------- scoring
    def _require_model(self) -> None:
        if not self.ready:
            raise ModelNotFoundError(self.load_error or "Model not loaded")

    def _assemble(
        self, requests: list[ScoreRequest]
    ) -> tuple[pd.DataFrame, list[list[str]], list[list[str]]]:
        rows, from_store, missing_in_request = [], [], []
        for r in requests:
            data = r.model_dump()
            stored = (
                self.feature_store.loc[r.borrower_id] if r.borrower_id in self.feature_store.index else None
            )
            filled, missing = [], []
            for f in LOOKUP_FEATURES:
                if data.get(f) is None:
                    missing.append(f)
                    # Only fill fields the caller left out. An explicit null means "known to be empty",
                    # and filling it from a later snapshot would leak information the caller did not have.
                    if f in r.model_fields_set:
                        continue
                    if stored is not None and pd.notna(stored.get(f)):
                        data[f] = float(stored[f])
                        filled.append(f)
            if data.get("partner_id") is None and stored is not None:
                data["partner_id"] = stored.get("partner_id")
            rows.append(data)
            from_store.append(filled)
            missing_in_request.append(missing)
        df = pd.DataFrame(rows)
        for f in MODEL_FEATURES:
            if f not in df.columns:
                df[f] = np.nan
        return add_derived_features(df), from_store, missing_in_request

    def score(self, requests: list[ScoreRequest], request_id: str, source: str = "api") -> list[dict]:
        self._require_model()
        df, from_store, missing_in_request = self._assemble(requests)
        out = self.scorer.score_frame(df)
        ts = utc_now()
        results, records = [], []
        for i, r in enumerate(requests):
            o = out.iloc[i]
            res = {
                "request_id": request_id,
                "application_id": r.application_id,
                "borrower_id": r.borrower_id,
                "model_version": self.scorer.version,
                "score": float(o["score"]),
                "probability_of_default": float(o["probability_of_default"]),
                "decision": o["decision"],
                "risk_band": o["risk_band"],
                "recommended_limit": float(o["recommended_limit"]),
                "recommended_tenor_months": None
                if pd.isna(o["recommended_tenor_months"])
                else int(o["recommended_tenor_months"]),
                "reason_codes": list(o["reason_codes"]),
                "reason_details": list(o["reason_details"]),
                "features_from_store": from_store[i],
                "missing_core_features": list(o["missing_core_features"]),
                "timestamp": ts,
            }
            results.append(res)
            feature_values = {
                f: (None if pd.isna(df.at[i, f]) else float(df.at[i, f]))
                for f in MODEL_FEATURES
                if f in df.columns
            }
            records.append(
                {
                    "request_id": request_id,
                    "application_id": r.application_id,
                    "borrower_id": r.borrower_id,
                    "partner_id": df.at[i, "partner_id"],
                    "model_version": self.scorer.version,
                    "score": res["score"],
                    "decision": res["decision"],
                    "recommended_limit": res["recommended_limit"],
                    "recommended_tenor_months": res["recommended_tenor_months"],
                    "reason_codes": json.dumps(res["reason_codes"]),
                    "missing_features": json.dumps(missing_in_request[i]),
                    "features_json": json.dumps(feature_values),
                    "source": source,
                    "scored_at": ts,
                }
            )
        db.write_table(pd.DataFrame(records), "model_scores", if_exists="append")
        return results

    # ------------------------------------------------------------- metrics
    def metrics_summary(self) -> dict:
        model_metrics = {}
        if self.paths.training_metrics.exists():
            m = json.loads(self.paths.training_metrics.read_text())
            model_metrics = {
                "champion": m["champion"],
                "thresholds": m["thresholds"],
                "test": {
                    k: m["test"][k]
                    for k in (
                        "roc_auc",
                        "precision",
                        "recall",
                        "f1",
                        "auto_approval_rate",
                        "bad_rate_among_approved",
                        "brier_score",
                    )
                },
            }

        req = db.read_sql("SELECT status_code, latency_ms, path FROM api_requests WHERE path LIKE '%score%'")
        service = {"scoring_requests": int(len(req))}
        if len(req):
            service |= {
                "failed_requests": int((req["status_code"] >= 400).sum()),
                "error_rate": round(float((req["status_code"] >= 400).mean()), 4),
                "server_errors": int((req["status_code"] >= 500).sum()),
                "latency_p50_ms": round(float(req["latency_ms"].quantile(0.5)), 2),
                "latency_p95_ms": round(float(req["latency_ms"].quantile(0.95)), 2),
                "requests_by_status": {str(k): int(v) for k, v in req["status_code"].value_counts().items()},
            }

        scores = db.read_sql("SELECT score, decision FROM model_scores")
        decisions = {"scored_applications": int(len(scores))}
        if len(scores):
            share = scores["decision"].value_counts(normalize=True)
            decisions |= {
                "approval_rate": round(float(share.get("approve", 0)), 4),
                "manual_review_rate": round(float(share.get("manual_review", 0)), 4),
                "decline_rate": round(float(share.get("decline", 0)), 4),
                "average_score": round(float(scores["score"].mean()), 4),
            }

        monitoring = {}
        if db.table_exists("monitoring_snapshots"):
            snap = db.read_sql(
                "SELECT * FROM monitoring_snapshots WHERE snapshot_id = "
                "(SELECT snapshot_id FROM monitoring_snapshots ORDER BY snapshot_at DESC LIMIT 1)"
            )
            if len(snap):
                monitoring = {
                    "snapshot_at": snap["snapshot_at"].iat[0],
                    "status_counts": snap["status"].value_counts().to_dict(),
                    "alerts_by_layer": snap[snap["status"] == "alert"]["owner_layer"]
                    .value_counts()
                    .to_dict(),
                }
        return {
            "model_version": self.scorer.version if self.scorer else None,
            "model_metrics": model_metrics,
            "service": service,
            "decisions": decisions,
            "monitoring": monitoring,
        }
