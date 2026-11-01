"""Backend Engineering: the scoring API contract."""

from __future__ import annotations

PRD_EXAMPLE = {
    "application_id": "APP-10025",
    "borrower_id": "BOR-0301",
    "requested_amount": 25000,
    "recent_procurement_value": 180000,
    "late_payment_count_90d": 1,
    "repayment_rate_180d": 0.91,
    "stock_turnover_ratio": 1.3,
}


def test_health(client):
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["model_loaded"] is True
    assert body["database_ok"] is True


def test_score_prd_example(client):
    r = client.post("/score", json=PRD_EXAMPLE)
    assert r.status_code == 200
    body = r.json()
    assert body["request_id"] == r.headers["X-Request-ID"]
    assert body["model_version"] == "v1.0.0"
    assert 0 <= body["score"] <= 1
    assert body["decision"] in {"approve", "manual_review", "decline"}
    assert body["reason_codes"]
    assert body["timestamp"].endswith("Z")
    assert abs(body["score"] + body["probability_of_default"] - 1) < 1e-3


def test_caller_request_id_is_kept(client):
    r = client.post("/score", json=PRD_EXAMPLE, headers={"X-Request-ID": "req_from_partner"})
    assert r.json()["request_id"] == "req_from_partner"


def test_feature_store_fills_missing_fields(client):
    r = client.post(
        "/score", json={"application_id": "A-1", "borrower_id": "BOR-0010", "requested_amount": 15000}
    )
    body = r.json()
    assert r.status_code == 200
    assert "procurement_frequency_90d" in body["features_from_store"]


def test_unknown_borrower_goes_to_manual_review_or_decline(client):
    r = client.post(
        "/score", json={"application_id": "A-2", "borrower_id": "NEW-999", "requested_amount": 15000}
    )
    body = r.json()
    assert body["decision"] in {"manual_review", "decline"}
    assert len(body["missing_core_features"]) == 4


def test_invalid_payload_returns_422_with_readable_errors(client):
    r = client.post("/score", json={**PRD_EXAMPLE, "requested_amount": -5, "repayment_rate_180d": 1.5})
    assert r.status_code == 422
    body = r.json()
    assert body["error"] == "validation_error"
    assert {e["field"] for e in body["detail"]} == {"requested_amount", "repayment_rate_180d"}


def test_unknown_field_is_rejected(client):
    r = client.post("/score", json={**PRD_EXAMPLE, "loan_to_procurement_ratio": 0.1})
    assert r.status_code == 422


def test_batch_score(client):
    apps = [{**PRD_EXAMPLE, "application_id": f"APP-{i}"} for i in range(5)]
    r = client.post("/batch-score", json={"applications": apps})
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 5
    assert sum(body["decision_counts"].values()) == 5
    assert len({x["request_id"] for x in body["results"]}) == 1


def test_model_info(client):
    body = client.get("/model-info").json()
    assert "repayment_rate_180d" in body["features"]
    assert set(body["thresholds"]) == {"approve_threshold", "decline_threshold"}


def test_metrics_summary_counts_traffic(client):
    client.post("/score", json=PRD_EXAMPLE)
    client.post("/score", json={"bad": "payload"})
    body = client.get("/metrics-summary").json()
    assert body["service"]["scoring_requests"] >= 2
    assert body["service"]["failed_requests"] >= 1
    assert body["decisions"]["scored_applications"] >= 1


def test_model_missing_returns_503(platform_env, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from src.api.main import app
    from src.config import get_settings

    monkeypatch.setenv("ARTIFACTS_DIR", str(tmp_path / "empty"))
    get_settings.cache_clear()
    try:
        with TestClient(app) as c:
            health = c.get("/health")
            assert health.status_code == 503  # load balancers and Docker must see "not ready"
            assert health.json()["status"] == "degraded"
            r = c.post("/score", json=PRD_EXAMPLE)
            assert r.status_code == 503
            assert r.json()["error"] == "model_unavailable"
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()


def test_infinity_is_rejected_not_a_server_error(client):
    body = '{"application_id": "A", "borrower_id": "BOR-0301", "requested_amount": 25000, "stock_turnover_ratio": Infinity}'
    r = client.post("/score", content=body, headers={"Content-Type": "application/json"})
    assert r.status_code == 422


def test_absurd_and_loosely_typed_values_are_rejected(client):
    for bad in (
        {"recent_procurement_value": 1e308},
        {"requested_amount": "25000"},
        {"requested_amount": True},
        {"requested_amount": 450},
        {"borrower_id": "   "},
    ):
        r = client.post("/score", json={**PRD_EXAMPLE, **bad})
        assert r.status_code == 422, bad


def test_explicit_null_is_not_filled_from_feature_store(client):
    r = client.post(
        "/score",
        json={
            "application_id": "A-3",
            "borrower_id": "BOR-0010",
            "requested_amount": 15000,
            "procurement_trend_ratio": None,
        },
    )
    body = r.json()
    assert r.status_code == 200
    assert "procurement_trend_ratio" not in body["features_from_store"]
    assert "procurement_frequency_90d" in body["features_from_store"]


def test_unknown_purchases_never_auto_approved(client):
    r = client.post(
        "/score",
        json={
            "application_id": "A-4",
            "borrower_id": "NEW-777",
            "requested_amount": 1_000_000,
            "late_payment_count_90d": 0,
            "repayment_rate_180d": 1.0,
            "stock_turnover_ratio": 1.2,
            "procurement_frequency_90d": 4,
            "avg_order_value_90d": 20000,
        },
    )
    body = r.json()
    assert body["decision"] != "approve"
    assert body["recommended_limit"] <= 500_000
