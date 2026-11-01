"""Post-launch traffic simulator (connects Backend -> Monitoring).

Sends the Jan-Mar 2026 incoming applications through the *real* API, the way
a partner system would. That makes the monitoring layer work on genuine
API logs and scoring outputs, not on a copy of the training data.

A small share of requests are deliberately malformed (negative amounts,
impossible ratios, missing IDs) to mimic a buggy partner integration.

Run against a live server:   uv run python -m src.monitoring.simulate_traffic --url http://localhost:8000
Run in-process (no server):  uv run python -m src.monitoring.simulate_traffic
"""

from __future__ import annotations

import argparse
import math
from contextlib import contextmanager

import httpx
import numpy as np
import pandas as pd

from src.api.services import LOOKUP_FEATURES
from src.utils import db
from src.utils.logger import get_logger
from src.utils.paths import Paths

log = get_logger(__name__)

BATCH_SHARE = 0.25
BATCH_SIZE = 25
MALFORMED_SHARE = 0.03


def build_payloads(features: pd.DataFrame) -> list[dict]:
    payloads = []
    for r in features.itertuples(index=False):
        p = {
            "application_id": r.application_id,
            "borrower_id": r.borrower_id,
            "partner_id": r.partner_id,
            "requested_amount": float(r.requested_amount),
            "requested_tenor_months": int(r.requested_tenor_months),
        }
        for f in LOOKUP_FEATURES:
            v = getattr(r, f)
            # The upstream system knows these values as of the application date. "No data" is sent
            # as an explicit null so the API does not fill it from a later feature-store snapshot.
            missing = v is None or (isinstance(v, float) and math.isnan(v))
            p[f] = None if missing else round(float(v), 4)
        payloads.append(p)
    return payloads


def corrupt(payload: dict, rng: np.random.Generator) -> dict:
    p = dict(payload)
    kind = rng.integers(0, 4)
    if kind == 0:
        p["requested_amount"] = -abs(p["requested_amount"])
    elif kind == 1:
        p["repayment_rate_180d"] = 1.7
    elif kind == 2:
        p.pop("borrower_id")
    else:
        p["stock_turnover_ratio"] = "not-a-number"
    return p


@contextmanager
def _client(url: str | None):
    if url:
        with httpx.Client(base_url=url, timeout=30) as c:
            yield c
    else:
        from fastapi.testclient import TestClient

        from src.api.main import app

        with TestClient(app) as c:
            yield c


def reset_service_logs() -> None:
    db.init_service_tables()
    with db.connect() as conn:
        conn.execute("DELETE FROM model_scores")
        conn.execute("DELETE FROM api_requests")


def run(url: str | None = None, paths: Paths | None = None, seed: int = 7, reset: bool = True) -> dict:
    paths = paths or Paths()
    rng = np.random.default_rng(seed)
    features = pd.read_parquet(paths.incoming_features)
    payloads = build_payloads(features)
    if reset:
        reset_service_logs()

    stats = {"sent": 0, "ok": 0, "failed": 0, "batches": 0}
    with _client(url) as client:
        i = 0
        while i < len(payloads):
            if rng.random() < BATCH_SHARE:
                chunk = payloads[i : i + BATCH_SIZE]
                r = client.post("/batch-score", json={"applications": chunk})
                stats["batches"] += 1
                stats["sent"] += len(chunk)
                stats["ok" if r.status_code == 200 else "failed"] += len(chunk)
                i += BATCH_SIZE
                continue
            p = payloads[i]
            if rng.random() < MALFORMED_SHARE:
                bad = client.post("/score", json=corrupt(p, rng))
                stats["failed"] += int(bad.status_code >= 400)
                stats["sent"] += 1
            r = client.post("/score", json=p)  # partner retries with a correct payload
            stats["sent"] += 1
            stats["ok" if r.status_code == 200 else "failed"] += 1
            i += 1
    log.info("Traffic simulation done: %s", stats)
    logged = int(db.read_sql("SELECT COUNT(*) AS n FROM model_scores")["n"].iat[0])
    if url and stats["ok"] and logged == 0:
        raise RuntimeError(
            f"The API at {url} scored {stats['ok']} applications but wrote them to a different database "
            f"than {db.db_path()}. Monitoring must read the API's own logs: run it where the API's data "
            "is visible (`make api` + `make monitor` locally, or `make docker-monitor` for the Docker API)."
        )
    return stats


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default=None, help="Base URL of a running API. Omit to run in-process.")
    ap.add_argument("--keep-history", action="store_true", help="Do not clear earlier scoring logs first.")
    args = ap.parse_args()
    run(url=args.url, reset=not args.keep_history)
