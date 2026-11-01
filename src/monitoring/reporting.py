"""Business reports (PRD 14.9).

Turns scoring logs and monitoring checks into short Markdown reports with
charts that a risk analyst or product manager can read without code:

  reports/business/scoring_summary.md
  reports/business/drift_report.md
  reports/business/approval_trend_report.md
  reports/business/high_risk_applications.md
  reports/business/monthly_model_performance.md
  reports/business/monitoring_summary.md   <- the feedback loop, grouped by owning layer

Run:  uv run python -m src.monitoring.reporting
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
import seaborn as sns

from src.monitoring.score_checks import AI, BACKEND, CLOUD, DATA, load_live_scores
from src.utils import db
from src.utils.logger import get_logger
from src.utils.paths import Paths

log = get_logger(__name__)
sns.set_theme(style="whitegrid")
STATUS_ICON = {"ok": "OK", "warn": "WARN", "alert": "ALERT"}
LAYER_ORDER = [DATA, AI, BACKEND, CLOUD]


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")


def _pct(x: float) -> str:
    return f"{x:.1%}"


def _table(df: pd.DataFrame) -> list[str]:
    cols = list(df.columns)
    out = ["| " + " | ".join(cols) + " |", "|" + "|".join("---" for _ in cols) + "|"]
    for row in df.itertuples(index=False):
        out.append("| " + " | ".join("" if pd.isna(v) else str(v) for v in row) + " |")
    return out


def _write(paths: Paths, name: str, title: str, lines: list[str]) -> None:
    body = [f"# {title}", "", f"_Generated {_now()} by `src/monitoring/reporting.py`_", "", *lines]
    paths.business_report(name).write_text("\n".join(body) + "\n")


def _live_with_months(paths: Paths) -> pd.DataFrame:
    live = load_live_scores()
    months = {}
    if paths.incoming_applications.exists():
        inc = pd.read_csv(paths.incoming_applications, usecols=["application_id", "application_date"])
        months = dict(zip(inc["application_id"], inc["application_date"].str[:7], strict=True))
    live["application_month"] = live["application_id"].map(months).fillna(live["scored_at"].str[:7])
    live["reason_list"] = live["reason_codes"].map(json.loads)
    return live


# --------------------------------------------------------------------- reports
def scoring_summary(live: pd.DataFrame, paths: Paths) -> None:
    counts = live["decision"].value_counts()
    total = len(live)
    by_partner = (
        live.groupby("partner_id")
        .agg(
            applications=("score", "size"),
            avg_score=("score", "mean"),
            approval_rate=("decision", lambda d: (d == "approve").mean()),
            avg_limit=("recommended_limit", "mean"),
        )
        .reset_index()
    )
    by_partner["avg_score"] = by_partner["avg_score"].round(3)
    by_partner["approval_rate"] = by_partner["approval_rate"].map(_pct)
    by_partner["avg_limit"] = by_partner["avg_limit"].round(0).astype(int).map("{:,}".format)
    reasons = live.explode("reason_list")["reason_list"].value_counts().head(8)

    fig, ax = plt.subplots(figsize=(7, 4))
    base = pd.read_parquet(paths.baseline_scores)
    sns.kdeplot(base["score"], ax=ax, label="before launch (Apr-Dec 2025)", fill=True, alpha=0.3, clip=(0, 1))
    sns.kdeplot(
        live["score"], ax=ax, label="live API traffic (Jan-Mar 2026)", fill=True, alpha=0.3, clip=(0, 1)
    )
    ax.set(title="Score distribution: before vs after launch", xlabel="score (probability of repayment)")
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(paths.figure("live_score_distribution"), dpi=120)
    plt.close(fig)

    lines = [
        "Who was scored through the API, and what did the platform decide?",
        "",
        f"- Applications scored: **{total:,}**",
        f"- Approved: **{counts.get('approve', 0):,}** ({_pct(counts.get('approve', 0) / total)})",
        f"- Manual review: **{counts.get('manual_review', 0):,}** ({_pct(counts.get('manual_review', 0) / total)})",
        f"- Declined: **{counts.get('decline', 0):,}** ({_pct(counts.get('decline', 0) / total)})",
        f"- Average score: **{live['score'].mean():.3f}**",
        f"- Total recommended credit (approved): **{live.loc[live['decision'] == 'approve', 'recommended_limit'].sum():,.0f}**",
        "",
        "## By partner",
        "",
        *_table(by_partner),
        "",
        "## Most common reason codes",
        "",
        *_table(reasons.rename_axis("reason").reset_index(name="applications")),
        "",
        "![score distribution](../figures/live_score_distribution.png)",
    ]
    _write(paths, "scoring_summary", "Scoring Summary Report", lines)


def drift_report(paths: Paths, checks: pd.DataFrame) -> None:
    drift = pd.read_csv(paths.drift_table)
    colors = drift["drift_status"].map({"ok": "#4c9f70", "warn": "#e0a030", "alert": "#c8453b"})
    fig, ax = plt.subplots(figsize=(7, 5))
    ax.barh(drift["feature"], drift["drift_score"], color=colors)
    ax.axvline(0.10, color="grey", ls="--", lw=1)
    ax.axvline(0.25, color="black", ls="--", lw=1)
    ax.invert_yaxis()
    ax.set(title="Feature drift vs pre-launch window (PSI)", xlabel="PSI (dashed: 0.10 warn, 0.25 alert)")
    fig.tight_layout()
    fig.savefig(paths.figure("feature_drift_psi"), dpi=120)
    plt.close(fig)

    table = drift[
        [
            "feature",
            "method",
            "drift_score",
            "drift_status",
            "psi_vs_training",
            "ks_statistic",
            "baseline_median",
            "current_median",
            "baseline_missing_rate",
            "current_missing_rate",
        ]
    ].copy()
    table["drift_status"] = table["drift_status"].map(STATUS_ICON)
    for c in ("baseline_missing_rate", "current_missing_rate"):
        table[c] = table[c].map(_pct)
    seg = checks[checks["check_name"] == "segment_drift"][["subject", "status", "explanation"]].copy()
    seg["status"] = seg["status"].map(STATUS_ICON)
    lines = [
        "Has the data arriving at the API changed compared with the 9 months before launch?",
        "",
        "How to read this: **PSI** below 0.10 means stable, 0.10-0.25 a moderate shift, above 0.25 a major",
        "shift. **KS** is the largest gap between the two cumulative distributions (0 = identical).",
        "`psi_vs_training` compares with the original 2024 training months, for information only.",
        "",
        "Owner: **Data Engineering** (is the feed correct?), then **AI Engineering** (does the model still fit?).",
        "",
        *_table(table),
        "",
        "## Drift inside regions",
        "",
        *_table(seg),
        "",
        "![feature drift](../figures/feature_drift_psi.png)",
    ]
    _write(paths, "drift_report", "Drift Report", lines)


def approval_trend(live: pd.DataFrame, paths: Paths) -> None:
    base = pd.read_parquet(paths.baseline_scores)
    base["period"], live = "before launch", live.assign(period="live")
    both = pd.concat(
        [
            base[["application_month", "partner_id", "decision", "period"]],
            live[["application_month", "partner_id", "decision", "period"]],
        ]
    )
    monthly = (
        both.groupby(["application_month", "period"])
        .agg(
            applications=("decision", "size"),
            approval_rate=("decision", lambda d: (d == "approve").mean()),
            manual_review_rate=("decision", lambda d: (d == "manual_review").mean()),
            decline_rate=("decision", lambda d: (d == "decline").mean()),
        )
        .reset_index()
    )
    partner = both.pivot_table(
        index="partner_id", columns="period", values="decision", aggfunc=lambda d: (d == "approve").mean()
    )
    partner = partner.reindex(columns=["before launch", "live"])
    partner["change_pp"] = ((partner["live"] - partner["before launch"]) * 100).round(1)

    fig, ax = plt.subplots(figsize=(8, 4))
    for col in ("approval_rate", "manual_review_rate", "decline_rate"):
        ax.plot(monthly["application_month"], monthly[col], marker="o", label=col.replace("_", " "))
    ax.axvline(len(monthly[monthly["period"] == "before launch"]) - 0.5, color="black", ls="--", lw=1)
    ax.text(len(monthly[monthly["period"] == "before launch"]) - 0.4, 0.95, "launch", fontsize=8)
    ax.set(title="Decision mix by month", ylabel="share of applications", ylim=(0, 1))
    ax.tick_params(axis="x", rotation=45)
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(paths.figure("approval_trend"), dpi=120)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(7, 4))
    partner[["before launch", "live"]].plot.bar(ax=ax)
    ax.set(title="Approval rate by partner", ylabel="approval rate", xlabel="partner", ylim=(0, 1))
    fig.tight_layout()
    fig.savefig(paths.figure("approval_by_partner"), dpi=120)
    plt.close(fig)

    m = monthly.copy()
    for c in ("approval_rate", "manual_review_rate", "decline_rate"):
        m[c] = m[c].map(_pct)
    p = partner.reset_index()
    for c in ("before launch", "live"):
        p[c] = p[c].map(lambda v: "-" if pd.isna(v) else _pct(v))
    lines = [
        "Are approval rates shifting over time, or for a particular partner?",
        "",
        "Before-launch months are the model's decisions on historical applications (what it *would* have",
        "decided). Live months are real API decisions.",
        "",
        "## By month",
        "",
        *_table(m),
        "",
        "## By partner",
        "",
        *_table(p),
        "",
        "![approval trend](../figures/approval_trend.png)",
        "",
        "![approval by partner](../figures/approval_by_partner.png)",
    ]
    _write(paths, "approval_trend_report", "Approval Trend Report", lines)


def high_risk(live: pd.DataFrame, paths: Paths) -> None:
    risky = live[live["decision"] != "approve"].sort_values("score").copy()
    top = risky.head(25)[
        ["application_id", "borrower_id", "partner_id", "score", "decision", "reason_list"]
    ].copy()
    top["score"] = top["score"].round(3)
    top["reasons"] = top.pop("reason_list").map("; ".join)
    review = live[live["decision"] == "manual_review"]
    review_reasons = review.explode("reason_list")["reason_list"].value_counts().head(6)
    data_gaps = int(review["reason_list"].map(lambda rs: any(r.startswith("limited data") for r in rs)).sum())
    lines = [
        "Which applications need a human decision, and why?",
        "",
        f"- Declined: **{(live['decision'] == 'decline').sum()}**",
        f"- In the manual review queue: **{len(review)}**, of which **{data_gaps}** are there partly because "
        "key data was missing (a data-feed problem, not necessarily a risky borrower)",
        "",
        "## Why cases go to manual review",
        "",
        *_table(review_reasons.rename_axis("reason").reset_index(name="cases")),
        "",
        "## 25 highest-risk applications",
        "",
        *_table(top),
    ]
    _write(paths, "high_risk_applications", "High-Risk Application Report", lines)


def monthly_performance(live: pd.DataFrame, paths: Paths) -> None:
    hist = pd.read_csv(paths.s.reports_dir / "metrics" / "monthly_model_performance.csv")
    metrics = json.loads(paths.training_metrics.read_text())
    live_m = (
        live.groupby("application_month")
        .agg(
            applications=("score", "size"),
            average_score=("score", "mean"),
            approval_rate=("decision", lambda d: (d == "approve").mean()),
        )
        .reset_index()
    )
    live_m["predicted_default_rate"] = (1 - live_m["average_score"]).map(_pct)
    live_m["average_score"] = live_m["average_score"].round(3)
    live_m["approval_rate"] = live_m["approval_rate"].map(_pct)
    live_m["actual_default_rate"] = "not yet known"

    fig, ax = plt.subplots(figsize=(8, 4))
    ax.plot(hist["month"], hist["actual_default_rate"], marker="o", label="actual default rate")
    ax.plot(hist["month"], hist["predicted_default_rate"], marker="o", label="predicted default rate")
    ax.set(title="Predicted vs actual default rate by application month", ylabel="default rate")
    ax.tick_params(axis="x", rotation=45)
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(paths.figure("monthly_model_performance"), dpi=120)
    plt.close(fig)

    h = hist.copy()
    for c in ("actual_default_rate", "predicted_default_rate", "auto_approval_rate"):
        h[c] = h[c].map(_pct)
    t = metrics["test"]
    lines = [
        f"Model `{metrics['model_version']}` ({metrics['champion']}). Out-of-time test: ROC-AUC "
        f"**{t['roc_auc']:.3f}**, auto-approval **{_pct(t['auto_approval_rate'])}**, bad rate among approved "
        f"**{_pct(t['bad_rate_among_approved'])}**.",
        "",
        "## Historical months (outcomes known)",
        "",
        "Predicted default rate tracking the actual rate means the model is well calibrated over time.",
        "",
        *_table(h),
        "",
        "## Live months (outcomes not yet known)",
        "",
        "A loan's outcome is only known after 6 months on book, so live months show predictions only.",
        "",
        *_table(live_m),
        "",
        "![monthly performance](../figures/monthly_model_performance.png)",
    ]
    _write(paths, "monthly_model_performance", "Monthly Model Performance Summary", lines)


def monitoring_summary(checks: pd.DataFrame, paths: Paths) -> None:
    counts = checks["status"].value_counts()
    lines = [
        "This is the feedback loop. Each check belongs to the engineering layer that would fix it.",
        "",
        f"Snapshot `{checks['snapshot_id'].iat[0]}` at {checks['snapshot_at'].iat[0]}: "
        f"**{counts.get('alert', 0)} alerts**, **{counts.get('warn', 0)} warnings**, {counts.get('ok', 0)} OK.",
        "",
        "| Layer | Alerts | Warnings | OK |",
        "|---|---:|---:|---:|",
    ]
    for layer in LAYER_ORDER:
        c = checks[checks["owner_layer"] == layer]["status"].value_counts()
        lines.append(f"| {layer} | {c.get('alert', 0)} | {c.get('warn', 0)} | {c.get('ok', 0)} |")
    for layer in LAYER_ORDER:
        sub = checks[checks["owner_layer"] == layer].copy()
        sub["rank"] = sub["status"].map({"alert": 0, "warn": 1, "ok": 2})
        sub = sub.sort_values(["rank", "check_name"])
        lines += ["", f"## {layer}", ""]
        for r in sub.itertuples():
            lines.append(f"- **{STATUS_ICON[r.status]}** `{r.check_name}` - {r.subject}: {r.explanation}")
            if r.status != "ok":
                lines.append(f"  - Action: {r.action}")
    _write(paths, "monitoring_summary", "Monitoring Summary - Feedback Loop", lines)


def latency_figure(paths: Paths) -> None:
    req = db.read_sql("SELECT path, status_code, latency_ms FROM api_requests WHERE path LIKE '%score%'")
    if req.empty:
        return
    fig, ax = plt.subplots(figsize=(7, 4))
    sns.histplot(data=req, x="latency_ms", hue="path", bins=40, ax=ax)
    ax.set(title="API latency (scoring endpoints)", xlabel="latency (ms)")
    fig.tight_layout()
    fig.savefig(paths.figure("api_latency"), dpi=120)
    plt.close(fig)


def run(paths: Paths | None = None) -> None:
    paths = paths or Paths()
    live = _live_with_months(paths)
    if live.empty:
        raise RuntimeError("No scoring traffic found. Run the simulate stage first.")
    checks = pd.read_csv(paths.monitoring_checks)
    scoring_summary(live, paths)
    drift_report(paths, checks)
    approval_trend(live, paths)
    high_risk(live, paths)
    monthly_performance(live, paths)
    monitoring_summary(checks, paths)
    latency_figure(paths)
    log.info("Reports written to %s", paths.s.reports_dir / "business")


if __name__ == "__main__":
    run()
