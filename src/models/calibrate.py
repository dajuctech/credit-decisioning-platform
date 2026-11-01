"""Calibration (AI Engineering).

A model's raw output is a ranking, not a trustworthy probability. Calibration
re-maps raw outputs so that "20% default risk" really means about 1 in 5 such
borrowers default. That matters because thresholds, credit limits and
expected-loss estimates are all set on the probability itself.

We use Platt scaling: a one-feature logistic regression fitted on a separate
validation set (never on the training data).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression

_EPS = 1e-6


def _logit(p: np.ndarray) -> np.ndarray:
    p = np.clip(np.asarray(p, dtype=float), _EPS, 1 - _EPS)
    return np.log(p / (1 - p))


class PlattCalibrator:
    def __init__(self) -> None:
        self.model = LogisticRegression(C=1e6)

    def fit(self, raw_pd: np.ndarray, y: np.ndarray) -> PlattCalibrator:
        self.model.fit(_logit(raw_pd).reshape(-1, 1), np.asarray(y).astype(int))
        return self

    def transform(self, raw_pd: np.ndarray) -> np.ndarray:
        return self.model.predict_proba(_logit(raw_pd).reshape(-1, 1))[:, 1]

    @property
    def params(self) -> dict:
        return {"slope": float(self.model.coef_[0][0]), "intercept": float(self.model.intercept_[0])}


def calibration_table(y: np.ndarray, p: np.ndarray, n_bins: int = 10) -> pd.DataFrame:
    """Group predictions into bins and compare predicted vs actual default rates."""
    df = pd.DataFrame({"y": np.asarray(y).astype(float), "p": np.asarray(p, dtype=float)})
    df["bin"] = pd.qcut(df["p"].rank(method="first"), q=min(n_bins, len(df)), labels=False)
    out = (
        df.groupby("bin")
        .agg(predicted=("p", "mean"), observed=("y", "mean"), count=("y", "size"))
        .reset_index()
    )
    return out


def expected_calibration_error(y: np.ndarray, p: np.ndarray, n_bins: int = 10) -> float:
    t = calibration_table(y, p, n_bins)
    return float((t["count"] * (t["predicted"] - t["observed"]).abs()).sum() / t["count"].sum())
