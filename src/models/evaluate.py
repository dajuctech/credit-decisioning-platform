"""Model evaluation (AI Engineering).

Answers: "Is the model good enough, and what happens to the business at
different thresholds?" All metrics treat *default* as the positive class:
"recall" = share of real defaults the model flags before money goes out.

The model "flags" an application when it is not auto-approved
(score < approve_threshold).
"""

from __future__ import annotations

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns
from sklearn.metrics import (
    brier_score_loss,
    confusion_matrix,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
    roc_curve,
)

from src.models.calibrate import calibration_table, expected_calibration_error
from src.utils.paths import Paths

sns.set_theme(style="whitegrid")


def classification_metrics(y: np.ndarray, score: np.ndarray, approve_threshold: float) -> dict:
    y = np.asarray(y).astype(int)
    pd_hat = 1 - np.asarray(score)
    flagged = (np.asarray(score) < approve_threshold).astype(int)
    tn, fp, fn, tp = confusion_matrix(y, flagged, labels=[0, 1]).ravel()
    return {
        "rows": int(len(y)),
        "default_rate": round(float(y.mean()), 4),
        "roc_auc": round(float(roc_auc_score(y, pd_hat)), 4) if len(set(y)) > 1 else None,
        "precision": round(float(precision_score(y, flagged, zero_division=0)), 4),
        "recall": round(float(recall_score(y, flagged, zero_division=0)), 4),
        "f1": round(float(f1_score(y, flagged, zero_division=0)), 4),
        "brier_score": round(float(brier_score_loss(y, pd_hat)), 4),
        "expected_calibration_error": round(expected_calibration_error(y, pd_hat), 4),
        "confusion_matrix": {
            "true_negative": int(tn),
            "false_positive": int(fp),
            "false_negative": int(fn),
            "true_positive": int(tp),
        },
        "auto_approval_rate": round(float(1 - flagged.mean()), 4),
        "bad_rate_among_approved": round(float(y[flagged == 0].mean()), 4) if (flagged == 0).any() else None,
    }


def threshold_simulation(y: np.ndarray, score: np.ndarray, amounts: np.ndarray | None = None) -> pd.DataFrame:
    """What approval rate, bad rate and loss would each threshold produce?"""
    y = np.asarray(y).astype(int)
    score = np.asarray(score)
    amounts = np.ones_like(score) if amounts is None else np.asarray(amounts, dtype=float)
    rows = []
    for t in np.round(np.arange(0.60, 0.991, 0.01), 2):
        approved = score >= t
        rows.append(
            {
                "threshold": t,
                "approval_rate": round(float(approved.mean()), 4),
                "bad_rate_among_approved": round(float(y[approved].mean()), 4) if approved.any() else None,
                "defaults_caught_share": round(
                    float((~approved & (y == 1)).sum() / max((y == 1).sum(), 1)), 4
                ),
                "good_borrowers_declined_share": round(
                    float((~approved & (y == 0)).sum() / max((y == 0).sum(), 1)), 4
                ),
                "approved_volume": round(float(amounts[approved].sum()), 2),
                "defaulted_volume_approved": round(float(amounts[approved & (y == 1)].sum()), 2),
            }
        )
    return pd.DataFrame(rows)


def choose_approve_threshold(sim: pd.DataFrame, target_bad_rate: float, floor: float) -> float:
    """Lowest threshold (= most approvals) whose bad rate stays within risk appetite."""
    ok = sim[(sim["bad_rate_among_approved"] <= target_bad_rate) & (sim["threshold"] > floor)]
    if ok.empty:
        return float(sim["threshold"].max())
    return float(ok["threshold"].min())


