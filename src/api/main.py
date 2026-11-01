"""Credit decisioning API (Backend Engineering, Milestone 4).

Run locally:   uv run uvicorn src.api.main:app --reload --port 8000
Interactive docs: http://localhost:8000/docs

Every request gets a request ID (returned in the `X-Request-ID` header) and
is logged to the `api_requests` table with its status and latency. Those logs
feed the monitoring layer.
"""

from __future__ import annotations

import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from src.api.schemas import (
    BatchScoreRequest,
    BatchScoreResponse,
    ErrorResponse,
    HealthResponse,
    MetricsSummaryResponse,
    ModelInfoResponse,
    ScoreRequest,
    ScoreResponse,
)
from src.api.services import ScoringService, utc_now
from src.config import get_settings
from src.models.predict import ModelNotFoundError
from src.utils import db
from src.utils.logger import get_logger

log = get_logger("api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.service = ScoringService()
    yield


app = FastAPI(
    title="Credit Decisioning API",
    description="Scores clinic and pharmacy loan applications and returns a decision with reason codes.",
    version="1.0.0",
    lifespan=lifespan,
)


def _service(request: Request) -> ScoringService:
    return request.app.state.service


@app.middleware("http")
async def request_logging(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID") or f"req_{uuid.uuid4().hex[:12]}"
    request.state.request_id = request_id
    request.state.error = None
    start = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception as exc:  # noqa: BLE001 - last line of defence: never leak a stack trace
        log.exception("Unhandled error on %s", request.url.path)
        request.state.error = f"{type(exc).__name__}: {exc}"
        response = JSONResponse(
            status_code=500, content={"request_id": request_id, "error": "internal_error", "detail": None}
        )
    latency_ms = (time.perf_counter() - start) * 1000
    response.headers["X-Request-ID"] = request_id
    log.info(
        "%s %s -> %d (%.1f ms) id=%s",
        request.method,
        request.url.path,
        response.status_code,
        latency_ms,
        request_id,
    )
    if request.url.path not in {"/health", "/favicon.ico"}:
        try:
            db.insert_row(
                "api_requests",
                {
                    "request_id": request_id,
                    "method": request.method,
                    "path": request.url.path,
                    "status_code": response.status_code,
                    "latency_ms": round(latency_ms, 2),
                    "error": request.state.error,
                    "requested_at": utc_now(),
                },
            )
        except Exception:  # noqa: BLE001 - logging failures must not break scoring
            log.exception("Could not write request log")
    return response


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    errors = [{"field": ".".join(str(p) for p in e["loc"][1:]), "message": e["msg"]} for e in exc.errors()]
    request.state.error = (
        "validation_error: " + "; ".join(f"{e['field']}: {e['message']}" for e in errors)[:500]
    )
    return JSONResponse(
        status_code=422,
        content={"request_id": request.state.request_id, "error": "validation_error", "detail": errors},
    )


@app.exception_handler(ModelNotFoundError)
async def model_missing(request: Request, exc: ModelNotFoundError):
    request.state.error = f"model_unavailable: {exc}"
    return JSONResponse(
        status_code=503,
        content={"request_id": request.state.request_id, "error": "model_unavailable", "detail": str(exc)},
    )


ERRORS = {422: {"model": ErrorResponse}, 503: {"model": ErrorResponse}}


@app.get("/", include_in_schema=False)
def root():
    return {"service": "credit-decisioning-api", "docs": "/docs", "health": "/health"}


@app.get(
    "/health", response_model=HealthResponse, responses={503: {"model": HealthResponse}}, tags=["operations"]
)
def health(request: Request, response: Response):
    """Readiness check used by Docker and cloud load balancers.
    Returns 503 when the model or database is unavailable, so traffic is not routed here."""
    body = _service(request).health(get_settings().app_env)
    if body["status"] != "ok":
        response.status_code = 503
    return body


@app.get("/model-info", response_model=ModelInfoResponse, responses=ERRORS, tags=["model"])
def model_info(request: Request):
    """Which model is live, what it uses, and how it performed in testing."""
    return _service(request).model_info()


@app.post("/score", response_model=ScoreResponse, responses=ERRORS, tags=["scoring"])
def score(payload: ScoreRequest, request: Request):
    """Score one application and return a decision, limit, tenor and reason codes."""
    return _service(request).score([payload], request.state.request_id)[0]


@app.post("/batch-score", response_model=BatchScoreResponse, responses=ERRORS, tags=["scoring"])
def batch_score(payload: BatchScoreRequest, request: Request):
    """Score up to 1,000 applications in one call."""
    results = _service(request).score(payload.applications, request.state.request_id, source="batch")
    counts: dict[str, int] = {}
    for r in results:
        counts[r["decision"]] = counts.get(r["decision"], 0) + 1
    return {
        "request_id": request.state.request_id,
        "model_version": results[0]["model_version"],
        "count": len(results),
        "decision_counts": counts,
        "results": results,
        "timestamp": utc_now(),
    }


@app.get("/metrics-summary", response_model=MetricsSummaryResponse, tags=["operations"])
def metrics_summary(request: Request):
    """Model test metrics, live decision mix, API reliability and the latest monitoring status."""
    return _service(request).metrics_summary()


@app.post("/admin/reload-model", tags=["operations"])
def reload_model(request: Request):
    """Reload the latest model artefact without restarting the container (used after retraining)."""
    _service(request).reload()
    return _service(request).health(get_settings().app_env)
