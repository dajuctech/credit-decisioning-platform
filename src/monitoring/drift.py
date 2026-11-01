"""Data drift checks (Monitoring -> points back to Data Engineering).

PSI (Population Stability Index) compares how a feature is spread across
buckets now versus at training time:
    PSI < 0.10        stable
    0.10 - 0.25       moderate shift, keep an eye on it
    > 0.25            major shift, investigate the data feed / consider retraining

The KS test asks "could these two samples come from the same distribution?"
A tiny p-value with a large KS statistic means the distribution really changed.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.stats import ks_2samp

PSI_WARN, PSI_ALERT = 0.10, 0.25

# PSI is unreliable for features that take only a handful of values (one value
# per partner per month). For these we compare the average instead:
# feature -> (warn, alert) absolute change in the mean.
MEAN_SHIFT_FEATURES = {"partner_late_rate_180d": (0.03, 0.06)}
MISSING_WARN_PP, MISSING_ALERT_PP = 0.05, 0.10
_EPS = 1e-4


def psi(expected: pd.Series | np.ndarray, actual: pd.Series | np.ndarray, bins: int = 10) -> float:
    e = pd.Series(expected, dtype=float).dropna().to_numpy()
    a = pd.Series(actual, dtype=float).dropna().to_numpy()
    if len(e) == 0 or len(a) == 0:
        return float("nan")
    edges = np.unique(np.quantile(e, np.linspace(0, 1, bins + 1)))
    if len(edges) < 2:
        return 0.0 if np.allclose(a, e[0]) else float(PSI_ALERT * 4)
    edges[0], edges[-1] = -np.inf, np.inf
    e_pct = np.histogram(e, edges)[0] / len(e)
    a_pct = np.histogram(a, edges)[0] / len(a)
    e_pct, a_pct = np.clip(e_pct, _EPS, None), np.clip(a_pct, _EPS, None)
    return float(np.sum((a_pct - e_pct) * np.log(a_pct / e_pct)))


def ks(expected, actual) -> tuple[float, float]:
    e = pd.Series(expected, dtype=float).dropna()
    a = pd.Series(actual, dtype=float).dropna()
    if len(e) < 2 or len(a) < 2:
        return float("nan"), float("nan")
    r = ks_2samp(e, a)
    return float(r.statistic), float(r.pvalue)


def psi_status(value: float) -> str:
    if np.isnan(value):
        return "warn"
    return "alert" if value > PSI_ALERT else "warn" if value > PSI_WARN else "ok"


def missing_status(delta: float) -> str:
    return "alert" if delta > MISSING_ALERT_PP else "warn" if delta > MISSING_WARN_PP else "ok"


def feature_drift(
    baseline: pd.DataFrame,
    current: pd.DataFrame,
    features: list[str],
    current_missing: pd.Series | None = None,
) -> pd.DataFrame:
    """One row per feature with PSI, KS and missing-rate comparison.

    `current_missing` (feature -> share) lets us measure missing values as they
    arrived in API requests, before the feature store filled any gaps.
    """
    rows = []
    for f in features:
        ks_stat, ks_p = ks(baseline[f], current[f])
        if f in MEAN_SHIFT_FEATURES:
            warn, alert = MEAN_SHIFT_FEATURES[f]
            shift = (
                abs(float(current[f].mean() - baseline[f].mean()))
                if current[f].notna().any()
                else float("nan")
            )
            method, p = "mean_shift", shift
            status = (
                "warn" if np.isnan(shift) else "alert" if shift > alert else "warn" if shift > warn else "ok"
            )
        else:
            method, p = "psi", psi(baseline[f], current[f])
            status = psi_status(p)
        base_miss = float(baseline[f].isna().mean())
        cur_miss = (
            float(current_missing.get(f, current[f].isna().mean()))
            if current_missing is not None
            else float(current[f].isna().mean())
        )
        rows.append(
            {
                "feature": f,
                "method": method,
                "drift_score": round(p, 4),
                "drift_status": status,
                "ks_statistic": round(ks_stat, 4),
                "ks_pvalue": float(f"{ks_p:.3g}") if not np.isnan(ks_p) else None,
                "baseline_median": round(float(baseline[f].median()), 4),
                "current_median": round(float(current[f].median()), 4) if current[f].notna().any() else None,
                "baseline_missing_rate": round(base_miss, 4),
                "current_missing_rate": round(cur_miss, 4),
                "missing_status": missing_status(cur_miss - base_miss),
            }
        )
    return pd.DataFrame(rows).sort_values("drift_score", ascending=False).reset_index(drop=True)
