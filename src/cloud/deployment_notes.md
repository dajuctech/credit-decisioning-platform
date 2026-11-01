# Cloud Deployment Notes

How the platform moves from a laptop to a container platform, and where every
piece would live in the cloud. Nothing here needs a cloud account to read;
the local Docker setup mirrors the same shape.

## 1. What gets deployed

| Piece | Local (docker compose) | Cloud equivalent |
|---|---|---|
| Scoring API | `api` container, port 8000 | Cloud Run service / ECS Fargate service / Azure Container App |
| Dashboard | `dashboard` container, port 8501 | Second container service behind SSO (internal users only) |
| Pipeline + monitoring | `monitor` job (`--profile jobs`) | Cloud Run Job / ECS scheduled task / Container Apps Job, run nightly |
| Raw + processed data | `platform-data` volume (`/app/data`) | S3 / GCS / Azure Blob bucket (`raw/`, `processed/`, `monitoring/`) |
| Model artefacts | Built into the image (`/app/artifacts`), so every image version carries its own model | Bucket prefix `models/` or a model registry (MLflow, SageMaker, Vertex) |
| Reports | `platform-reports` volume (`/app/reports`) | Bucket prefix `reports/`, shared with analysts |
| Database (`platform.db`) | SQLite file | Managed PostgreSQL (RDS / Cloud SQL / Azure Database) |
| Logs | container stdout | CloudWatch Logs / Cloud Logging / Log Analytics |
| Config | `.env` file | Service environment variables; secrets in Secrets Manager / Secret Manager / Key Vault |

The code reads every location from environment variables (`src/config/settings.py`),
so switching from local folders to cloud mounts is a configuration change, not a code change.

## 2. Environment variables

| Variable | Purpose | Example in cloud |
|---|---|---|
| `APP_ENV` | Shown in `/health`; lets logs separate environments | `production` |
| `LOG_LEVEL` | Log verbosity | `INFO` |
| `MODEL_VERSION` | Which artefact to serve | `v1.0.0` |
| `DATA_DIR`, `ARTIFACTS_DIR`, `REPORTS_DIR` | Storage roots | mounted bucket paths (e.g. Cloud Run volume mounts, EFS on ECS) |
| `DB_PATH` | SQLite path (replace with a `DATABASE_URL` for PostgreSQL) | - |
| `API_URL` | Where the dashboard / monitoring job reach the API | internal service URL |

## 3. Deployment paths

### GCP Cloud Run
```bash
gcloud artifacts repositories create credit --repository-format=docker --location=europe-west2
docker build -t europe-west2-docker.pkg.dev/$PROJECT/credit/api:v1.0.0 .
docker push europe-west2-docker.pkg.dev/$PROJECT/credit/api:v1.0.0
gcloud run deploy credit-api --image europe-west2-docker.pkg.dev/$PROJECT/credit/api:v1.0.0 \
  --region europe-west2 --port 8000 --no-allow-unauthenticated \
  --set-env-vars APP_ENV=production,MODEL_VERSION=v1.0.0 --min-instances 1 --memory 1Gi
```
Run monitoring nightly with a **Cloud Run Job** using the same image and
`python -m src.run_pipeline --from simulate --api-url https://<service-url>` triggered by Cloud Scheduler.

### AWS ECS (Fargate)
1. Push the image to ECR.
2. Task definition: container port 8000, health check command = the Dockerfile `HEALTHCHECK`,
   environment from the table above, secrets from Secrets Manager, logs to CloudWatch (`awslogs` driver).
3. Service behind an internal Application Load Balancer with target-group health check `GET /health`.
4. Mount EFS (or sync from S3 at start-up) for `/app/artifacts`; use RDS PostgreSQL for decisions and logs.
5. EventBridge Scheduler runs the monitoring task nightly.

### Azure Container Apps
```bash
az acr build --registry $ACR --image credit-api:v1.0.0 .
az containerapp create -n credit-api -g $RG --environment $ENV \
  --image $ACR.azurecr.io/credit-api:v1.0.0 --target-port 8000 --ingress internal \
  --env-vars APP_ENV=production MODEL_VERSION=v1.0.0 --min-replicas 1
```
Use an Azure Files volume for artefacts and a Container Apps Job (cron trigger) for monitoring.

## 4. CI/CD flow

1. Pull request -> `.github/workflows/ci.yml`: lint, tests, full pipeline, Docker build, container smoke test.
2. Merge to `main` -> build and push the image tagged with the git SHA.
3. Deploy to staging, run the smoke test (`/health`, one `/score`), then promote to production.
4. Roll back by redeploying the previous image tag; the model version is pinned by `MODEL_VERSION`.

## 5. Model promotion and retraining

* Training writes `artifacts/models/credit_model_<version>.joblib` plus `latest.json` (a pointer).
* In the cloud, training runs as a job, uploads the artefact to the bucket, and a reviewer
  promotes it by updating `MODEL_VERSION` (or the registry stage).
* `POST /admin/reload-model` reloads the artefact without a restart (protect it behind IAM in production).
* Monitoring alerts owned by **AI Engineering** (score shift, approval-rate shift) are the trigger to
  consider retraining; alerts owned by **Data Engineering** must be fixed *before* retraining, otherwise
  the new model learns from broken data.

## 6. Operations

* **Health**: `/health` returns HTTP 503 (`degraded`) if the model or database is unavailable, so Docker,
  load balancers and CI all see the instance as not ready.
* **Scaling**: the API is stateless apart from the database, so scale horizontally on CPU or request count.
  p95 latency above 300 ms (warn) / 1000 ms (alert) is a Cloud Engineering signal to scale out.
* **Tracing**: every response carries `X-Request-ID`; the same ID is in the API log line, the
  `api_requests` table and the `model_scores` table.
* **Security (not in scope for this version)**: put the API behind private ingress + IAM / API keys,
  encrypt buckets, restrict `/admin/*`, and keep personal data out of logs.
* **Cost**: one small instance (0.5-1 vCPU, 1 GiB) handles this workload; set `min-instances 1` only in
  production to avoid cold starts.
