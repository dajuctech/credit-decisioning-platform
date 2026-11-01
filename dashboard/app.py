"""Internal analyst dashboard (stretch goal).

Run:  uv run streamlit run dashboard/app.py
The "Score an application" tab calls the live API (API_URL, default
http://localhost:8000). Every other tab reads the files and tables the
pipeline and API produce, so it shows the same numbers as the reports.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx
import pandas as pd
import plotly.express as px
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.config import get_settings  # noqa: E402
from src.utils import db  # noqa: E402
from src.utils.paths import Paths  # noqa: E402

st.set_page_config(page_title="Credit Decisioning Platform", layout="wide")
settings = get_settings()
paths = Paths(settings)
LAYER_COLORS = {
    "Data Engineering": "#3b6ea8",
    "AI Engineering": "#7a4ea3",
    "Backend Engineering": "#2f8f6a",
    "Cloud Engineering": "#c07a2c",
}
STATUS_COLORS = {"ok": "#4c9f70", "warn": "#e0a030", "alert": "#c8453b"}


@st.cache_data(ttl=30)
def load_json(path: str) -> dict:
    p = Path(path)
    return json.loads(p.read_text()) if p.exists() else {}


@st.cache_data(ttl=30)
def load_csv(path: str) -> pd.DataFrame:
    p = Path(path)
    return pd.read_csv(p) if p.exists() else pd.DataFrame()


@st.cache_data(ttl=15)
def query(sql: str) -> pd.DataFrame:
    try:
        return db.read_sql(sql)
    except Exception:  # noqa: BLE001 - table may not exist before the first pipeline run
        return pd.DataFrame()


def api_health() -> dict | None:
    try:
        return httpx.get(f"{settings.api_url}/health", timeout=2).json()
    except Exception:  # noqa: BLE001
        return None


metrics = load_json(str(paths.training_metrics))
checks = load_csv(str(paths.monitoring_checks))
health = api_health()

st.title("Credit Decisioning Platform")
st.caption(
    "Clinic and pharmacy stock-finance lending. Data Engineering -> AI Engineering -> Backend API -> "
    "Cloud packaging, with monitoring closing the loop."
)

if not metrics:
    st.warning(
        "No trained model found yet. Run `make pipeline` (or `uv run python -m src.run_pipeline`) first."
    )
    st.stop()

tabs = st.tabs(["Overview", "Score an application", "Data quality", "Model", "Monitoring", "Decisions log"])

# ------------------------------------------------------------------ overview
with tabs[0]:
    live = query("SELECT decision, score FROM model_scores")
    c = st.columns(5)
    c[0].metric("API status", (health or {}).get("status", "offline").upper())
    c[1].metric("Model", f"{metrics['model_version']} ({metrics['champion'].replace('_', ' ')})")
    c[2].metric("Test ROC-AUC", f"{metrics['test']['roc_auc']:.3f}")
    c[3].metric(
        "Live approval rate",
        f"{(live['decision'] == 'approve').mean():.1%}" if len(live) else "-",
        delta=(
            f"{((live['decision'] == 'approve').mean() - metrics['test']['auto_approval_rate']) * 100:+.1f} pp vs test"
            if len(live)
            else None
        ),
    )
    c[4].metric("Open alerts", int((checks["status"] == "alert").sum()) if len(checks) else 0)

    st.subheader("How the layers connect")
    st.markdown(
        """
