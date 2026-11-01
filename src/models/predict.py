"""Scoring (the single place where model + policy + explanations come together).

Both the batch pipeline and the API call `CreditScorer.score_frame`, so a
borrower gets exactly the same answer whichever route their data takes.
"""

from __future__ import annotations

import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

from src.data.feature_engineering import add_derived_features
from src.models.decision import CORE_FEATURES, DecisionPolicy
from src.models.explain import feature_impacts, reason_codes, reason_details
from src.utils.paths import Paths


class ModelNotFoundError(FileNotFoundError):
    pass


class CreditScorer:
    def __init__(self, bundle: dict):
        self.bundle = bundle
        self.model = bundle["model"]
        self.calibrator = bundle["calibrator"]
        self.features: list[str] = bundle["features"]
        self.reference: dict[str, float] = bundle["reference_values"]
        self.policy = DecisionPolicy(**bundle["thresholds"])
        self.version: str = bundle["model_version"]

    # ------------------------------------------------------------------ load
    @classmethod
    def load(cls, path: Path | None = None, paths: Paths | None = None) -> CreditScorer:
        paths = paths or Paths()
        if path is None:
            # MODEL_VERSION pins a specific artefact; otherwise follow the "latest" pointer.
            pinned = paths.model_file()
            pointer = paths.latest_model_pointer
            if pinned.exists():
                path = pinned
            elif pointer.exists():
                path = paths.s.models_dir / json.loads(pointer.read_text())["model_file"]
            else:
                raise ModelNotFoundError("No trained model found. Run the pipeline (`make pipeline`) first.")
        if not Path(path).exists():
            raise ModelNotFoundError(f"Model file not found: {path}")
        return cls(joblib.load(path))

    @property
    def info(self) -> dict:
        b = self.bundle
        return {
            "model_version": self.version,
            "model_type": b["model_type"],
            "trained_at": b["trained_at"],
            "features": self.features,
            "thresholds": b["thresholds"],
            "calibration": b["calibration_params"],
            "training_rows": b["training_rows"],
            "metrics": b["metrics_summary"],
        }

    # ----------------------------------------------------------------- score
    def _prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        df = add_derived_features(df)
        X = pd.DataFrame(
            {f: pd.to_numeric(df.get(f), errors="coerce") for f in self.features}, index=df.index
        )
        return X.astype(float)

    def predict_pd(self, X: pd.DataFrame) -> np.ndarray:
        raw = self.model.predict_proba(X[self.features])[:, 1]
        return self.calibrator.transform(raw)

    def predict_score(self, X: pd.DataFrame) -> np.ndarray:
        return 1.0 - self.predict_pd(X)

    def score_frame(self, df: pd.DataFrame, explain: bool = True) -> pd.DataFrame:
        """Score applications. `df` needs MODEL_FEATURES (missing values allowed) plus optional
        `requested_tenor_months`. Returns one output row per input row."""
        df = df.reset_index(drop=True)
        X = self._prepare(df)
        scores = self.predict_score(X)
        impacts = feature_impacts(X, self.predict_score, self.reference) if explain else None

        rows = []
        for i in range(len(df)):
            missing = [f for f in CORE_FEATURES if pd.isna(X.at[i, f])]
            s = float(scores[i])
            proc = X.at[i, "recent_procurement_value"]
            proc = None if pd.isna(proc) else float(proc)
            requested = float(X.at[i, "requested_amount"])
            decision = self.policy.decide(s, len(missing), procurement_known=proc is not None)
            limit = self.policy.recommend_limit(s, decision, requested, proc)
            if decision == "approve" and limit < self.policy.round_to:
                # purchases too small to support even the minimum loan: never "approve" with a 0 limit
                decision = "manual_review"
                limit = self.policy.recommend_limit(s, decision, requested, proc)
            tenor_req = df.at[i, "requested_tenor_months"] if "requested_tenor_months" in df.columns else None
            tenor_req = None if tenor_req is None or pd.isna(tenor_req) else int(tenor_req)
            row = {
                "score": round(s, 4),
                "probability_of_default": round(1 - s, 4),
                "decision": decision,
                "risk_band": self.policy.band(s),
                "recommended_limit": limit,
                "recommended_tenor_months": self.policy.recommend_tenor(s, decision, tenor_req),
                "missing_core_features": missing,
            }
            if impacts is not None:
                # A missing value says nothing about the borrower, so it never becomes a reason.
                known = impacts.iloc[i][X.iloc[i].notna()]
                row["reason_codes"] = reason_codes(known, decision, missing)
                row["reason_details"] = reason_details(known, X.iloc[i])
            rows.append(row)
        return pd.DataFrame(rows)
