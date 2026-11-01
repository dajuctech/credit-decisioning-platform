"""Central configuration.

Every path and tunable value lives here so the same code runs on a laptop,
in Docker, or on a cloud container platform. Values come from environment
variables (or a local `.env` file) and fall back to sensible defaults.

Cloud mapping (see src/cloud/deployment_notes.md):
    DATA_DIR       -> object storage bucket / mounted volume for datasets
    ARTIFACTS_DIR  -> model registry / bucket for model files
    REPORTS_DIR    -> bucket for generated reports
    DB_PATH        -> managed database (PostgreSQL) in production
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = "local"
    log_level: str = "INFO"

    data_dir: Path = PROJECT_ROOT / "data"
    artifacts_dir: Path = PROJECT_ROOT / "artifacts"
    reports_dir: Path = PROJECT_ROOT / "reports"
    db_path: Path | None = None

    model_version: str = "v1.0.0"
    random_seed: int = 42

    # Synthetic data size. Smaller values make tests fast.
    n_borrowers: int = 4000
    n_incoming_applications: int = 600

    api_host: str = "0.0.0.0"
    api_port: int = 8000
    api_url: str = "http://localhost:8000"

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def processed_dir(self) -> Path:
        return self.data_dir / "processed"

    @property
    def monitoring_dir(self) -> Path:
        return self.data_dir / "monitoring"

    @property
    def models_dir(self) -> Path:
        return self.artifacts_dir / "models"

    @property
    def database_path(self) -> Path:
        return self.db_path or (self.data_dir / "platform.db")

    def ensure_dirs(self) -> None:
        for d in (
            self.raw_dir,
            self.processed_dir,
            self.monitoring_dir,
            self.models_dir,
            self.reports_dir / "metrics",
            self.reports_dir / "business",
            self.reports_dir / "figures",
        ):
            d.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_dirs()
    return settings