| Step | Layer | What happens | Where to look |
|---|---|---|---|
| 1 | **Data Engineering** | raw files validated, cleaned, joined; point-in-time features built | *Data quality* tab |
| 2 | **AI Engineering** | model trained, calibrated, thresholds set from risk appetite | *Model* tab |
| 3 | **Backend Engineering** | FastAPI serves score + decision + reason codes, logs every call | *Score an application* |
| 4 | **Cloud Engineering** | Docker image, health checks, env config, CI | `/health`, `docker compose` |
| 5 | **Monitoring** | drift, score, approval, error and latency checks routed to an owner | *Monitoring* tab |
"""
    )
    if len(checks):
        st.subheader("Monitoring status by owning layer")
        summary = checks.groupby(["owner_layer", "status"]).size().reset_index(name="checks")
        fig = px.bar(
            summary,
            x="owner_layer",
            y="checks",
            color="status",
            color_discrete_map=STATUS_COLORS,
            category_orders={"status": ["alert", "warn", "ok"]},
        )
        fig.update_layout(height=320, xaxis_title=None, legend_title=None)
        st.plotly_chart(fig, width="stretch")

# ------------------------------------------------------------------ scoring
with tabs[1]:
    st.write(
        f"Sends the application to **{settings.api_url}/score**. Untick *Send the behaviour signals* to let "
        "the API look the borrower up in its feature store instead."
    )
    if not health:
        st.error(
            "The API is not reachable. Start it with `make api` or `docker compose up`, then refresh this page."
        )
    with st.form("score"):
        a, b, c3 = st.columns(3)
        application_id = a.text_input("Application ID", "APP-DEMO-001")
        borrower_id = b.text_input("Borrower ID", "BOR-0301")
        requested = c3.number_input("Requested amount", min_value=1000.0, value=25000.0, step=500.0)
        st.markdown("**Behaviour signals** (optional)")
        f1, f2, f3, f4 = st.columns(4)
        proc = f1.number_input("Procurement, last 90 days", min_value=0.0, value=180000.0, step=5000.0)
        late = f2.number_input("Late payments, last 90 days", min_value=0, value=1, step=1)
        rate = f3.slider("Repayment rate, last 180 days", 0.0, 1.0, 0.91)
        turnover = f4.number_input("Stock turnover ratio", min_value=0.0, value=1.3, step=0.1)
        use_signals = st.checkbox("Send the behaviour signals above", value=True)
        submitted = st.form_submit_button("Score application", type="primary")
    if submitted:
        payload = {
            "application_id": application_id,
            "borrower_id": borrower_id,
            "requested_amount": requested,
        }
        if use_signals:
            payload |= {
                "recent_procurement_value": proc,
                "late_payment_count_90d": late,
                "repayment_rate_180d": rate,
                "stock_turnover_ratio": turnover,
            }
        try:
            r = httpx.post(f"{settings.api_url}/score", json=payload, timeout=10)
            body = r.json()
        except Exception as e:  # noqa: BLE001
            st.error(f"Could not reach the API: {e}")
            body, r = None, None
        if r is not None and r.status_code != 200:
            st.error(f"API returned {r.status_code}")
            st.json(body)
        elif body:
            color = {"approve": "green", "manual_review": "orange", "decline": "red"}[body["decision"]]
            st.markdown(f"### Decision: :{color}[{body['decision'].replace('_', ' ').upper()}]")
            k = st.columns(4)
            k[0].metric("Score (repayment probability)", f"{body['score']:.3f}")
            k[1].metric("Recommended limit", f"{body['recommended_limit']:,.0f}")
            k[2].metric("Tenor (months)", body["recommended_tenor_months"] or "-")
            k[3].metric("Risk band", body["risk_band"])
            st.markdown("**Reason codes:** " + "; ".join(body["reason_codes"]))
            details = pd.DataFrame(body["reason_details"])
            if len(details):
                fig = px.bar(
                    details.sort_values("impact"),
                    x="impact",
                    y="feature",
                    orientation="h",
                    color="direction",
                    color_discrete_map={"supports approval": "#4c9f70", "increases risk": "#c8453b"},
                )
                fig.update_layout(height=300, legend_title=None, yaxis_title=None)
                st.plotly_chart(fig, width="stretch")
            if body["features_from_store"]:
                st.caption("Filled from feature store: " + ", ".join(body["features_from_store"]))
            with st.expander("Raw API response"):
                st.json(body)

# ------------------------------------------------------------------ data quality
with tabs[2]:
    report = load_json(str(paths.validation_report_json))
    if report:
        k = st.columns(3)
        k[0].metric("Raw rows checked", f"{report['total_rows_in']:,}")
        k[1].metric("Rows removed", f"{report['total_rows_removed']:,}")
        k[2].metric("Removed share", f"{report['removed_share']:.2%}")
        st.subheader("Per table")
        st.dataframe(pd.DataFrame(report["tables"]).T, width="stretch")
        st.subheader("Issues by rule")
        rules = pd.DataFrame(report["rule_counts"])
        st.plotly_chart(
            px.bar(rules, x="count", y="rule", color="table", orientation="h").update_layout(height=380),
            width="stretch",
        )
        st.subheader("Sample of rejected or fixed records")
        st.dataframe(load_csv(str(paths.rejected_records)).groupby("rule").head(5), width="stretch")

# ------------------------------------------------------------------ model
with tabs[3]:
    t = metrics["test"]
    k = st.columns(5)
    k[0].metric("ROC-AUC", f"{t['roc_auc']:.3f}")
    k[1].metric("Recall (defaults flagged)", f"{t['recall']:.1%}")
    k[2].metric("Precision", f"{t['precision']:.1%}")
    k[3].metric("Auto-approval", f"{t['auto_approval_rate']:.1%}")
    k[4].metric("Bad rate among approved", f"{t['bad_rate_among_approved']:.1%}")
    st.caption(
        f"Approve if score >= {metrics['thresholds']['approve_threshold']:.2f}; "
        f"decline if score < {metrics['thresholds']['decline_threshold']:.2f}; otherwise manual review."
    )
    left, right = st.columns(2)
    left.subheader("Candidate models")
    left.dataframe(pd.DataFrame(metrics["model_comparison"]), width="stretch", hide_index=True)
    mu = metrics["manual_underwriter_test"]
    left.subheader("Model vs manual underwriter (test set)")
    left.dataframe(
        pd.DataFrame(
            {
                "model": [t["auto_approval_rate"], t["bad_rate_among_approved"], t["recall"]],
                "manual underwriter": [mu["auto_approval_rate"], mu["bad_rate_among_approved"], mu["recall"]],
            },
            index=["approval rate", "bad rate among approved", "defaults flagged"],
        ).style.format("{:.1%}"),
        width="stretch",
    )
    sim = load_csv(str(paths.threshold_simulation))
    if len(sim):
        right.subheader("What if we moved the threshold?")
        fig = px.line(
            sim.melt(
                id_vars="threshold",
                value_vars=["approval_rate", "bad_rate_among_approved", "defaults_caught_share"],
            ),
            x="threshold",
            y="value",
            color="variable",
        )
        fig.add_vline(x=metrics["thresholds"]["approve_threshold"], line_dash="dash")
        fig.update_layout(height=380, legend_title=None, yaxis_title="rate")
        right.plotly_chart(fig, width="stretch")
    figs = st.columns(3)
    for col, name in zip(figs, ["roc_curve", "calibration_curve", "confusion_matrix"], strict=True):
        if paths.figure(name).exists():
            col.image(str(paths.figure(name)), width="stretch")

# ------------------------------------------------------------------ monitoring
with tabs[4]:
    if checks.empty:
        st.info("No monitoring snapshot yet. Run `make monitor`.")
    else:
        st.caption(f"Snapshot {checks['snapshot_id'].iat[0]} at {checks['snapshot_at'].iat[0]}")
        f1, f2 = st.columns(2)
        layers = f1.multiselect("Owning layer", list(LAYER_COLORS), default=list(LAYER_COLORS))
        statuses = f2.multiselect("Status", ["alert", "warn", "ok"], default=["alert", "warn"])
        view = checks[checks["owner_layer"].isin(layers) & checks["status"].isin(statuses)]
        st.dataframe(
            view[["status", "owner_layer", "check_name", "subject", "explanation", "action"]],
            width="stretch",
            hide_index=True,
        )
        drift = load_csv(str(paths.drift_table))
        left, right = st.columns(2)
        if len(drift):
            fig = px.bar(
                drift,
                x="drift_score",
                y="feature",
                orientation="h",
                color="drift_status",
                color_discrete_map=STATUS_COLORS,
                title="Feature drift vs pre-launch window",
            )
            fig.add_vline(x=0.10, line_dash="dot")
            fig.add_vline(x=0.25, line_dash="dash")
            fig.update_layout(height=450, yaxis={"autorange": "reversed"}, legend_title=None)
            left.plotly_chart(fig, width="stretch")
        req = query("SELECT path, status_code, latency_ms FROM api_requests WHERE path LIKE '%score%'")
        if len(req):
            fig = px.histogram(req, x="latency_ms", color="path", nbins=40, title="API latency (ms)")
            fig.update_layout(height=450, legend_title=None)
            right.plotly_chart(fig, width="stretch")
        for name in ("approval_trend", "approval_by_partner"):
            if paths.figure(name).exists():
                st.image(str(paths.figure(name)))

# ------------------------------------------------------------------ log
with tabs[5]:
    log_df = query(
        "SELECT scored_at, request_id, application_id, borrower_id, partner_id, score, decision, "
        "recommended_limit, recommended_tenor_months, reason_codes, source FROM model_scores "
        "ORDER BY rowid DESC LIMIT 500"
    )
    if log_df.empty:
        st.info("No decisions logged yet.")
    else:
        decision = st.selectbox("Decision", ["all", "approve", "manual_review", "decline"])
        if decision != "all":
            log_df = log_df[log_df["decision"] == decision]
        log_df["reason_codes"] = log_df["reason_codes"].map(lambda s: "; ".join(json.loads(s)))
        st.dataframe(log_df, width="stretch", hide_index=True)
