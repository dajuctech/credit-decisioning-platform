"""API contracts (Backend Engineering).

Pydantic models define exactly what callers may send and what they get back.
Bad input is rejected with a clear 422 error before it ever reaches the model.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Decision = Literal["approve", "manual_review", "decline"]
MIN_LOAN = 1_000  # smallest loan the lender offers


class ScoreRequest(BaseModel):
    """One loan application. Only the first three fields are required.

    * Leave a behaviour field **out** and the API looks it up in the borrower feature store.
    * Send it as **null** to say "we know there is no data for this" (e.g. no purchases in the earlier
      90 days); the API then does not fill it in.
    """

    model_config = ConfigDict(
        extra="forbid",
        strict=True,  # "25000" or true are not amounts
        allow_inf_nan=False,  # Infinity / NaN are rejected with 422, never reach the model
        str_strip_whitespace=True,  # "   " is not an ID
        json_schema_extra={
            "example": {
                "application_id": "APP-10025",
                "borrower_id": "BOR-0301",
                "requested_amount": 25000,
                "recent_procurement_value": 180000,
                "late_payment_count_90d": 1,
                "repayment_rate_180d": 0.91,
                "stock_turnover_ratio": 1.3,
            }
        },
    )

    application_id: str = Field(min_length=1, max_length=64)
    borrower_id: str = Field(min_length=1, max_length=64)
    requested_amount: float = Field(ge=MIN_LOAN, le=50_000_000)
    partner_id: str | None = Field(default=None, max_length=16)
    requested_tenor_months: int | None = Field(default=None, ge=1, le=36)

    # Upper bounds are generous but finite: anything beyond them is a data error, not a borrower.
    recent_procurement_value: float | None = Field(default=None, ge=0, le=1_000_000_000)
    procurement_frequency_90d: float | None = Field(default=None, ge=0, le=10_000)
    avg_order_value_90d: float | None = Field(default=None, ge=0, le=1_000_000_000)
    procurement_trend_ratio: float | None = Field(default=None, ge=0, le=1_000)
    days_since_last_procurement: float | None = Field(default=None, ge=0, le=36_500)
    active_months_12m: float | None = Field(default=None, ge=0, le=13)
    late_payment_count_90d: float | None = Field(default=None, ge=0, le=1_000)
    repayment_rate_180d: float | None = Field(default=None, ge=0, le=1)
    prior_loan_count: float | None = Field(default=None, ge=0, le=1_000)
    stock_turnover_ratio: float | None = Field(default=None, ge=0, le=1_000)
    stock_trend_ratio: float | None = Field(default=None, ge=0, le=1_000)
    partner_late_rate_180d: float | None = Field(default=None, ge=0, le=1)
    years_in_operation: float | None = Field(default=None, ge=0, le=100)


class BatchScoreRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    applications: list[ScoreRequest] = Field(min_length=1, max_length=1000)


class ReasonDetail(BaseModel):
    feature: str
    value: float | None
    impact: float = Field(description="Change in score caused by this feature versus a typical borrower")
    direction: str


class ScoreResponse(BaseModel):
    request_id: str
    application_id: str
    borrower_id: str
    model_version: str
    score: float = Field(description="Probability the borrower repays (0-1)")
    probability_of_default: float
    decision: Decision
    risk_band: str
    recommended_limit: float
    recommended_tenor_months: int | None
    reason_codes: list[str]
    reason_details: list[ReasonDetail]
    features_from_store: list[str] = Field(description="Features filled in from the borrower feature store")
    missing_core_features: list[str]
    timestamp: str


class BatchScoreResponse(BaseModel):
    request_id: str
    model_version: str
    count: int
    decision_counts: dict[str, int]
    results: list[ScoreResponse]
    timestamp: str


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    model_loaded: bool
    model_version: str | None
    database_ok: bool
    feature_store_rows: int
    uptime_seconds: float
    app_env: str


class ModelInfoResponse(BaseModel):
    model_config = ConfigDict(protected_namespaces=())
    model_version: str
    model_type: str
    trained_at: str
    training_rows: int
    features: list[str]
    feature_descriptions: dict[str, str]
    thresholds: dict[str, float]
    calibration: dict[str, float]
    metrics: dict[str, float | None]


class MetricsSummaryResponse(BaseModel):
    model_config = ConfigDict(protected_namespaces=())
    model_version: str | None
    model_metrics: dict
    service: dict
    decisions: dict
    monitoring: dict


class ErrorResponse(BaseModel):
    request_id: str
    error: str
    detail: list | str | None = None
