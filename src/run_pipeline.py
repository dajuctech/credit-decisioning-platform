"""Run the whole platform end to end with one command.

    uv run python -m src.run_pipeline                 # everything
    uv run python -m src.run_pipeline --from train    # resume from a stage
    uv run python -m src.run_pipeline --only monitor  # one stage
    uv run python -m src.run_pipeline --api-url http://localhost:8000   # send traffic to a live API

Stages (each one feeds the next):
    1 generate   synthetic raw data                       -> data/raw/
    2 clean      ingest + validate + clean                -> data/processed/, data/platform.db
    3 features   point-in-time features + feature store   -> data/processed/
    4 train      train, calibrate, evaluate, save model   -> artifacts/models/, reports/metrics/
    5 simulate   send post-launch applications to the API -> model_scores, api_requests tables
    6 monitor    drift, score and service checks          -> data/monitoring/, monitoring_snapshots
    7 report     business reports and charts              -> reports/business/, reports/figures/
"""

from __future__ import annotations

import argparse
import time

from src.config import get_settings
from src.utils.logger import get_logger

log = get_logger("pipeline")

STAGES = ["generate", "clean", "features", "train", "simulate", "monitor", "report"]


def run_stage(name: str, api_url: str | None = None) -> None:
    if name == "generate":
        from src.data.generate import generate

        generate()
    elif name == "clean":
        from src.data.clean import run

        run()
    elif name == "features":
        from src.data.feature_engineering import run

        run()
    elif name == "train":
        from src.models.train import train

        train()
    elif name == "simulate":
        from src.monitoring.simulate_traffic import run

        run(url=api_url)
    elif name == "monitor":
        from src.monitoring.score_checks import run

        health = None
        if api_url:
            import httpx

            health = httpx.get(f"{api_url}/health", timeout=10).json()
        run(health=health)
    elif name == "report":
        from src.monitoring.reporting import run

        run()


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--from", dest="start", choices=STAGES, default=STAGES[0], help="first stage to run")
    ap.add_argument("--to", dest="end", choices=STAGES, default=STAGES[-1], help="last stage to run")
    ap.add_argument("--only", choices=STAGES, help="run a single stage")
    ap.add_argument(
        "--api-url", default=None, help="send simulated traffic to this running API instead of in-process"
    )
    args = ap.parse_args(argv)

    stages = [args.only] if args.only else STAGES[STAGES.index(args.start) : STAGES.index(args.end) + 1]
    settings = get_settings()
    log.info(
        "Environment=%s data=%s artifacts=%s", settings.app_env, settings.data_dir, settings.artifacts_dir
    )
    total = time.perf_counter()
    for i, stage in enumerate(stages, 1):
        t = time.perf_counter()
        log.info("=== [%d/%d] %s ===", i, len(stages), stage)
        run_stage(stage, args.api_url)
        log.info("=== %s finished in %.1fs ===", stage, time.perf_counter() - t)
    log.info("Pipeline complete in %.1fs", time.perf_counter() - total)


if __name__ == "__main__":
    main()
