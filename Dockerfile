# Credit Decisioning Platform - one image, three roles (api, dashboard, monitoring job).
#
#   docker build -t credit-decisioning:latest .
#   docker run -p 8000:8000 credit-decisioning:latest
#
# The data pipeline and model training run during the build, so the image is
# self-contained: it starts serving immediately with a trained model. In a real
# cloud setup the model would be pulled from a model registry / bucket instead
# (see src/cloud/deployment_notes.md).

FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.10.8 /uv /uvx /bin/

ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH=/opt/venv/bin:$PATH \
    APP_ENV=docker

WORKDIR /app

# 1. Dependencies first: this layer is cached until pyproject.toml / uv.lock change.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project

# 2. Application code.
COPY src ./src
COPY dashboard ./dashboard

# 3. Build data and model artefacts into the image (reproducible: fixed random seed).
RUN python -m src.run_pipeline --to train

# 4. Run as a non-root user.
RUN useradd --create-home --uid 1000 app && chown -R app:app /app
USER app

EXPOSE 8000 8501

HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/health', timeout=4).status == 200 else 1)"

CMD ["uvicorn", "src.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
