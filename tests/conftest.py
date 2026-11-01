"""Shared test fixtures.

`platform_env` builds a small, complete platform (data -> model) in a temporary
folder once per test session, so API and monitoring tests run against a real
trained model without touching the project's own data/ and artifacts/ folders.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from src.config import get_settings


@pytest.fixture(scope="session")
def platform_env(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("platform")
    overrides = {
        "DATA_DIR": str(root / "data"),
        "ARTIFACTS_DIR": str(root / "artifacts"),
        "REPORTS_DIR": str(root / "reports"),
        "N_BORROWERS": "900",
        "N_INCOMING_APPLICATIONS": "150",
        "RANDOM_SEED": "11",
        "DB_PATH": "",
    }
    previous = {k: os.environ.get(k) for k in overrides}
    os.environ.update({k: v for k, v in overrides.items() if v})
    os.environ.pop("DB_PATH", None)
    get_settings.cache_clear()

    from src.run_pipeline import main

    main(["--to", "train"])
    yield root

    for k, v in previous.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    get_settings.cache_clear()


@pytest.fixture()
def client(platform_env):
    from fastapi.testclient import TestClient

    from src.api.main import app

    with TestClient(app) as c:
        yield c
