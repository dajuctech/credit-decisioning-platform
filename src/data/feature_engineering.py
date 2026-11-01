"""Feature engineering (Data Engineering -> AI Engineering hand-off).

Turns clean tables into one row of model inputs per application.

The golden rule here is *point-in-time correctness*: a feature for an
application dated D may only use events that happened before D. If we let
future repayments leak into training features, the model would look great in
testing and fail in production.

Outputs:
  data/processed/application_features.parquet           historical applications (+ label, split)
  data/processed/borrower_feature_store.parquet         latest features per borrower (used by the API)
  data/processed/incoming_application_features.parquet  features for post-launch applications
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from src.utils import db
from src.utils.logger import get_logger
from src.utils.paths import Paths

log = get_logger(__name__)

# Features the model uses. The order here is the order the model expects.
MODEL_FEATURES = [
    "requested_amount",
    "recent_procurement_value",
    "procurement_frequency_90d",
    "avg_order_value_90d",
    "procurement_trend_ratio",
    "days_since_last_procurement",
    "active_months_12m",
    "late_payment_count_90d",
    "repayment_rate_180d",
    "prior_loan_count",
    "stock_turnover_ratio",
    "stock_trend_ratio",
    "loan_to_procurement_ratio",
    "partner_late_rate_180d",
    "years_in_operation",
]

# Features that the API computes itself, never accepts from the caller.
DERIVED_FEATURES = ["loan_to_procurement_ratio"]

FEATURE_DESCRIPTIONS = {
    "requested_amount": "Loan amount requested",
    "recent_procurement_value": "Total stock purchased in the last 90 days",
    "procurement_frequency_90d": "Number of stock orders in the last 90 days",
    "avg_order_value_90d": "Average order size in the last 90 days",
    "procurement_trend_ratio": "Purchases in last 90 days divided by the 90 days before (growth > 1)",
    "days_since_last_procurement": "Days since the most recent stock order (recency)",
    "active_months_12m": "Months with at least one order in the last 12 months (activity level)",
    "late_payment_count_90d": "Instalments paid 7+ days late (or still overdue) in the last 90 days",
    "repayment_rate_180d": "Share of instalment value due in the last 180 days that was repaid",
    "prior_loan_count": "Number of earlier loans (repeat borrowing), capped at 3",
    "stock_turnover_ratio": "Stock sold divided by stock received in the last 90 days",
    "stock_trend_ratio": "Stock sold in last 90 days divided by the 90 days before",
    "loan_to_procurement_ratio": "Requested amount divided by average monthly purchases",
    "partner_late_rate_180d": "Share of late instalments across the partner's borrowers in the last 180 days",
    "years_in_operation": "Years the business has been operating",
}

TRAIN_END = pd.Timestamp("2025-01-01")
VALID_END = pd.Timestamp("2025-04-01")
TEST_END = pd.Timestamp("2025-07-01")
LATE_GRACE_DAYS = 7
PRIOR_LOAN_CAP = 3


def assign_split(dates: pd.Series, labels: pd.Series) -> pd.Series:
    """Time-based split: train on the past, validate and test on later months."""
    split = np.select(
        [dates < TRAIN_END, dates < VALID_END, dates < TEST_END],
        ["train", "validation", "test"],
        default="recent",
    )
    split = pd.Series(split, index=dates.index)
    split[labels.isna() & (split != "recent")] = "unlabelled"
    return split


def _procurement_features(apps: pd.DataFrame, proc: pd.DataFrame) -> pd.DataFrame:
    m = apps[["application_id", "borrower_id", "application_date"]].merge(
        proc[["borrower_id", "txn_date", "procurement_value"]], on="borrower_id", how="inner"
    )
    m = m[
        (m["txn_date"] < m["application_date"])
        & (m["txn_date"] >= m["application_date"] - pd.Timedelta(days=365))
    ]
    age = (m["application_date"] - m["txn_date"]).dt.days
    last90 = m[age <= 90]
    prev90 = m[(age > 90) & (age <= 180)]
    out = pd.DataFrame(index=apps["application_id"])
    out["recent_procurement_value"] = last90.groupby("application_id")["procurement_value"].sum()
    out["procurement_frequency_90d"] = last90.groupby("application_id").size()
    prev = prev90.groupby("application_id")["procurement_value"].sum()
    out[["recent_procurement_value", "procurement_frequency_90d"]] = out[
        ["recent_procurement_value", "procurement_frequency_90d"]
    ].fillna(0)
    out["avg_order_value_90d"] = out["recent_procurement_value"] / out["procurement_frequency_90d"].replace(
        0, np.nan
    )
    out["procurement_trend_ratio"] = (out["recent_procurement_value"] / prev.reindex(out.index)).clip(0, 5)
    last_txn = m.groupby("application_id")["txn_date"].max()
    app_dates = apps.set_index("application_id")["application_date"]
    out["days_since_last_procurement"] = (app_dates - last_txn.reindex(app_dates.index)).dt.days.fillna(365)
    out["active_months_12m"] = (
        m.assign(month=m["txn_date"].dt.to_period("M")).groupby("application_id")["month"].nunique()
    )
    out["active_months_12m"] = out["active_months_12m"].fillna(0)
    return out


def _stock_features(apps: pd.DataFrame, stock: pd.DataFrame) -> pd.DataFrame:
    m = apps[["application_id", "borrower_id", "application_date"]].merge(
        stock[["borrower_id", "movement_date", "movement_type", "stock_value"]], on="borrower_id", how="inner"
    )
    age = (m["application_date"] - m["movement_date"]).dt.days
    m = m[(age >= 1) & (age <= 180)]
    age = age.loc[m.index]
    out = pd.DataFrame(index=apps["application_id"])
    recent, prior = m[age <= 90], m[age > 90]
    inbound = recent[recent["movement_type"] == "inbound"].groupby("application_id")["stock_value"].sum()
    outbound = recent[recent["movement_type"] == "outbound"].groupby("application_id")["stock_value"].sum()
    outbound_prev = prior[prior["movement_type"] == "outbound"].groupby("application_id")["stock_value"].sum()
    out["stock_turnover_ratio"] = (outbound.reindex(out.index) / inbound.reindex(out.index)).clip(0, 3)
    out["stock_trend_ratio"] = (outbound.reindex(out.index) / outbound_prev.reindex(out.index)).clip(0, 5)
    return out


def _repayment_features(apps: pd.DataFrame, reps: pd.DataFrame, all_apps: pd.DataFrame) -> pd.DataFrame:
    m = apps[["application_id", "borrower_id", "application_date"]].merge(
        reps[
            [
                "application_id",
                "borrower_id",
                "repayment_due_date",
                "repayment_date",
                "amount_due",
                "repayment_amount",
            ]
        ],
        on="borrower_id",
        how="inner",
        suffixes=("", "_loan"),
    )
    m = m[
        (m["application_id_loan"] != m["application_id"]) & (m["repayment_due_date"] < m["application_date"])
    ]
    age = (m["application_date"] - m["repayment_due_date"]).dt.days
    # What did we *know* at application time? A payment made after D is unknown at D.
    paid_by_d = m["repayment_date"].notna() & (m["repayment_date"] < m["application_date"])
    days_late_known = (m["repayment_date"] - m["repayment_due_date"]).dt.days
    overdue_at_d = (m["application_date"] - m["repayment_due_date"]).dt.days
    late = (paid_by_d & (days_late_known > LATE_GRACE_DAYS)) | (~paid_by_d & (overdue_at_d > LATE_GRACE_DAYS))
    m = m.assign(late=late.astype(int), repaid=np.where(paid_by_d, m["repayment_amount"], 0.0))

    out = pd.DataFrame(index=apps["application_id"])
    w90, w180 = m[age <= 90], m[age <= 180]
    out["late_payment_count_90d"] = w90.groupby("application_id")["late"].sum()
    out["late_payment_count_90d"] = out["late_payment_count_90d"].fillna(0)
    due180 = w180.groupby("application_id")["amount_due"].sum()
    repaid180 = w180.groupby("application_id")["repaid"].sum()
    out["repayment_rate_180d"] = (repaid180 / due180).clip(0, 1)

    prior = apps[["application_id", "borrower_id", "application_date"]].merge(
        all_apps[["borrower_id", "application_date"]].rename(columns={"application_date": "prior_date"}),
        on="borrower_id",
    )
    prior = prior[prior["prior_date"] < prior["application_date"]]
    out["prior_loan_count"] = prior.groupby("application_id").size()
    # "3 or more": the model never sees more than a few repeat loans in training, so it must not extrapolate.
    out["prior_loan_count"] = out["prior_loan_count"].fillna(0).clip(upper=PRIOR_LOAN_CAP)
    return out


def partner_late_rates(
    reps: pd.DataFrame, borrowers: pd.DataFrame, month_starts: pd.DatetimeIndex
) -> pd.DataFrame:
    """Late-instalment share per partner, measured at the start of each month (no look-ahead)."""
    r = reps.merge(borrowers[["borrower_id", "partner_id"]], on="borrower_id")
    rows = []
    for ms in month_starts:
        w = r[(r["repayment_due_date"] < ms) & (r["repayment_due_date"] >= ms - pd.Timedelta(days=180))]
        if w.empty:
            continue
        paid = w["repayment_date"].notna() & (w["repayment_date"] < ms)
        late = (paid & ((w["repayment_date"] - w["repayment_due_date"]).dt.days > LATE_GRACE_DAYS)) | (
            ~paid & ((ms - w["repayment_due_date"]).dt.days > LATE_GRACE_DAYS)
        )
        rates = late.groupby(w["partner_id"]).mean()
        rows += [{"partner_id": p, "month_start": ms, "partner_late_rate_180d": v} for p, v in rates.items()]
    return pd.DataFrame(rows, columns=["partner_id", "month_start", "partner_late_rate_180d"])


def add_derived_features(df: pd.DataFrame) -> pd.DataFrame:
    """Features computed from other features. Shared by training *and* the API so they always match."""
    df = df.copy()
    monthly_purchases = pd.to_numeric(df["recent_procurement_value"], errors="coerce") / 3
    ratio = pd.to_numeric(df["requested_amount"], errors="coerce") / monthly_purchases.replace(0, np.nan)
    df["loan_to_procurement_ratio"] = ratio.clip(0, 10).fillna(10.0).where(monthly_purchases.notna(), np.nan)
    return df


def build_features(
    apps: pd.DataFrame, tables: dict[str, pd.DataFrame], history_apps: pd.DataFrame | None = None
) -> pd.DataFrame:
    """Build point-in-time features for a set of applications."""
    apps = apps.copy()
    apps["application_date"] = pd.to_datetime(apps["application_date"])
    history_apps = tables["applications"] if history_apps is None else history_apps

    proc = _procurement_features(apps, tables["procurement_transactions"])
    stock = _stock_features(apps, tables["stock_movements"])
    rep = _repayment_features(apps, tables["repayments"], history_apps)
    feats = apps.set_index("application_id").join([proc, stock, rep]).reset_index()

    borrowers = tables["borrowers"][["borrower_id", "years_in_operation", "business_type", "region"]]
    feats = feats.merge(borrowers, on="borrower_id", how="left")

    months = pd.date_range(
        feats["application_date"].min().to_period("M").to_timestamp(),
        feats["application_date"].max(),
        freq="MS",
    )
    plr = partner_late_rates(tables["repayments"], tables["borrowers"], months)
    feats["month_start"] = feats["application_date"].dt.to_period("M").dt.to_timestamp()
    feats = feats.merge(plr, on=["partner_id", "month_start"], how="left").drop(columns="month_start")
    feats = add_derived_features(feats)
    feats["application_month"] = feats["application_date"].dt.strftime("%Y-%m")
    return feats


def build_feature_store(tables: dict[str, pd.DataFrame], as_of: pd.Timestamp) -> pd.DataFrame:
    """Latest known features per borrower, so the API can fill in fields a caller does not send."""
    snapshot = tables["borrowers"][["borrower_id", "partner_id"]].copy()
    snapshot["application_id"] = "SNAPSHOT-" + snapshot["borrower_id"]
    snapshot["application_date"] = as_of
    snapshot["requested_amount"] = np.nan
    feats = build_features(snapshot, tables)
    feats["as_of_date"] = as_of.strftime("%Y-%m-%d")
    keep = ["borrower_id", "partner_id", "as_of_date"] + [
        f for f in MODEL_FEATURES if f not in DERIVED_FEATURES
    ]
    return feats[[c for c in keep if c != "requested_amount"]]


def run(tables: dict[str, pd.DataFrame] | None = None, paths: Paths | None = None) -> pd.DataFrame:
    paths = paths or Paths()
    if tables is None:
        from src.utils.paths import RAW_TABLES

        tables = {t: pd.read_parquet(paths.processed(t)) for t in RAW_TABLES}

    apps = tables["applications"]
    feats = build_features(apps, tables)
    feats["split"] = assign_split(feats["application_date"], feats["default_flag"])
    feats.to_parquet(paths.features, index=False)
    db.write_table(feats, "application_features")
    log.info(
        "Built %d application feature rows; split counts: %s",
        len(feats),
        feats["split"].value_counts().to_dict(),
    )

    latest_event = max(
        tables["procurement_transactions"]["txn_date"].max(), tables["stock_movements"]["movement_date"].max()
    )
    store = build_feature_store(tables, latest_event + pd.Timedelta(days=1))
    store.to_parquet(paths.feature_store, index=False)
    db.write_table(store, "borrower_feature_store")
    log.info("Feature store: %d borrowers as of %s", len(store), store["as_of_date"].iloc[0])

    if paths.incoming_applications.exists():
        from src.data.ingest import ingest_incoming

        incoming = ingest_incoming(paths)
        incoming["requested_amount"] = pd.to_numeric(incoming["requested_amount"])
        incoming["application_date"] = pd.to_datetime(incoming["application_date"])
        inc = build_features(incoming, tables)
        inc.to_parquet(paths.incoming_features, index=False)
        log.info("Built %d incoming application feature rows", len(inc))
    return feats


if __name__ == "__main__":
    run()
