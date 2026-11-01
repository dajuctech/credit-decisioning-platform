.DEFAULT_GOAL := help
API_URL ?= http://localhost:8000

help: ## List commands
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

setup: ## Install dependencies with uv
	uv sync

pipeline: ## Run everything: data -> model -> simulated traffic -> monitoring -> reports
	uv run python -m src.run_pipeline

data: ## Generate, validate and clean data, build features
	uv run python -m src.run_pipeline --to features

train: ## Train, calibrate and evaluate the model
	uv run python -m src.run_pipeline --only train

api: ## Start the scoring API on :8000 (docs at /docs)
	uv run uvicorn src.api.main:app --host 0.0.0.0 --port 8000 --reload

dashboard: ## Start the Streamlit dashboard on :8501
	uv run streamlit run dashboard/app.py

monitor: ## Send traffic to a running API, then run monitoring and reports
	uv run python -m src.run_pipeline --from simulate --api-url $(API_URL)

test: ## Run the test suite
	uv run pytest

lint: ## Lint and check formatting
	uv run ruff check .
	uv run ruff format --check .

format: ## Auto-format the code
	uv run ruff format .
	uv run ruff check --fix .

docker-build: ## Build the Docker image
	docker compose build

docker-up: ## Start API + dashboard in Docker
	docker compose up -d --build

docker-monitor: ## Run the monitoring job inside Docker against the containerised API
	docker compose --profile jobs run --rm monitor

docker-down: ## Stop the containers
	docker compose down

docker-reset: ## Rebuild from scratch: delete the volumes, rebuild the image, start again
	docker compose down -v
	docker compose up -d --build

clean: ## Delete generated data, models and reports
	rm -rf data/raw/*.csv data/processed/* data/monitoring/* data/*.db artifacts/models/*.joblib artifacts/models/latest.json
	find reports -type f ! -name .gitkeep -delete

all: setup lint test pipeline ## Install, check, test and run the full pipeline

.PHONY: help setup pipeline data train api dashboard monitor test lint format docker-build docker-up docker-monitor docker-down docker-reset clean all
