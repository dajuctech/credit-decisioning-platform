"""Monitoring: the end-to-end feedback loop on real API traffic."""

from __future__ import annotations

from src.monitoring.score_checks import AI, BACKEND, CLOUD, DATA


def test_simulation_monitoring_and_reports(platform_env):
    from src.monitoring import reporting, score_checks, simulate_traffic
    from src.utils import db
    from src.utils.paths import Paths

    stats = simulate_traffic.run()
    assert stats["ok"] > 0

    logged = db.read_sql("SELECT COUNT(*) AS n FROM model_scores")["n"].iat[0]
    assert logged == stats["ok"]

    checks = score_checks.run()
    assert set(checks["owner_layer"]) == {DATA, AI, BACKEND, CLOUD}
    assert set(checks["status"]) <= {"ok", "warn", "alert"}
    # the new partner with no stock feed must be caught as a data problem
    stock_gap = checks[
        (checks["check_name"] == "missing_data_spike") & (checks["subject"] == "stock_turnover_ratio")
    ]
    assert not stock_gap.empty and stock_gap["status"].iat[0] == "alert"
    assert "P09" in stock_gap["explanation"].iat[0]

    reporting.run()
    paths = Paths()
    for name in (
        "scoring_summary",
        "drift_report",
        "approval_trend_report",
        "high_risk_applications",
        "monthly_model_performance",
        "monitoring_summary",
    ):
        assert paths.business_report(name).exists(), name
    assert db.table_exists("monitoring_snapshots")
