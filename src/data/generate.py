"""Synthetic data generator (Milestone 1: dataset design).

Creates realistic but fake data for a lender that finances stock for clinics
and pharmacies. Nothing here is real customer data.

Business story baked into the data
----------------------------------
* Each borrower has a hidden "risk" level. Riskier businesses order stock less
  often, sell it more slowly, pay instalments late and default more often.
  The model never sees this hidden value; it must learn it from behaviour.
* During a pilot (Jul 2023 - Dec 2025) the lender funded every application so
  it could learn which signals predict repayment. `approval_flag` records what
  a manual underwriter would have decided at the time, for comparison.
* `default_flag` = borrower missed payments and reached 60+ days past due within
  the first 6 months. Loans applied for after 2025-06-30 have not been open long
  enough to know the outcome, so their label is empty ("not yet matured").
* `incoming_applications.csv` holds new requests from Jan-Mar 2026 that arrive
  at the API after launch. They are intentionally different from the training
  data (price inflation, a supply shock in two regions, and a newly onboarded
  partner whose feed has no stock data) so monitoring has something to detect.
* A small number of dirty records are injected so the validation layer has
  real problems to catch.

Run:  uv run python -m src.data.generate
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from src.config import get_settings
from src.utils.logger import get_logger
from src.utils.paths import Paths

log = get_logger(__name__)

# History starts a year before lending, so even the first applications have 12+ months of behaviour.
HISTORY_START = pd.Timestamp("2022-07-01")
APPLICATION_START = pd.Timestamp("2023-07-01")
APPLICATION_END = pd.Timestamp("2025-12-31")  # last historical application
REPAYMENT_CUTOFF = pd.Timestamp("2026-03-31")  # repayment feed keeps flowing through the live period
LABEL_MATURITY_CUTOFF = pd.Timestamp("2025-06-30")
LIVE_START = pd.Timestamp("2026-01-01")
LIVE_END = pd.Timestamp("2026-03-31")

REGIONS = ["Lagos", "Abuja", "Kano", "Nairobi", "Mombasa", "Kisumu"]
SHOCK_REGIONS = {"Kano", "Mombasa"}  # supply shock in 2026 (drift scenario)

PARTNERS = [
    # partner_id, name, channel, region, quality effect (negative = safer)
    ("P01", "MedSupply Lagos", "pharma_distributor", "Lagos", -0.30),
    ("P02", "HealthChain Abuja", "pharma_distributor", "Abuja", -0.10),
    ("P03", "Northern Wholesale", "wholesaler", "Kano", 0.25),
    ("P04", "Afya Distributors", "pharma_distributor", "Nairobi", -0.20),
    ("P05", "Coastal Pharma", "wholesaler", "Mombasa", 0.15),
    ("P06", "Lakeside Health", "ngo_program", "Kisumu", 0.35),
    ("P07", "CarePoint Direct", "direct_sales", "Lagos", 0.05),
    ("P08", "Savannah Medical", "wholesaler", "Abuja", 0.10),
    ("P09", "QuickMeds Network", "digital_marketplace", "Nairobi", 0.45),  # onboarded 2026
]


def _sigmoid(x):
    return 1.0 / (1.0 + np.exp(-x))


def _month_starts(start: pd.Timestamp, end: pd.Timestamp) -> pd.DatetimeIndex:
    return pd.date_range(start.to_period("M").to_timestamp(), end, freq="MS")


def _make_partners() -> pd.DataFrame:
    rows = []
    for pid, name, channel, region, _ in PARTNERS:
        onboarded = "2026-01-05" if pid == "P09" else "2023-03-01"
        rows.append(
            {
                "partner_id": pid,
                "partner_name": name,
                "channel": channel,
                "region": region,
                "onboarded_date": onboarded,
            }
        )
    return pd.DataFrame(rows)


def _make_borrowers(rng: np.random.Generator, n: int, n_new: int) -> pd.DataFrame:
    quality = {p[0]: p[4] for p in PARTNERS}
    region_of = {p[0]: p[3] for p in PARTNERS}
    established = [p[0] for p in PARTNERS if p[0] != "P09"]

    partner_ids = list(rng.choice(established, size=n)) + ["P09"] * n_new
    total = n + n_new
    is_new = np.array([False] * n + [True] * n_new)

    onboarding = np.where(
        is_new,
        (LIVE_START - pd.to_timedelta(rng.integers(0, 40, total), unit="D")).values,
        (pd.Timestamp("2022-01-01") + pd.to_timedelta(rng.integers(0, 1200, total), unit="D")).values,
    )
    latent = rng.normal(0, 1, total) + np.array([quality[p] for p in partner_ids])
    business_type = rng.choice(["pharmacy", "clinic"], size=total, p=[0.55, 0.45])
    base_monthly = np.exp(rng.normal(np.log(60_000), 0.55, total)).clip(12_000, 400_000)
    # Clinics buy a little less stock than pharmacies.
    base_monthly = np.where(business_type == "clinic", base_monthly * 0.8, base_monthly)

    return pd.DataFrame(
        {
            "borrower_id": [f"BOR-{i:04d}" for i in range(1, total + 1)],
            "business_name": [f"{bt.title()} {i:04d}" for i, bt in enumerate(business_type, 1)],
            "business_type": business_type,
            "region": [region_of[p] if rng.random() < 0.85 else rng.choice(REGIONS) for p in partner_ids],
            "partner_id": partner_ids,
            "onboarding_date": pd.to_datetime(onboarding).strftime("%Y-%m-%d"),
            "years_in_operation": np.clip(np.round(rng.gamma(2.2, 2.5, total) - 0.4 * latent, 1), 0.5, 30),
            "staff_count": np.clip(rng.poisson(5, total) + 1, 1, 60),
            # hidden columns used only inside the generator (dropped before saving)
            "hidden_latent": latent,
            "hidden_base_monthly": base_monthly,
            "hidden_is_new": is_new,
        }
    )


def _make_procurement_and_stock(rng: np.random.Generator, borrowers: pd.DataFrame):
    proc_rows, stock_rows = [], []
    months_all = _month_starts(HISTORY_START, LIVE_END)
    for b in borrowers.itertuples(index=False):
        start = max(pd.Timestamp(b.onboarding_date), HISTORY_START)
        months = months_all[months_all >= start.to_period("M").to_timestamp()]
        z, base = b.hidden_latent, b.hidden_base_monthly
        p_inactive = _sigmoid(-2.6 + 0.9 * z)
        for m in months:
            years_from_start = (m - HISTORY_START).days / 365.0
            # riskier borrowers slowly shrink, safer ones slowly grow
            trend = np.exp((-0.12 * z + 0.05) * years_from_start)
            shock = 1.0
            inflation = 1.0
            if m >= LIVE_START:
                inflation = 1.18  # prices up in 2026
                if b.region in SHOCK_REGIONS:
                    shock = 0.55  # supply shortage -> far fewer orders
            if rng.random() < p_inactive:
                continue
            n_orders = max(1, rng.poisson(max(0.6, 3.2 - 0.7 * z) * shock))
            month_value = base * trend * shock * inflation * rng.lognormal(0, 0.18)
            weights = rng.dirichlet(np.ones(n_orders))
            days = np.sort(rng.integers(0, m.days_in_month, n_orders))
            for w, d in zip(weights, days, strict=True):
                proc_rows.append(
                    {
                        "borrower_id": b.borrower_id,
                        "partner_id": b.partner_id,
                        "txn_date": m + pd.Timedelta(days=int(d)),
                        "procurement_value": round(float(month_value * w), 2),
                        "items_count": int(max(1, rng.poisson(12 * w * n_orders))),
                    }
                )
            # Stock feed: the 2026 marketplace partner does not send stock data.
            if b.partner_id == "P09":
                continue
            inbound = month_value * rng.uniform(0.95, 1.05)
            turnover = np.clip(rng.normal(1.0 - 0.12 * z, 0.08), 0.4, 1.4)
            stock_rows.append(
                {
                    "borrower_id": b.borrower_id,
                    "movement_date": m + pd.Timedelta(days=int(rng.integers(5, 25))),
                    "movement_type": "inbound",
                    "stock_value": round(float(inbound), 2),
                }
            )
            stock_rows.append(
                {
                    "borrower_id": b.borrower_id,
                    "movement_date": m + pd.Timedelta(days=int(rng.integers(5, 25))),
                    "movement_type": "outbound",
                    "stock_value": round(float(inbound * turnover), 2),
                }
            )
    proc = pd.DataFrame(proc_rows).sort_values("txn_date").reset_index(drop=True)
    proc.insert(0, "txn_id", [f"TXN-{i:07d}" for i in range(1, len(proc) + 1)])
    stock = pd.DataFrame(stock_rows).sort_values("movement_date").reset_index(drop=True)
    stock.insert(0, "movement_id", [f"STK-{i:07d}" for i in range(1, len(stock) + 1)])
    return proc, stock


def _make_applications_and_repayments(rng: np.random.Generator, borrowers: pd.DataFrame):
    apps, reps = [], []
    defaulted_borrowers: set[str] = set()
    app_no = 0
    quality = {p[0]: p[4] for p in PARTNERS}
    for b in borrowers[~borrowers["hidden_is_new"]].itertuples(index=False):
        z, base = b.hidden_latent, b.hidden_base_monthly
        first_possible = max(pd.Timestamp(b.onboarding_date) + pd.Timedelta(days=60), APPLICATION_START)
        current = first_possible + pd.Timedelta(days=int(rng.integers(0, 200)))
        loan_index = 0
        while current <= APPLICATION_END:
            app_no += 1
            loan_index += 1
            requested = round(base * rng.uniform(0.25, 0.75) / 500) * 500
            tenor = int(rng.choice([3, 6, 9, 12], p=[0.3, 0.4, 0.15, 0.15]))
            size_ratio = requested / base
            pd_logit = (
                -2.15
                + 1.6 * z
                + 0.9 * (size_ratio - 0.5)
                + 0.4 * quality[b.partner_id]
                - 0.15 * (loan_index - 1)
            )
            defaulted = rng.random() < _sigmoid(pd_logit)
            manual_score = -z + rng.normal(0, 1.0) + 0.1 * b.years_in_operation
            approval_flag = int(manual_score > -0.6)
            matured = current <= LABEL_MATURITY_CUTOFF
            application_id = f"APP-{app_no:05d}"
            apps.append(
                {
                    "application_id": application_id,
                    "borrower_id": b.borrower_id,
                    "partner_id": b.partner_id,
                    "application_date": current,
                    "requested_amount": float(requested),
                    "requested_tenor_months": tenor,
                    "approval_flag": approval_flag,
                    "default_flag": (int(defaulted) if matured else pd.NA),
                }
            )
            # Repayment schedule: monthly instalments with a flat 4% monthly fee.
            due_amount = round(requested * (1 + 0.04 * tenor) / tenor, 2)
            default_at = int(rng.integers(0, min(tenor, 5))) if defaulted else None
            p_late = _sigmoid(-2.0 + 1.1 * z)
            for k in range(1, tenor + 1):
                due = current + pd.DateOffset(months=k)
                if due > REPAYMENT_CUTOFF:
                    break
                if default_at is not None and k > default_at:
                    paid_date, paid = pd.NaT, 0.0
                elif rng.random() < p_late:
                    paid_date = due + pd.Timedelta(days=int(rng.integers(8, 45)))
                    # late payers often only manage part of the instalment
                    paid = due_amount * (rng.uniform(0.4, 0.95) if rng.random() < 0.45 else 1.0)
                else:
                    paid_date = due - pd.Timedelta(days=int(rng.integers(0, 6)))
                    paid = due_amount
                if pd.notna(paid_date) and paid_date > REPAYMENT_CUTOFF:
                    paid_date, paid = pd.NaT, 0.0
                reps.append(
                    {
                        "application_id": application_id,
                        "borrower_id": b.borrower_id,
                        "installment_number": k,
                        "repayment_due_date": due,
                        "amount_due": due_amount,
                        "repayment_date": paid_date,
                        "repayment_amount": round(paid, 2),
                    }
                )
            # A borrower who defaults does not come back. Others reapply later.
            if defaulted:
                defaulted_borrowers.add(b.borrower_id)
                break
            current = current + pd.Timedelta(days=int(rng.integers(110, 260)))
    applications = pd.DataFrame(apps).sort_values("application_date").reset_index(drop=True)
    repayments = pd.DataFrame(reps).reset_index(drop=True)
    repayments.insert(0, "repayment_id", [f"REP-{i:07d}" for i in range(1, len(repayments) + 1)])
    return applications, repayments, defaulted_borrowers


def _make_incoming(
    rng: np.random.Generator, borrowers: pd.DataFrame, n: int, blocked: set[str]
) -> pd.DataFrame:
    # Borrowers who defaulted are blocked by the lender and cannot reapply.
    existing = borrowers[~borrowers["hidden_is_new"] & ~borrowers["borrower_id"].isin(blocked)]
    new = borrowers[borrowers["hidden_is_new"]]
    n_new = min(len(new), int(n * 0.22))
    picks = pd.concat(
        [existing.sample(n - n_new, random_state=int(rng.integers(1e9))), new.sample(n_new, random_state=1)]
    )
    days = rng.integers(0, (LIVE_END - LIVE_START).days + 1, len(picks))
    # 2026 inflation: prices are ~18% higher, so borrowers ask for larger loans.
    requested = (
        np.round(picks["hidden_base_monthly"].values * rng.uniform(0.25, 0.75, len(picks)) * 1.18 / 500) * 500
    )
    df = pd.DataFrame(
        {
            "borrower_id": picks["borrower_id"].values,
            "partner_id": picks["partner_id"].values,
            "application_date": LIVE_START + pd.to_timedelta(days, unit="D"),
            "requested_amount": requested.astype(float),
            "requested_tenor_months": rng.choice([3, 6, 9, 12], size=len(picks), p=[0.3, 0.4, 0.15, 0.15]),
        }
    ).sort_values("application_date")
    df.insert(0, "application_id", [f"APP-L{i:05d}" for i in range(1, len(df) + 1)])
    return df.reset_index(drop=True)


def _inject_dirty_records(rng: np.random.Generator, t: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    """Deliberately corrupt a small share of rows so validation has work to do."""
    t = {k: v.copy() for k, v in t.items()}

    # Partners: inconsistent channel label
    t["partners"].loc[t["partners"]["partner_id"] == "P07", "channel"] = "Direct Sales "

    b = t["borrowers"]
    b["business_type"] = b["business_type"].astype(object)
    idx = rng.choice(b.index, 60, replace=False)
    variants = {"pharmacy": ["Pharmacy", "PHARMACY ", "pharm"], "clinic": ["Clinic", " clinic", "CLINIC"]}
    for i in idx:
        b.at[i, "business_type"] = rng.choice(variants[b.at[i, "business_type"]])
    for i in rng.choice(b.index, 25, replace=False):
        b.at[i, "region"] = rng.choice([b.at[i, "region"].lower(), b.at[i, "region"].upper() + " "])
    broken_fk = rng.choice(b.index[b["partner_id"] != "P09"], 3, replace=False)
    b.loc[broken_fk, "partner_id"] = "P99"
    b = pd.concat([b, b.sample(5, random_state=7)], ignore_index=True)  # duplicate IDs
    t["borrowers"] = b

    a = t["applications"]
    a["application_date"] = a["application_date"].dt.strftime("%Y-%m-%d").astype(object)
    a.loc[rng.choice(a.index, 8, replace=False), "requested_amount"] *= -1
    a.loc[rng.choice(a.index, 3, replace=False), "application_date"] = "2025-13-45"
    a.loc[rng.choice(a.index, 3, replace=False), "application_date"] = "not_a_date"
    orphan = a.sample(5, random_state=11).copy()
    orphan["borrower_id"] = [f"BOR-9{i:03d}" for i in range(5)]
    orphan["application_id"] = [f"APP-X{i:04d}" for i in range(5)]
    a = pd.concat([a, orphan, a.sample(4, random_state=3)], ignore_index=True)
    t["applications"] = a

    p = t["procurement_transactions"]
    p["txn_date"] = p["txn_date"].dt.strftime("%Y-%m-%d").astype(object)
    p.loc[rng.choice(p.index, 20, replace=False), "procurement_value"] *= -1
    p.loc[rng.choice(p.index, 10, replace=False), "txn_date"] = "31/02/2025"
    t["procurement_transactions"] = p

    s = t["stock_movements"]
    s.loc[rng.choice(s.index, 10, replace=False), "stock_value"] *= -1
    t["stock_movements"] = s

    r = t["repayments"]
    r.loc[rng.choice(r.index, 10, replace=False), "repayment_amount"] = -100.0
    t["repayments"] = r
    return t


def generate(seed: int | None = None, n_borrowers: int | None = None, n_incoming: int | None = None) -> dict:
    settings = get_settings()
    seed = settings.random_seed if seed is None else seed
    n_borrowers = n_borrowers or settings.n_borrowers
    n_incoming = n_incoming or settings.n_incoming_applications
    rng = np.random.default_rng(seed)
    paths = Paths(settings)

    log.info("Generating synthetic data: %s borrowers, seed=%s", n_borrowers, seed)
    partners = _make_partners()
    borrowers = _make_borrowers(rng, n_borrowers, n_new=max(10, n_borrowers // 10))
    procurement, stock = _make_procurement_and_stock(rng, borrowers)
    applications, repayments, defaulted = _make_applications_and_repayments(rng, borrowers)
    incoming = _make_incoming(rng, borrowers, n_incoming, blocked=defaulted)

    tables = {
        "partners": partners,
        "borrowers": borrowers.drop(columns=["hidden_latent", "hidden_base_monthly", "hidden_is_new"]),
        "applications": applications,
        "procurement_transactions": procurement,
        "repayments": repayments,
        "stock_movements": stock,
    }
    tables = _inject_dirty_records(rng, tables)

    for name, df in tables.items():
        df.to_csv(paths.raw(name), index=False)
        log.info("  wrote %-26s %7d rows -> %s", name, len(df), paths.raw(name).name)
    incoming.to_csv(paths.incoming_applications, index=False)
    log.info(
        "  wrote %-26s %7d rows -> %s", "incoming_applications", len(incoming), "incoming_applications.csv"
    )
    return tables | {"incoming_applications": incoming}


if __name__ == "__main__":
    generate()