def performance_by_month(df: pd.DataFrame, approve_threshold: float) -> pd.DataFrame:
    """Compare model behaviour across time (PRD 11.2)."""
    rows = []
    for month, g in df.groupby("application_month"):
        y = g["default_flag"].astype(int).to_numpy()
        rows.append(
            {
                "month": month,
                "split": g["split"].mode().iat[0],
                "applications": len(g),
                "actual_default_rate": round(float(y.mean()), 4),
                "predicted_default_rate": round(float((1 - g["score"]).mean()), 4),
                "average_score": round(float(g["score"].mean()), 4),
                "auto_approval_rate": round(float((g["score"] >= approve_threshold).mean()), 4),
                "roc_auc": round(float(roc_auc_score(y, 1 - g["score"])), 4)
                if 0 < y.sum() < len(y)
                else None,
            }
        )
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- figures
def plot_roc(curves: dict[str, tuple[np.ndarray, np.ndarray]], paths: Paths) -> None:
    fig, ax = plt.subplots(figsize=(6, 5))
    for name, (y, p) in curves.items():
        fpr, tpr, _ = roc_curve(y, p)
        ax.plot(fpr, tpr, label=f"{name} (AUC {roc_auc_score(y, p):.3f})")
    ax.plot([0, 1], [0, 1], "--", color="grey", lw=1)
    ax.set(
        xlabel="False positive rate", ylabel="True positive rate", title="ROC curve - out-of-time test set"
    )
    ax.legend(loc="lower right", fontsize=8)
    fig.tight_layout()
    fig.savefig(paths.figure("roc_curve"), dpi=120)
    plt.close(fig)


def plot_calibration(y: np.ndarray, raw: np.ndarray, calibrated: np.ndarray, paths: Paths) -> None:
    fig, ax = plt.subplots(figsize=(6, 5))
    for label, p in [("before calibration", raw), ("after calibration", calibrated)]:
        t = calibration_table(y, p, n_bins=8)
        ax.plot(t["predicted"], t["observed"], marker="o", label=label)
    lim = max(0.4, float(np.max(calibrated)) * 1.05)
    ax.plot([0, lim], [0, lim], "--", color="grey", lw=1, label="perfect calibration")
    ax.set(xlabel="Predicted default rate", ylabel="Observed default rate", title="Calibration - test set")
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(paths.figure("calibration_curve"), dpi=120)
    plt.close(fig)


def plot_threshold_simulation(sim: pd.DataFrame, chosen: float, paths: Paths) -> None:
    fig, ax = plt.subplots(figsize=(7, 4.5))
    ax.plot(sim["threshold"], sim["approval_rate"], label="approval rate")
    ax.plot(sim["threshold"], sim["bad_rate_among_approved"], label="bad rate among approved")
    ax.plot(sim["threshold"], sim["defaults_caught_share"], label="defaults caught")
    ax.axvline(chosen, color="black", ls="--", lw=1, label=f"chosen threshold {chosen:.2f}")
    ax.set(xlabel="Approve if score >=", ylabel="rate", title="Approval-rate simulation (validation set)")
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(paths.figure("threshold_simulation"), dpi=120)
    plt.close(fig)


def plot_score_distribution(scores: pd.DataFrame, thresholds: dict, paths: Paths) -> None:
    fig, ax = plt.subplots(figsize=(7, 4.5))
    sns.histplot(
        data=scores, x="score", hue="default_flag", bins=40, stat="density", common_norm=False, ax=ax
    )
    ax.axvline(thresholds["approve_threshold"], color="green", ls="--", lw=1)
    ax.axvline(thresholds["decline_threshold"], color="red", ls="--", lw=1)
    ax.set(
        title="Score distribution by actual outcome (1 = defaulted)",
        xlabel="score (probability of repayment)",
    )
    fig.tight_layout()
    fig.savefig(paths.figure("score_distribution"), dpi=120)
    plt.close(fig)


def plot_confusion(cm: dict, paths: Paths) -> None:
    m = np.array([[cm["true_negative"], cm["false_positive"]], [cm["false_negative"], cm["true_positive"]]])
    fig, ax = plt.subplots(figsize=(4.5, 4))
    sns.heatmap(
        m,
        annot=True,
        fmt="d",
        cmap="Blues",
        cbar=False,
        ax=ax,
        xticklabels=["auto-approved", "flagged"],
        yticklabels=["repaid", "defaulted"],
    )
    ax.set(title="Confusion matrix - test set", xlabel="model", ylabel="actual")
    fig.tight_layout()
    fig.savefig(paths.figure("confusion_matrix"), dpi=120)
    plt.close(fig)
