"""Model training (AI Engineering, Milestone 3).

1. Load point-in-time features built by the data layer.
2. Train three candidate models on the oldest months (train split).
3. Pick a champion on the validation months. A simpler model wins ties:
   logistic regression is kept unless another model is clearly better.
4. Calibrate the champion on the validation months.
5. Set decision thresholds from the business's risk appetite.
6. Evaluate once on untouched, later months (out-of-time test).
7. Save one artefact bundle that the API loads.

Run:  uv run python -m src.models.train
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier, RandomForestClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from src.data.feature_engineering import FEATURE_DESCRIPTIONS, MODEL_FEATURES
from src.models import evaluate as ev
from src.models.calibrate import PlattCalibrator
from src.models.predict import CreditScorer
from src.utils import db
from src.utils.logger import get_logger
from src.utils.paths import Paths

log = get_logger(__name__)

# Risk appetite (set by the credit committee, not by the model):
TARGET_BAD_RATE_APPROVED = 0.06  # at most ~6% of auto-approved loans may default
DECLINE_BELOW_SCORE = 0.70  # decline when default risk is above 30%
SIMPLICITY_MARGIN = 0.01  # prefer logistic regression unless another model beats it by > 0.01 AUC


def candidate_models(seed: int) -> dict[str, Pipeline]:
    return {
        "logistic_regression": Pipeline(
            [
                ("impute", SimpleImputer(strategy="median", add_indicator=True)),
                ("scale", StandardScaler()),
                ("model", LogisticRegression(C=0.3, max_iter=2000)),
            ]
        ),
        "random_forest": Pipeline(
            [
                ("impute", SimpleImputer(strategy="median", add_indicator=True)),
                (
                    "model",
                    RandomForestClassifier(
                        n_estimators=400, min_samples_leaf=25, random_state=seed, n_jobs=-1
                    ),
                ),
            ]
        ),
        "gradient_boosting": Pipeline(
            [
                (
                    "model",
                    HistGradientBoostingClassifier(
                        max_iter=250,
                        learning_rate=0.04,
                        max_leaf_nodes=12,
                        min_samples_leaf=30,
                        l2_regularization=1.0,
                        random_state=seed,
                    ),
                )
            ]
        ),
    }


def _xy(df: pd.DataFrame) -> tuple[pd.DataFrame, np.ndarray]:
    return df[MODEL_FEATURES].astype(float), df["default_flag"].astype(int).to_numpy()


def train(paths: Paths | None = None) -> dict:
    paths = paths or Paths()
    settings = paths.s
    feats = pd.read_parquet(paths.features)
    train_df = feats[feats["split"] == "train"]
    valid_df = feats[feats["split"] == "validation"]
    test_df = feats[feats["split"] == "test"]
    X_tr, y_tr = _xy(train_df)
    X_va, y_va = _xy(valid_df)
    X_te, y_te = _xy(test_df)
    log.info("Training rows: train=%d validation=%d test=%d", len(X_tr), len(X_va), len(X_te))

    # --- 1-3. train candidates, pick champion --------------------------------
    candidates = candidate_models(settings.random_seed)
    comparison, fitted = [], {}
    for name, model in candidates.items():
        model.fit(X_tr, y_tr)
        fitted[name] = model
        auc_va = roc_auc_score(y_va, model.predict_proba(X_va)[:, 1])
        auc_te = roc_auc_score(y_te, model.predict_proba(X_te)[:, 1])
        comparison.append({"model": name, "validation_auc": round(auc_va, 4), "test_auc": round(auc_te, 4)})
        log.info("  %-20s validation AUC=%.4f", name, auc_va)
    comp = pd.DataFrame(comparison).sort_values("validation_auc", ascending=False)
    best = comp.iloc[0]
    lr_auc = comp.set_index("model").at["logistic_regression", "validation_auc"]
    champion = (
        "logistic_regression" if best["validation_auc"] - lr_auc <= SIMPLICITY_MARGIN else best["model"]
    )
    model = fitted[champion]
    if hasattr(model[-1], "n_jobs"):
        model[-1].n_jobs = 1  # one request at a time in the API: avoid thread start-up cost per call
    log.info("Champion: %s", champion)

    # --- 4. calibrate on validation -----------------------------------------
    raw_va = model.predict_proba(X_va)[:, 1]
    calibrator = PlattCalibrator().fit(raw_va, y_va)
    score_va = 1 - calibrator.transform(raw_va)

    # --- 5. thresholds from risk appetite -----------------------------------
    sim_va = ev.threshold_simulation(y_va, score_va, valid_df["requested_amount"].to_numpy())
    approve_t = ev.choose_approve_threshold(sim_va, TARGET_BAD_RATE_APPROVED, floor=DECLINE_BELOW_SCORE)
    thresholds = {"approve_threshold": approve_t, "decline_threshold": DECLINE_BELOW_SCORE}
    log.info("Thresholds: approve >= %.2f, decline < %.2f", approve_t, DECLINE_BELOW_SCORE)

    # --- 6. out-of-time test --------------------------------------------------
    raw_te = model.predict_proba(X_te)[:, 1]
    score_te = 1 - calibrator.transform(raw_te)
    test_metrics = ev.classification_metrics(y_te, score_te, approve_t)
    valid_metrics = ev.classification_metrics(y_va, score_va, approve_t)
    train_metrics = ev.classification_metrics(
        y_tr, 1 - calibrator.transform(model.predict_proba(X_tr)[:, 1]), approve_t
    )
    log.info(
        "Test: AUC=%.3f recall=%.3f precision=%.3f approval=%.1f%% bad-rate-approved=%.1f%%",
        test_metrics["roc_auc"],
        test_metrics["recall"],
        test_metrics["precision"],
        100 * test_metrics["auto_approval_rate"],
        100 * (test_metrics["bad_rate_among_approved"] or 0),
    )

    # --- 7. save bundle ------------------------------------------------------
    reference = {f: float(X_tr[f].median()) for f in MODEL_FEATURES}
    bundle = {
        "model_version": settings.model_version,
        "model_type": champion,
        "trained_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "features": MODEL_FEATURES,
        "feature_descriptions": FEATURE_DESCRIPTIONS,
        "model": model,
        "calibrator": calibrator,
        "calibration_params": calibrator.params,
        "thresholds": thresholds,
        "reference_values": reference,
        "training_rows": int(len(X_tr)),
        "training_window": {
            "train": [
                str(train_df["application_date"].min().date()),
                str(train_df["application_date"].max().date()),
            ],
            "validation": [
                str(valid_df["application_date"].min().date()),
                str(valid_df["application_date"].max().date()),
            ],
            "test": [
                str(test_df["application_date"].min().date()),
                str(test_df["application_date"].max().date()),
            ],
        },
        "metrics_summary": {
            k: test_metrics[k]
            for k in (
                "roc_auc",
                "precision",
                "recall",
                "f1",
                "brier_score",
                "auto_approval_rate",
                "bad_rate_among_approved",
            )
        },
    }
    model_path = paths.model_file()
    joblib.dump(bundle, model_path)
    paths.latest_model_pointer.write_text(
        json.dumps(
            {
                "model_version": settings.model_version,
                "model_file": model_path.name,
                "promoted_at": bundle["trained_at"],
            },
            indent=2,
        )
    )
    log.info("Saved model bundle -> %s", model_path.relative_to(paths.s.artifacts_dir.parent))

    # --- evaluation outputs --------------------------------------------------
    scorer = CreditScorer(bundle)
    labelled = feats[feats["split"].isin(["train", "validation", "test"])].copy()
    labelled["score"] = scorer.predict_score(labelled[MODEL_FEATURES].astype(float))
    monthly = ev.performance_by_month(labelled, approve_t)
    monthly.to_csv(paths.s.reports_dir / "metrics" / "monthly_model_performance.csv", index=False)

    # Baseline scores: what the model would have said in the 9 months before launch.
    recent = feats[feats["application_date"] >= pd.Timestamp("2025-04-01")].copy()
    scored = scorer.score_frame(recent, explain=False)
    baseline = pd.concat(
        [
            recent[
                ["application_id", "borrower_id", "partner_id", "application_month", "requested_amount"]
            ].reset_index(drop=True),
            scored[["score", "decision", "recommended_limit"]],
        ],
        axis=1,
    )
    baseline.to_parquet(paths.baseline_scores, index=False)
    db.write_table(baseline, "baseline_scores")

    sim_va.to_csv(paths.threshold_simulation, index=False)
    ev.plot_roc({n: (y_te, fitted[n].predict_proba(X_te)[:, 1]) for n in fitted}, paths)
    ev.plot_calibration(y_te, raw_te, 1 - score_te, paths)
    ev.plot_threshold_simulation(sim_va, approve_t, paths)
    ev.plot_score_distribution(pd.DataFrame({"score": score_te, "default_flag": y_te}), thresholds, paths)
    ev.plot_confusion(test_metrics["confusion_matrix"], paths)

    metrics = {
        "model_version": settings.model_version,
        "champion": champion,
        "model_comparison": comp.to_dict("records"),
        "thresholds": thresholds,
        "risk_appetite": {
            "target_bad_rate_among_approved": TARGET_BAD_RATE_APPROVED,
            "decline_below_score": DECLINE_BELOW_SCORE,
        },
        "calibration": calibrator.params,
        "train": train_metrics,
        "validation": valid_metrics,
        "test": test_metrics,
        "manual_underwriter_test": ev.classification_metrics(
            y_te, test_df["approval_flag"].astype(float).to_numpy(), 0.5
        ),
        "training_window": bundle["training_window"],
    }
    paths.training_metrics.write_text(json.dumps(metrics, indent=2, default=str))
    _write_model_card(metrics, monthly, paths)
    return metrics


def _write_model_card(m: dict, monthly: pd.DataFrame, paths: Paths) -> None:
    t, mu = m["test"], m["manual_underwriter_test"]
    lines = [
        f"# Model Card - credit risk model {m['model_version']}",
        "",
        "## What it does",
        "Estimates the probability that a clinic or pharmacy repays a stock-finance loan, using only",
        "behaviour known on the application date (procurement, stock movement, repayment history, partner).",
        "",
        "## Candidates compared (ROC-AUC)",
        "",
        "| Model | Validation AUC | Test AUC |",
        "|---|---:|---:|",
        *[
            f"| {r['model']} | {r['validation_auc']:.3f} | {r['test_auc']:.3f} |"
            for r in m["model_comparison"]
        ],
        "",
        f"**Champion: `{m['champion']}`** (simpler model preferred unless another is better by more than "
        f"{SIMPLICITY_MARGIN} AUC).",
        "",
        "## Decision thresholds",
        f"- Approve when score >= **{m['thresholds']['approve_threshold']:.2f}** "
        f"(chosen so at most {TARGET_BAD_RATE_APPROVED:.0%} of auto-approved loans default on the validation set)",
        f"- Decline when score < **{m['thresholds']['decline_threshold']:.2f}** (default risk above 30%)",
        "- Everything in between goes to manual review.",
        "",
        "## Out-of-time test results (applications Apr-Jun 2025)",
        "",
        "| Metric | Model | Manual underwriter |",
        "|---|---:|---:|",
        f"| Auto-approval rate | {t['auto_approval_rate']:.1%} | {mu['auto_approval_rate']:.1%} |",
        f"| Bad rate among approved | {t['bad_rate_among_approved']:.1%} | {mu['bad_rate_among_approved']:.1%} |",
        f"| Recall (defaults flagged) | {t['recall']:.1%} | {mu['recall']:.1%} |",
        f"| Precision | {t['precision']:.1%} | {mu['precision']:.1%} |",
        f"| F1 | {t['f1']:.3f} | {mu['f1']:.3f} |",
        f"| ROC-AUC | {t['roc_auc']:.3f} | - |",
        f"| Brier score | {t['brier_score']:.4f} | - |",
        f"| Expected calibration error | {t['expected_calibration_error']:.4f} | - |",
        "",
        "Confusion matrix (test): "
        f"TN={t['confusion_matrix']['true_negative']}, FP={t['confusion_matrix']['false_positive']}, "
        f"FN={t['confusion_matrix']['false_negative']}, TP={t['confusion_matrix']['true_positive']}",
        "",
        "## Behaviour across time",
        "",
        "| Month | Split | Apps | Actual default | Predicted default | Avg score | Auto-approve | AUC |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
        *[
            f"| {r.month} | {r.split} | {r.applications} | {r.actual_default_rate:.1%} | "
            f"{r.predicted_default_rate:.1%} | {r.average_score:.3f} | {r.auto_approval_rate:.1%} | "
            f"{'-' if pd.isna(r.roc_auc) else f'{r.roc_auc:.3f}'} |"
            for r in monthly.itertuples()
        ],
        "",
        "## Limitations",
        "- Trained on synthetic pilot data where every application was funded (no reject-inference needed).",
        "- Small sample: individual months have few defaults, so monthly AUC is noisy.",
        "- Thresholds encode a risk appetite and must be re-approved if the business changes it.",
        "",
        "Figures: `reports/figures/roc_curve.png`, `calibration_curve.png`, `threshold_simulation.png`,",
        "`score_distribution.png`, `confusion_matrix.png`.",
    ]
    paths.business_report("model_card").write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    train()
